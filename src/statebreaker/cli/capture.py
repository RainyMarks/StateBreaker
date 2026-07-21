"""`statebreaker capture` commands: import traces from files."""
# ruff: noqa: E501

from __future__ import annotations

import json
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import typer

from statebreaker.capture import (
    load_har,
    load_postman,
    record_browser_trace,
    start_http_proxy_recorder,
)
from statebreaker.capture.proxy import is_loopback_listen_host
from statebreaker.cli.common import fail, load_config, open_store
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi
from statebreaker.models.capture import CapturedTrace, HttpExchange

app = typer.Typer(
    help=bi("录制或导入一段正常流程流量。", "Capture or import normal-flow traffic.")
)


_FILE_ARG = typer.Argument(
    ...,
    help=bi("HAR 文件或 Postman collection。", "HAR file or Postman collection"),
)


def save_capture_trace(project: str, trace: CapturedTrace) -> None:
    """Persist a captured trace under one project."""
    store = open_store(project)
    try:
        store.save("captures", trace.capture_id, trace)
    finally:
        store.close()


def import_capture_file(
    file: Path,
    project: str,
    *,
    capture_id: str | None = None,
) -> CapturedTrace:
    """Import one supported capture file and persist the normalized trace."""
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateBreakerError(
            bi(f"无法读取 {file}: {exc}", f"cannot read {file}: {exc}")
        ) from exc
    if not isinstance(data, dict):
        raise StateBreakerError(
            bi(
                f"无法识别的 capture 格式：{file}（期望 JSON object）",
                f"unrecognized capture format: {file} (expected a JSON object)",
            )
        )

    if "log" in data:
        trace = load_har(file, capture_id=capture_id, project=project)
    elif "item" in data:
        trace = load_postman(file, capture_id=capture_id, project=project)
    else:
        raise StateBreakerError(
            bi(
                f"无法识别的 capture 格式：{file}（期望 HAR 或 Postman collection）",
                f"unrecognized capture format: {file} (expected HAR or Postman collection)",
            )
        )
    if capture_id:
        trace = trace.model_copy(update={"capture_id": capture_id})

    save_capture_trace(project, trace)
    return trace


def record_proxy_capture(
    project: str,
    *,
    capture_id: str | None = None,
    listen_host: str = "127.0.0.1",
    listen_port: int = 8088,
    max_exchanges: int | None = None,
    allow_public_proxy: bool = False,
) -> CapturedTrace:
    """Record a normal flow through the local HTTP proxy."""
    selected_capture_id = capture_id or _default_proxy_capture_id()
    try:
        return anyio.run(
            _record_proxy_capture_async,
            project,
            selected_capture_id,
            listen_host,
            listen_port,
            max_exchanges,
            allow_public_proxy,
        )
    except OSError as exc:
        raise StateBreakerError(
            bi(f"无法启动本地代理：{exc}", f"cannot start local proxy: {exc}")
        ) from exc
    except ValueError as exc:
        raise StateBreakerError(str(exc)) from exc


def record_browser_capture(
    project: str,
    *,
    capture_id: str | None = None,
    start_url: str | None = None,
    browser_path: str | None = None,
    max_exchanges: int | None = None,
) -> CapturedTrace:
    """Record a normal flow from a spawned Chromium-family browser."""
    selected_capture_id = capture_id or _default_browser_capture_id()
    try:
        return anyio.run(
            _record_browser_capture_async,
            project,
            selected_capture_id,
            start_url,
            browser_path,
            max_exchanges,
        )
    except StateBreakerError:
        raise
    except Exception as exc:
        raise StateBreakerError(str(exc)) from exc


async def _record_proxy_capture_async(
    project: str,
    capture_id: str,
    listen_host: str,
    listen_port: int,
    max_exchanges: int | None,
    allow_public_proxy: bool,
) -> CapturedTrace:
    recorder = await start_http_proxy_recorder(
        capture_id=capture_id,
        project=project,
        listen_host=listen_host,
        listen_port=listen_port,
        allow_public_bind=allow_public_proxy,
    )
    _print_proxy_setup_instructions(
        recorder.bound_host,
        recorder.bound_port,
        public_bind=allow_public_proxy,
    )
    try:
        if max_exchanges is None:
            await anyio.to_thread.run_sync(_wait_for_enter)
        else:
            typer.echo(
                f"Recording will stop after {max_exchanges} exchange(s).  "
                f"({bi('达到数量后自动停止', 'auto-stop after this many exchanges')})"
            )
            await recorder.wait_for_exchanges(max_exchanges)
    finally:
        trace = await recorder.stop()
    return trace


async def _record_browser_capture_async(
    project: str,
    capture_id: str,
    start_url: str | None,
    browser_path: str | None,
    max_exchanges: int | None,
) -> CapturedTrace:
    _print_browser_setup_instructions(start_url, max_exchanges=max_exchanges)
    return await record_browser_trace(
        capture_id=capture_id,
        project=project,
        start_url=start_url,
        browser_path=browser_path,
        max_exchanges=max_exchanges,
        on_exchange=_print_browser_exchange,
        stop_signal=None if max_exchanges is not None else _wait_for_enter,
    )


