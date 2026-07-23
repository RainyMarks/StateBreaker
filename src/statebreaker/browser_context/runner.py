"""Run generated browser-context executors through Chrome DevTools Protocol."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from websockets.asyncio.client import connect as _ws_connect

from statebreaker.errors import StateBreakerError

_MAX_WS_MESSAGE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class BrowserContextRunResult:
    """Result returned by a browser-context CDP execution."""

    value: dict[str, Any]
    target_url: str
    screenshot_path: Path | None = None


class _CdpConnection:
    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._next_id = 0

    async def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        command_id = self._next_id
        message: dict[str, Any] = {"id": command_id, "method": method}
        if params:
            message["params"] = params
        await self._websocket.send(json.dumps(message))
        while True:
            raw = await self._websocket.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                response = json.loads(str(raw))
            except json.JSONDecodeError:
                continue
            if not isinstance(response, dict) or response.get("id") != command_id:
                continue
            error = response.get("error")
            if isinstance(error, dict):
                message_text = str(error.get("message") or error)
                raise StateBreakerError(f"CDP command {method} failed: {message_text}")
            return response


def _normalize_cdp_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        raise StateBreakerError("CDP endpoint must not be empty")
    if url.isdigit():
        return f"http://127.0.0.1:{url}"
    if "://" not in url:
        return f"http://{url}"
    return url


async def _list_page_targets(cdp_url: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
        try:
            response = await client.get(f"{cdp_url}/json/list")
        except httpx.HTTPError as exc:
            raise StateBreakerError(
                f"cannot reach Chrome DevTools endpoint {cdp_url}; "
                "start Chrome with --remote-debugging-port or pass --cdp"
            ) from exc
    if response.status_code != 200:
        raise StateBreakerError(
            f"Chrome DevTools endpoint {cdp_url} returned HTTP {response.status_code}"
        )
    try:
        targets = response.json()
    except ValueError as exc:
        raise StateBreakerError(f"Chrome DevTools endpoint {cdp_url} did not return JSON") from exc
    if not isinstance(targets, list):
        raise StateBreakerError(
            f"Chrome DevTools endpoint {cdp_url} returned an invalid target list"
        )
    return [
        target
        for target in targets
        if isinstance(target, dict) and target.get("type") == "page"
    ]


def _url_matches(candidate: str, expected: str) -> bool:
    candidate_text = unquote(candidate)
    expected_text = unquote(expected)
    return expected_text in candidate_text or candidate_text in expected_text


def _select_page_target(targets: list[dict[str, Any]], target_url: str | None) -> dict[str, Any]:
    if not targets:
        raise StateBreakerError("Chrome DevTools has no page targets to execute in")
    pages = [target for target in targets if isinstance(target.get("webSocketDebuggerUrl"), str)]
    if not pages:
        raise StateBreakerError("Chrome DevTools page targets do not expose websocket URLs")
    if target_url:
        for target in pages:
            url = str(target.get("url") or "")
            if _url_matches(url, target_url):
                return target
        visible = ", ".join(str(target.get("url") or "") for target in pages[:5])
        raise StateBreakerError(
            "no Chrome DevTools page target matched the requested URL; "
            f"wanted {target_url!r}; visible pages: {visible}"
        )
    return pages[0]


def _runtime_value(reply: dict[str, Any]) -> dict[str, Any]:
    result = reply.get("result")
    if not isinstance(result, dict):
        raise StateBreakerError("CDP Runtime.evaluate returned no result")
    exception = result.get("exceptionDetails")
    if isinstance(exception, dict):
        text = exception.get("text") or exception.get("exception")
        raise StateBreakerError(f"browser-context executor raised: {text}")
    remote = result.get("result")
    if not isinstance(remote, dict):
        raise StateBreakerError("CDP Runtime.evaluate returned an invalid remote object")
    value = remote.get("value")
    if not isinstance(value, dict):
        returned_type = remote.get("type")
        raise StateBreakerError(
            f"browser-context executor returned {returned_type!r}, not object"
        )
    return value


async def run_browser_context_executor(
    script: str,
    *,
    cdp: str,
    target_url: str | None = None,
    timeout_seconds: float = 90.0,
    screenshot_path: Path | None = None,
) -> BrowserContextRunResult:
    """Execute a generated browser-context script in an existing CDP page."""
    cdp_url = _normalize_cdp_url(cdp)
    target = _select_page_target(await _list_page_targets(cdp_url), target_url)
    ws_url = str(target["webSocketDebuggerUrl"])
    async with _ws_connect(ws_url, max_size=_MAX_WS_MESSAGE_BYTES) as websocket:
        connection = _CdpConnection(websocket)
        await connection.command("Runtime.enable")
        reply = await asyncio.wait_for(
            connection.command(
                "Runtime.evaluate",
                {
                    "expression": script,
                    "awaitPromise": True,
                    "returnByValue": True,
                    "timeout": int(timeout_seconds * 1000),
                },
            ),
            timeout=timeout_seconds + 5,
        )
        value = _runtime_value(reply)
        saved_screenshot = None
        if screenshot_path is not None:
            await connection.command("Page.enable")
            shot = await connection.command(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True},
            )
            data = shot.get("result", {}).get("data")
            if not isinstance(data, str):
                raise StateBreakerError("CDP Page.captureScreenshot returned no image data")
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(base64.b64decode(data))
            saved_screenshot = screenshot_path
    return BrowserContextRunResult(
        value=value,
        target_url=str(target.get("url") or ""),
        screenshot_path=saved_screenshot,
    )
