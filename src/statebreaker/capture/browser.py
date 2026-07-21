"""Browser recorder: drive a local Chromium browser over CDP to capture HTTPS traffic.

Unlike the HTTP proxy recorder, the browser itself performs TLS, so HTTPS
exchanges are recorded in cleartext without certificates or proxy settings.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit

import httpx
from websockets.asyncio.client import connect as _ws_connect

from statebreaker.errors import CaptureError
from statebreaker.models.capture import BodyEncoding, CapturedTrace, HttpExchange

_MAX_WS_MESSAGE_BYTES = 64 * 1024 * 1024
_BROWSER_START_TIMEOUT_SECONDS = 15.0
_NAVIGATE_TIMEOUT_SECONDS = 10.0


def find_browser_executable(browser_path: str | None = None) -> str:
    """Locate a Chromium-family browser executable, or raise ``CaptureError``."""
    candidates: list[str] = []
    if browser_path:
        candidates.append(browser_path)
    env_path = os.environ.get("STATEBREAKER_BROWSER")
    if env_path:
        candidates.append(env_path)
    if sys.platform.startswith("win"):
        for root_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(root_var)
            if not root:
                continue
            candidates.extend(
                [
                    str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                    str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                ]
            )
    for name in ("msedge", "chrome", "chromium", "google-chrome", "google-chrome-stable"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise CaptureError(
        "no Chromium-family browser found; install Chrome/Edge or pass "
        "--browser-path / set STATEBREAKER_BROWSER"
    )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _BrowserProcess(Protocol):
    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class _CdpTransport(Protocol):
    """Minimal websocket-like transport used by the CDP connection."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


class _WebSocketTransport:
    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send(self, message: str) -> None:
        await self._ws.send(message)

    async def recv(self) -> str:
        data = await self._ws.recv()
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    async def close(self) -> None:
        await self._ws.close()