def _default_proxy_capture_id() -> str:
    return "proxy-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _default_browser_capture_id() -> str:
    return "browser-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _wait_for_enter() -> None:
    with suppress(EOFError):
        input(bi("按 Enter 停止录制...", "Press Enter to stop recording..."))


def _print_proxy_setup_instructions(host: str, port: int, *, public_bind: bool) -> None:
    endpoint = _format_host_port(host, port)
    typer.echo(
        f"HTTP proxy listening on {endpoint}  "
        f"({bi('本地 HTTP 代理已启动', 'local HTTP proxy is ready')})"
    )
    if public_bind:
        typer.echo(
            "WARNING: public proxy binding is enabled; use only on a trusted network "
            "and only for authorized traffic.  "
            f"({bi('警告：仅限受信网络和授权流量', 'trusted networks only')})"
        )
    else:
        typer.echo(
            "Safety: proxy is loopback-only; StateBreaker will not change OS or browser "
            "proxy settings.  "
            f"({bi('安全提示：只监听本机，不自动改代理', 'local-only; no proxy changes')})"
        )
    typer.echo(
        f"Set your browser/client HTTP proxy to host {host!r}, port {port}.  "
        f"({bi('把浏览器或客户端代理临时指向这里', 'temporarily point your browser/client here')})"
    )
    typer.echo(
        "Run the authorized normal flow, then press Enter to stop recording.  "
        f"({bi('只操作一遍授权的正常流程，然后回到终端停止', 'record one normal flow, then stop')})"
    )
    typer.echo(
        "Before pressing Enter, switch the browser proxy back to direct/system.  "
        f"({bi('按 Enter 前先把浏览器代理切回直接连接', 'turn browser proxy off before Enter')})"
    )
    typer.echo(
        "HTTPS CONNECT is tunneled for browser assets but not recorded; "
        "only HTTP exchanges are saved. Import HAR/Postman for HTTPS capture.  "
        f"({bi('HTTPS 只转发不录制；要录 HTTPS 请导入 HAR/Postman', 'tunnel HTTPS; import HAR for TLS capture')})"
    )
    typer.echo(
        "Live capture lines look like: [proxy] #1 GET http://... -> 200  "
        f"({bi('有流量时终端会实时打印 [proxy] 行；没有就是没进代理', 'no [proxy] lines means traffic bypassed the proxy')})"
    )
    typer.echo(
        "Open the target over HTTP, then execute the normal-flow API calls in the browser.  "
        f"({bi('用浏览器打开 HTTP 目标并真正发出业务请求', 'issue real API requests through the proxy')})"
    )


def _print_browser_setup_instructions(
    start_url: str | None,
    *,
    max_exchanges: int | None,
) -> None:
    typer.echo(
        "StateBreaker will open a clean Chromium/Edge window with a temporary profile.  "
        "(temporary browser profile)"
    )
    if start_url:
        typer.echo(f"Start URL: {start_url}")
    typer.echo(
        "Run one authorized normal flow in that window, then return here and press Enter.  "
        "(one authorized normal flow)"
    )
    if max_exchanges is not None:
        typer.echo(
            f"Recording will stop after {max_exchanges} exchange(s).  "
            "(auto-stop after count)"
        )
    typer.echo(
        "HTTPS is captured through the browser DevTools Protocol; no proxy or "
        "certificate installation is required."
    )


def _print_browser_exchange(exchange: HttpExchange) -> None:
    typer.echo(
        f"[browser] {exchange.exchange_id} "
        f"{exchange.method} {exchange.url} -> {exchange.response_status}"
    )


def print_proxy_capture_summary(
    trace: CapturedTrace,
    *,
    leading_verb: str = "recorded",
) -> None:
    """Print a short post-recording summary for proxy captures."""
    for line in format_proxy_capture_summary(trace, leading_verb=leading_verb):
        typer.echo(line)


def format_proxy_capture_summary(
    trace: CapturedTrace,
    *,
    leading_verb: str = "recorded",
) -> list[str]:
    """Build a concise summary of the captured proxy traffic."""
    exchange_count = len(trace.exchanges)
    lines = [
        f"{leading_verb} {exchange_count} exchanges as capture {trace.capture_id!r}.",
        f"Recording stopped: captured {exchange_count} HTTP exchange(s).",
    ]
    if exchange_count == 0:
        lines.append(
            "No HTTP traffic passed through the proxy; discovery may need a new capture.  "
            f"({bi('代理没有录到 HTTP 流量，可能需要重新录制', 'record a new capture if needed')})"
        )
        return lines

    methods: Counter[str] = Counter(exchange.method for exchange in trace.exchanges)
    statuses: Counter[str] = Counter(
        _status_bucket(exchange.response_status) for exchange in trace.exchanges
    )
    hosts = sorted(
        {
            parsed.netloc
            for exchange in trace.exchanges
            if (parsed := urlsplit(exchange.url)).netloc
        }
    )
    lines.append(f"Methods: {_format_counter(methods)}  ({bi('HTTP 方法分布', 'HTTP methods')})")
    lines.append(
        f"Statuses: {_format_counter(statuses)}  ({bi('响应状态分布', 'response status groups')})"
    )
    if hosts:
        lines.append(f"Hosts: {', '.join(hosts[:5])}  ({bi('目标主机', 'target hosts')})")
    return lines


def _format_host_port(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _format_counter(counter: Counter[str]) -> str:
    return ", ".join(f"{key}={counter[key]}" for key in sorted(counter))


def _status_bucket(status: int) -> str:
    if status <= 0:
        return "unknown"
    return f"{status // 100}xx"


def _is_loopback_listen_host(host: str) -> bool:
    return is_loopback_listen_host(host)


def _validate_proxy_listen_host(host: str, *, allow_public_proxy: bool) -> None:
    if allow_public_proxy or is_loopback_listen_host(host):
        return
    raise StateBreakerError(
        bi(
            f"拒绝把未认证代理监听到非本机地址 {host!r}；"
            "只有受信网络才可使用 --unsafe-public-proxy",
            "refusing to bind unauthenticated proxy to non-loopback host "
            f"{host!r}; use --unsafe-public-proxy only on trusted networks",
        )
    )


def _default_browser_start_url(project: str, url: str | None) -> str | None:
    if url is not None:
        return url
    return load_config(project).project.base_url


@app.command(
    "import",
    help=bi(
        "把 HAR/Postman 正常流导入为 capture。",
        "Import a HAR/Postman normal flow as a capture.",
    ),
)
def import_capture(
    file: Path = _FILE_ARG,
    project: str = typer.Option(..., "--project", "-p"),
    capture_id: str | None = typer.Option(None, "--capture-id"),
) -> None:
    """Import a HAR/Postman capture as the normal-flow trace."""
    try:
        trace = import_capture_file(file, project, capture_id=capture_id)
        typer.echo(
            f"imported {len(trace.exchanges)} exchanges as capture {trace.capture_id!r}  "
            f"({bi('已导入正常流程流量', 'normal-flow traffic imported')})"
        )
    except StateBreakerError as exc:
        fail(exc)


@app.command(
    "proxy",
    help=bi(
        "启动本地 HTTP 代理录制正常流程。",
        "Record a normal flow through a local HTTP proxy.",
    ),
)
def proxy_capture(
    project: str = typer.Option(..., "--project", "-p"),
    capture_id: str | None = typer.Option(None, "--capture-id"),
    listen_host: str = typer.Option("127.0.0.1", "--listen-host"),
    listen_port: int = typer.Option(8088, "--listen-port"),
    unsafe_public_proxy: bool = typer.Option(
        False,
        "--unsafe-public-proxy",
        help=(
            bi(
                "允许未认证 HTTP 代理监听非本机地址；只应在受信网络短时使用。",
                "Allow binding the unauthenticated HTTP proxy to a non-loopback address. "
                "Only use on trusted networks.",
            )
        ),
    ),
    max_exchanges: int | None = typer.Option(
        None,
        "--max-exchanges",
        help=bi(
            "录到指定数量的 HTTP exchange 后自动停止。",
            "Stop automatically after recording this many HTTP exchanges.",
        ),
    ),
) -> None:
    """Record a normal HTTP flow through a local forward proxy."""
    try:
        _validate_proxy_listen_host(
            listen_host,
            allow_public_proxy=unsafe_public_proxy,
        )
        trace = record_proxy_capture(
            project,
            capture_id=capture_id,
            listen_host=listen_host,
            listen_port=listen_port,
            max_exchanges=max_exchanges,
            allow_public_proxy=unsafe_public_proxy,
        )
        save_capture_trace(project, trace)
        print_proxy_capture_summary(trace)
    except StateBreakerError as exc:
        fail(exc)


@app.command(
    "browser",
    help="Open a browser and record a normal HTTPS flow through CDP.",
)
def browser_capture(
    project: str = typer.Option(..., "--project", "-p"),
    capture_id: str | None = typer.Option(None, "--capture-id"),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Initial page to open; defaults to the project Base URL.",
    ),
    browser_path: str | None = typer.Option(
        None,
        "--browser-path",
        help="Chrome/Edge executable path; STATEBREAKER_BROWSER is also supported.",
    ),
    max_exchanges: int | None = typer.Option(
        None,
        "--max-exchanges",
        help="Stop automatically after recording this many HTTP exchanges.",
    ),
) -> None:
    """Record a normal HTTPS-capable browser flow through CDP."""
    try:
        trace = record_browser_capture(
            project,
            capture_id=capture_id,
            start_url=_default_browser_start_url(project, url),
            browser_path=browser_path,
            max_exchanges=max_exchanges,
        )
        save_capture_trace(project, trace)
        print_proxy_capture_summary(trace)
    except StateBreakerError as exc:
        fail(exc)