def _launch_browser_process(
    executable: str,
    port: int,
    profile_dir: str,
    start_url: str | None,
) -> _BrowserProcess:
    return subprocess.Popen(  # noqa: S603
        [
            executable,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            start_url or "about:blank",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _fetch_debugger_url(port: int) -> str:
    """Poll the browser's /json/version endpoint until it answers."""
    deadline = time.monotonic() + _BROWSER_START_TIMEOUT_SECONDS
    async with httpx.AsyncClient(trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(
                    f"http://127.0.0.1:{port}/json/version", timeout=1.0
                )
                if response.status_code == 200:
                    ws_url = response.json().get("webSocketDebuggerUrl")
                    if isinstance(ws_url, str) and ws_url:
                        return ws_url
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(0.2)
    raise CaptureError(
        "browser did not expose a DevTools endpoint in time; "
        "it may have failed to start"
    )


@dataclass
class _PendingExchange:
    request_id: str
    method: str
    url: str
    request_headers: dict[str, str]
    post_data: str | None
    wall_time: float
    mono_time: float
    status: int = 0
    response_headers: dict[str, str] = field(default_factory=dict)
    mime_type: str = ""
    finish_mono_time: float | None = None


class ExchangeTracker:
    """Fold CDP Network events into normalized ``HttpExchange`` records."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingExchange] = {}
        self.exchanges: list[HttpExchange] = []
        self._counter = 0

    def request_will_be_sent(self, params: dict[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        if not request_id:
            return
        redirect = params.get("redirectResponse")
        previous = self._pending.pop(request_id, None)
        if previous is not None and isinstance(redirect, dict):
            self._finalize_redirect(previous, redirect)
        request = params.get("request")
        if not isinstance(request, dict):
            return
        url = str(request.get("url", ""))
        if not url.startswith(("http://", "https://")):
            return
        wall_time = params.get("wallTime")
        mono_time = params.get("timestamp")
        post_data = request.get("postData")
        self._pending[request_id] = _PendingExchange(
            request_id=request_id,
            method=str(request.get("method", "GET")).upper(),
            url=url,
            request_headers=_headers_to_dict(request.get("headers")),
            post_data=post_data if isinstance(post_data, str) else None,
            wall_time=float(wall_time) if isinstance(wall_time, (int, float)) else 0.0,
            mono_time=float(mono_time) if isinstance(mono_time, (int, float)) else 0.0,
        )

    def response_received(self, params: dict[str, Any]) -> None:
        pending = self._pending.get(str(params.get("requestId", "")))
        if pending is None:
            return
        response = params.get("response")
        if not isinstance(response, dict):
            return
        status = response.get("status")
        pending.status = int(status) if isinstance(status, (int, float)) else 0
        pending.response_headers = _headers_to_dict(response.get("headers"))
        mime = response.get("mimeType")
        pending.mime_type = str(mime) if isinstance(mime, str) else ""

    def loading_finished(
        self,
        params: dict[str, Any],
        *,
        body: str | None,
        base64_encoded: bool,
    ) -> HttpExchange | None:
        pending = self._pending.pop(str(params.get("requestId", "")), None)
        if pending is None:
            return None
        timestamp = params.get("timestamp")
        if isinstance(timestamp, (int, float)):
            pending.finish_mono_time = float(timestamp)
        return self._finalize(pending, body=body, base64_encoded=base64_encoded)

    def loading_failed(self, params: dict[str, Any]) -> HttpExchange | None:
        pending = self._pending.pop(str(params.get("requestId", "")), None)
        if pending is None:
            return None
        timestamp = params.get("timestamp")
        if isinstance(timestamp, (int, float)):
            pending.finish_mono_time = float(timestamp)
        return self._finalize(pending, body=None, base64_encoded=False)

    def finish_all_pending(self) -> None:
        """Close out exchanges still in flight when recording stops."""
        for pending in list(self._pending.values()):
            self._finalize(pending, body=None, base64_encoded=False)
        self._pending.clear()

    def _finalize_redirect(
        self, pending: _PendingExchange, redirect: dict[str, Any]
    ) -> None:
        status = redirect.get("status")
        pending.status = int(status) if isinstance(status, (int, float)) else 0
        pending.response_headers = _headers_to_dict(redirect.get("headers"))
        mime = redirect.get("mimeType")
        pending.mime_type = str(mime) if isinstance(mime, str) else ""
        self._finalize(pending, body=None, base64_encoded=False)

    def _finalize(
        self,
        pending: _PendingExchange,
        *,
        body: str | None,
        base64_encoded: bool,
    ) -> HttpExchange:
        self._counter += 1
        started_at_ns = int(pending.wall_time * 1_000_000_000)
        completed_at_ns = started_at_ns
        if pending.finish_mono_time is not None and pending.mono_time:
            delta_ns = int((pending.finish_mono_time - pending.mono_time) * 1_000_000_000)
            completed_at_ns = started_at_ns + max(0, delta_ns)
        request_body, request_encoding = _decode_request_body(
            pending.post_data, pending.request_headers
        )
        response_body, response_encoding = _decode_response_body(
            body, base64_encoded, pending.mime_type
        )
        exchange = HttpExchange(
            exchange_id=f"browser-{self._counter}",
            method=pending.method,
            url=pending.url,
            request_headers=pending.request_headers,
            request_body=request_body,
            request_body_encoding=request_encoding,
            response_status=pending.status,
            response_headers=pending.response_headers,
            response_body=response_body,
            response_body_encoding=response_encoding,
            started_at_ns=started_at_ns,
            completed_at_ns=completed_at_ns,
        )
        self.exchanges.append(exchange)
        return exchange


class _CdpConnection:
    """JSON-RPC framing over one CDP websocket: commands by id, events by method."""

    def __init__(
        self,
        transport: _CdpTransport,
        event_handler: Callable[[dict[str, Any]], None],
    ) -> None:
        self._transport = transport
        self._event_handler = event_handler
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None

    def start_reader(self) -> None:
        self._reader_task = asyncio.create_task(self._read_loop())

    async def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        command_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[command_id] = future
        message: dict[str, Any] = {"id": command_id, "method": method}
        if params:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        await self._transport.send(json.dumps(message))
        return await future

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        with suppress(Exception):
            await self._transport.close()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def _read_loop(self) -> None:
        while True:
            raw = await self._transport.recv()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            message_id = message.get("id")
            if isinstance(message_id, int):
                future = self._pending.pop(message_id, None)
                if future is not None and not future.done():
                    future.set_result(message)
            else:
                self._event_handler(message)


class BrowserRecorder:
    """Record a normal flow by driving a local browser through DevTools Protocol."""

    def __init__(
        self,
        *,
        capture_id: str,
        project: str = "default",
        start_url: str | None = None,
        browser_path: str | None = None,
        on_exchange: Callable[[HttpExchange], None] | None = None,
        transport: _CdpTransport | None = None,
        process_launcher: Callable[
            [str, int, str, str | None], _BrowserProcess
        ] = _launch_browser_process,
        debugger_url_fetcher: Callable[[int], Any] = _fetch_debugger_url,
    ) -> None:
        self.capture_id = capture_id
        self.project = project
        self.start_url = start_url
        self.browser_path = browser_path
        self.on_exchange = on_exchange
        self._transport = transport
        self._process_launcher = process_launcher
        self._debugger_url_fetcher = debugger_url_fetcher
        self._tracker = ExchangeTracker()
        self._connection: _CdpConnection | None = None
        self._session_id: str | None = None
        self._process: _BrowserProcess | None = None
        self._profile_dir: str | None = None
        self._exchange_event = asyncio.Event()

    async def start(self) -> None:
        executable = find_browser_executable(self.browser_path)
        port = _free_loopback_port()
        self._profile_dir = tempfile.mkdtemp(prefix="statebreaker-browser-")
        try:
            self._process = self._process_launcher(
                executable, port, self._profile_dir, self.start_url
            )
            ws_url = await self._debugger_url_fetcher(port)
            transport = self._transport
            if transport is None:
                ws = await _ws_connect(ws_url, max_size=_MAX_WS_MESSAGE_BYTES)
                transport = _WebSocketTransport(ws)
            self._connection = _CdpConnection(transport, self._handle_event)
            self._connection.start_reader()
            if self.start_url:
                # The process already opened start_url; attach to that page target.
                target_id = await self._attach_existing_page()
            else:
                target = await self._connection.command(
                    "Target.createTarget", {"url": "about:blank"}
                )
                target_id = str(target.get("result", {}).get("targetId", ""))
            attached = await self._connection.command(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            self._session_id = str(attached.get("result", {}).get("sessionId", ""))
            await self._connection.command(
                "Network.enable", session_id=self._session_id
            )
        except BaseException:
            await self._cleanup()
            raise

    async def _attach_existing_page(self) -> str:
        """Attach to the already-open page instead of creating a new tab."""
        assert self._connection is not None  # noqa: S101
        targets = await self._connection.command("Target.getTargets")
        for info in targets.get("result", {}).get("targetInfos", []):
            if not isinstance(info, dict):
                continue
            if info.get("type") == "page":
                return str(info.get("targetId", ""))
        created = await self._connection.command(
            "Target.createTarget", {"url": self.start_url or "about:blank"}
        )
        return str(created.get("result", {}).get("targetId", ""))

    async def stop(self) -> CapturedTrace:
        await self._cleanup()
        self._tracker.finish_all_pending()
        exchanges = sorted(self._tracker.exchanges, key=lambda ex: ex.started_at_ns)
        return CapturedTrace(
            capture_id=self.capture_id,
            source="browser",
            project=self.project,
            base_url=_origin_of(self.start_url),
            sessions=[],
            exchanges=exchanges,
        )

    async def wait_for_exchanges(self, count: int) -> None:
        while len(self._tracker.exchanges) < count:
            await self._exchange_event.wait()
            self._exchange_event.clear()

    def _handle_event(self, message: dict[str, Any]) -> None:
        session_id = message.get("sessionId")
        if session_id is not None and session_id != self._session_id:
            return
        method = message.get("method")
        params = message.get("params")
        if not isinstance(params, dict):
            return
        if method == "Network.requestWillBeSent":
            self._tracker.request_will_be_sent(params)
        elif method == "Network.responseReceived":
            self._tracker.response_received(params)
        elif method == "Network.loadingFinished":
            asyncio.create_task(self._finish_with_body(params))
        elif method == "Network.loadingFailed":
            self._emit(self._tracker.loading_failed(params))

    async def _finish_with_body(self, params: dict[str, Any]) -> None:
        body: str | None = None
        base64_encoded = False
        connection = self._connection
        request_id = params.get("requestId")
        if connection is not None and request_id:
            try:
                reply = await asyncio.wait_for(
                    connection.command(
                        "Network.getResponseBody",
                        {"requestId": str(request_id)},
                        session_id=self._session_id,
                    ),
                    timeout=_NAVIGATE_TIMEOUT_SECONDS,
                )
            except (TimeoutError, asyncio.CancelledError, RuntimeError):
                reply = {}
            result = reply.get("result")
            if isinstance(result, dict):
                raw_body = result.get("body")
                if isinstance(raw_body, str):
                    body = raw_body
                    base64_encoded = bool(result.get("base64Encoded"))
        self._emit(
            self._tracker.loading_finished(
                params, body=body, base64_encoded=base64_encoded
            )
        )

    def _emit(self, exchange: HttpExchange | None) -> None:
        if exchange is None:
            return
        if self.on_exchange is not None:
            self.on_exchange(exchange)
        self._exchange_event.set()

    async def _cleanup(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
        if self._process is not None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self._process.wait), timeout=5.0
                    )
                except TimeoutError:
                    self._process.kill()
                    await asyncio.to_thread(self._process.wait)
            except Exception:  # noqa: BLE001 - best-effort shutdown
                pass
            self._process = None
        if self._profile_dir is not None:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None


async def record_browser_trace(
    *,
    capture_id: str,
    project: str = "default",
    start_url: str | None = None,
    browser_path: str | None = None,
    max_exchanges: int | None = None,
    on_exchange: Callable[[HttpExchange], None] | None = None,
    stop_signal: Callable[[], Any] | None = None,
) -> CapturedTrace:
    """Record one normal flow in a spawned browser window into a ``CapturedTrace``.

    Recording stops when ``stop_signal`` returns (typically waiting for Enter),
    or after ``max_exchanges`` exchanges when that limit is given.
    """
    recorder = BrowserRecorder(
        capture_id=capture_id,
        project=project,
        start_url=start_url,
        browser_path=browser_path,
        on_exchange=on_exchange,
    )
    await recorder.start()
    try:
        if max_exchanges is not None:
            await recorder.wait_for_exchanges(max_exchanges)
        elif stop_signal is not None:
            await asyncio.to_thread(stop_signal)
        else:
            await asyncio.sleep(0)
    finally:
        trace = await recorder.stop()
    return trace


def _headers_to_dict(headers: Any) -> dict[str, str]:
    """Normalize a CDP header object; lowercase keys, later duplicates win."""
    result: dict[str, str] = {}
    if not isinstance(headers, dict):
        return result
    for name, value in headers.items():
        result[str(name).lower()] = "" if value is None else str(value)
    return result


def _decode_request_body(
    post_data: str | None, headers: dict[str, str]
) -> tuple[Any | None, BodyEncoding]:
    if post_data is None:
        return None, "none"
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()
    if "json" in content_type:
        try:
            return json.loads(post_data), "json"
        except json.JSONDecodeError:
            return post_data, "raw"
    if content_type == "application/x-www-form-urlencoded":
        return dict(parse_qsl(post_data, keep_blank_values=True)), "form"
    return post_data, "raw"


def _decode_response_body(
    text: str | None, base64_encoded: bool, mime_type: str
) -> tuple[Any | None, BodyEncoding]:
    if text is None:
        return None, "none"
    if base64_encoded:
        try:
            text = base64.b64decode(text).decode("utf-8", errors="replace")
        except ValueError:
            return text, "raw"
    if "json" in mime_type.lower():
        try:
            return json.loads(text), "json"
        except json.JSONDecodeError:
            pass
    return text, "raw"


def _origin_of(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"
