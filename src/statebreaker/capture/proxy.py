"""Local HTTP forward proxy recorder for normal-flow capture."""

from __future__ import annotations

import asyncio
import gzip
import json
import time
import zlib
from contextlib import suppress
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from statebreaker.models.capture import BodyEncoding, CapturedTrace, HttpExchange

_MAX_HEADER_BYTES = 64 * 1024
_MAX_BODY_BYTES = 10 * 1024 * 1024
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass
class _HttpMessage:
    first_line: str
    headers: dict[str, str]
    raw_headers: list[tuple[str, str]]
    body: bytes


@dataclass
class HttpProxyRecorder:
    """Running HTTP proxy that records proxied requests as a ``CapturedTrace``."""

    capture_id: str
    project: str = "default"
    listen_host: str = "127.0.0.1"
    listen_port: int = 8088
    allow_public_bind: bool = False
    _server: asyncio.Server | None = None
    _exchanges: list[HttpExchange] = field(default_factory=list)
    _exchange_event: asyncio.Event = field(default_factory=asyncio.Event)
    _client_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    _connect_tunnels: int = 0

    @property
    def bound_host(self) -> str:
        return self.listen_host

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self.listen_port
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if not self.allow_public_bind and not is_loopback_listen_host(self.listen_host):
            raise ValueError(
                "refusing to bind unauthenticated proxy to non-loopback host "
                f"{self.listen_host!r}"
            )
        self._server = await asyncio.start_server(
            self._handle_client,
            self.listen_host,
            self.listen_port,
        )

    async def stop(self) -> CapturedTrace:
        pending = [task for task in self._client_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._client_tasks.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        return CapturedTrace(
            capture_id=self.capture_id,
            source="proxy",
            project=self.project,
            sessions=[],
            exchanges=list(self._exchanges),
        )

    async def wait_for_exchanges(self, count: int) -> None:
        while len(self._exchanges) < count:
            await self._exchange_event.wait()
            self._exchange_event.clear()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
            task.add_done_callback(self._client_tasks.discard)
        try:
            request = await _read_http_message(reader)
            if request is None:
                return
            method, target, version = _parse_request_line(request.first_line)
            if method == "CONNECT":
                await self._tunnel_connect(target, reader, writer)
                return
            await self._forward_and_record(method, target, version, request, writer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with suppress(Exception):
                await _write_error(writer, 502, f"proxy error: {exc}")
        finally:
            with suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _tunnel_connect(
        self,
        target: str,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Blind-forward HTTPS CONNECT so browsers can load CDN assets.

        TLS bytes are not decrypted or recorded; import HAR/Postman for HTTPS capture.
        """
        host, port = _parse_connect_target(target)
        try:
            origin_reader, origin_writer = await asyncio.open_connection(host, port)
        except Exception as exc:
            await _write_error(client_writer, 502, f"CONNECT upstream error: {exc}")
            return

        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        self._connect_tunnels += 1
        if self._connect_tunnels <= 3:
            print(
                f"[proxy] CONNECT tunnel (HTTPS not recorded): {host}:{port}",
                flush=True,
            )
        elif self._connect_tunnels == 4:
            print(
                "[proxy] further CONNECT tunnels suppressed in log",
                flush=True,
            )

        await asyncio.gather(
            _pipe_bytes(client_reader, origin_writer),
            _pipe_bytes(origin_reader, client_writer),
            return_exceptions=True,
        )

    async def _forward_and_record(
        self,
        method: str,
        target: str,
        version: str,
        request: _HttpMessage,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        started_at_ns = time.time_ns()
        url, host, port, origin_target = _resolve_target(target, request.headers)
        response_status = 502
        response_headers: dict[str, str] = {"content-type": "text/plain"}
        response_body = b""
        try:
            origin_reader, origin_writer = await asyncio.open_connection(host, port)
            try:
                origin_writer.write(
                    _build_origin_request(method, origin_target, version, request)
                )
                await origin_writer.drain()
                response = await _read_http_message(origin_reader, read_until_eof=True)
                if response is None:
                    raise ValueError("origin closed before sending a response")
                response_status = _parse_status(response.first_line)
                response_headers = _captured_headers(response)
                response_body = response.body
                client_writer.write(_serialize_message(response))
                await client_writer.drain()
            finally:
                origin_writer.close()
                await origin_writer.wait_closed()
        except Exception as exc:
            message = f"proxy upstream error: {exc}".encode()
            response_body = message
            response_headers = {"content-type": "text/plain", "content-length": str(len(message))}
            client_writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                + _headers_to_bytes(response_headers)
                + b"\r\n"
                + message
            )
            await client_writer.drain()

        completed_at_ns = time.time_ns()
        request_body, request_encoding = _decode_body(request.headers, request.body)
        parsed_response_body, response_encoding = _decode_body(response_headers, response_body)
        exchange_id = f"proxy-{len(self._exchanges) + 1}"
        self._exchanges.append(
            HttpExchange(
                exchange_id=exchange_id,
                method=method,
                url=url,
                request_headers={
                    key: value
                    for key, value in request.headers.items()
                    if key not in _HOP_BY_HOP_HEADERS
                },
                request_body=request_body,
                request_body_encoding=request_encoding,
                response_status=response_status,
                response_headers=response_headers,
                response_body=parsed_response_body,
                response_body_encoding=response_encoding,
                started_at_ns=started_at_ns,
                completed_at_ns=completed_at_ns,
            )
        )
        print(
            f"[proxy] #{len(self._exchanges)} {method} {url} -> {response_status}",
            flush=True,
        )
        self._exchange_event.set()


async def start_http_proxy_recorder(
    *,
    capture_id: str,
    project: str = "default",
    listen_host: str = "127.0.0.1",
    listen_port: int = 8088,
    allow_public_bind: bool = False,
) -> HttpProxyRecorder:
    """Start a local HTTP proxy recorder."""
    recorder = HttpProxyRecorder(
        capture_id=capture_id,
        project=project,
        listen_host=listen_host,
        listen_port=listen_port,
        allow_public_bind=allow_public_bind,
    )
    await recorder.start()
    return recorder


def is_loopback_listen_host(host: str) -> bool:
    """Return whether a listen host keeps the unauthenticated proxy local-only."""
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


async def _read_http_message(
    reader: asyncio.StreamReader,
    *,
    read_until_eof: bool = False,
) -> _HttpMessage | None:
    first = await reader.readline()
    if not first:
        return None
    first_line = first.decode("iso-8859-1").strip()
    raw_headers: list[tuple[str, str]] = []
    header_bytes = len(first)
    while True:
        line = await reader.readline()
        if not line:
            break
        header_bytes += len(line)
        if header_bytes > _MAX_HEADER_BYTES:
            raise ValueError("HTTP headers are too large")
        if line in {b"\r\n", b"\n"}:
            break
        name, _, value = line.decode("iso-8859-1").partition(":")
        if not value and not _:
            continue
        raw_headers.append((name.strip(), value.strip()))
    headers = {name.lower(): value for name, value in raw_headers}
    if _is_chunked(headers):
        body = await _read_chunked_body(reader)
    else:
        content_length = int(headers.get("content-length", "0") or "0")
        if content_length > _MAX_BODY_BYTES:
            raise ValueError("HTTP body is too large to capture")
        if content_length:
            body = await reader.readexactly(content_length)
        elif read_until_eof:
            body = await reader.read(_MAX_BODY_BYTES + 1)
            if len(body) > _MAX_BODY_BYTES:
                raise ValueError("HTTP body is too large to capture")
        else:
            body = b""
    return _HttpMessage(first_line=first_line, headers=headers, raw_headers=raw_headers, body=body)


async def _read_chunked_body(reader: asyncio.StreamReader) -> bytes:
    body = bytearray()
    while True:
        line = await reader.readline()
        if not line:
            raise ValueError("chunked body ended before final chunk")
        raw_size = line.decode("iso-8859-1").split(";", 1)[0].strip()
        try:
            size = int(raw_size, 16)
        except ValueError as exc:
            raise ValueError(f"invalid chunk size: {raw_size!r}") from exc
        if size == 0:
            await _discard_trailers(reader)
            break
        if len(body) + size > _MAX_BODY_BYTES:
            raise ValueError("HTTP body is too large to capture")
        body.extend(await reader.readexactly(size))
        line_end = await reader.readexactly(2)
        if line_end != b"\r\n":
            raise ValueError("invalid chunk terminator")
    return bytes(body)


async def _discard_trailers(reader: asyncio.StreamReader) -> None:
    trailer_bytes = 0
    while True:
        line = await reader.readline()
        if not line:
            return
        trailer_bytes += len(line)
        if trailer_bytes > _MAX_HEADER_BYTES:
            raise ValueError("HTTP trailers are too large")
        if line in {b"\r\n", b"\n"}:
            return


def _parse_connect_target(target: str) -> tuple[str, int]:
    host_port = target.strip()
    if host_port.startswith("[") and "]" in host_port:
        host, _, remainder = host_port[1:].partition("]")
        if remainder.startswith(":"):
            return host, int(remainder[1:])
        return host, 443
    if ":" in host_port:
        host, raw_port = host_port.rsplit(":", 1)
        return host, int(raw_port)
    return host_port, 443


async def _pipe_bytes(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
) -> None:
    try:
        while True:
            chunk = await source.read(65536)
            if not chunk:
                break
            destination.write(chunk)
            await destination.drain()
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, BrokenPipeError, OSError):
        return
    finally:
        with suppress(Exception):
            destination.close()
            await destination.wait_closed()


def _parse_request_line(line: str) -> tuple[str, str, str]:
    parts = line.split()
    if len(parts) != 3:
        raise ValueError(f"invalid request line: {line!r}")
    return parts[0].upper(), parts[1], parts[2]


def _parse_status(line: str) -> int:
    parts = line.split(maxsplit=2)
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1])
    except ValueError:
        return 0


def _resolve_target(target: str, headers: dict[str, str]) -> tuple[str, str, int, str]:
    parsed = urlsplit(target)
    if parsed.scheme and parsed.netloc:
        if parsed.scheme.lower() != "http":
            raise ValueError("only HTTP proxy capture is supported")
        host = parsed.hostname or ""
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return target, host, port, path

    host_header = headers.get("host", "")
    if not host_header:
        raise ValueError("origin-form requests require a Host header")
    if ":" in host_header:
        host, raw_port = host_header.rsplit(":", 1)
        port = int(raw_port)
    else:
        host = host_header
        port = 80
    url = f"http://{host_header}{target}"
    return url, host, port, target


def _build_origin_request(
    method: str,
    target: str,
    version: str,
    request: _HttpMessage,
) -> bytes:
    lines = [f"{method} {target} {version}\r\n".encode("iso-8859-1")]
    has_host = False
    for name, value in request.raw_headers:
        key = name.lower()
        if key in _HOP_BY_HOP_HEADERS or key in {"accept-encoding", "content-length"}:
            continue
        if key == "host":
            has_host = True
        lines.append(f"{name}: {value}\r\n".encode("iso-8859-1"))
    if not has_host:
        raise ValueError("request is missing Host header")
    lines.append(b"Accept-Encoding: identity\r\n")
    if request.body:
        lines.append(f"Content-Length: {len(request.body)}\r\n".encode("iso-8859-1"))
    lines.append(b"Connection: close\r\n")
    lines.append(b"\r\n")
    lines.append(request.body)
    return b"".join(lines)


def _serialize_message(message: _HttpMessage) -> bytes:
    data = [f"{message.first_line}\r\n".encode("iso-8859-1")]
    for name, value in message.raw_headers:
        key = name.lower()
        if key in _HOP_BY_HOP_HEADERS or key == "content-length":
            continue
        data.append(f"{name}: {value}\r\n".encode("iso-8859-1"))
    data.append(f"Content-Length: {len(message.body)}\r\n".encode("iso-8859-1"))
    data.append(b"Connection: close\r\n")
    data.append(b"\r\n")
    data.append(message.body)
    return b"".join(data)


def _captured_headers(message: _HttpMessage) -> dict[str, str]:
    headers = {
        name.lower(): value
        for name, value in message.raw_headers
        if name.lower() not in _HOP_BY_HOP_HEADERS and name.lower() != "content-length"
    }
    headers["content-length"] = str(len(message.body))
    return headers


def _headers_to_bytes(headers: dict[str, str]) -> bytes:
    return b"".join(
        f"{name}: {value}\r\n".encode("iso-8859-1")
        for name, value in headers.items()
    )


async def _write_error(
    writer: asyncio.StreamWriter,
    status: int,
    message: str,
) -> None:
    body = message.encode()
    reason = "Not Implemented" if status == 501 else "Bad Gateway"
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n".encode("iso-8859-1")
        + b"Content-Type: text/plain\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("iso-8859-1")
        + b"Connection: close\r\n\r\n"
        + body
    )
    await writer.drain()


def _decode_body(headers: dict[str, str], body: bytes) -> tuple[Any | None, BodyEncoding]:
    if not body:
        return None, "none"
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    decoded_body = _decompress_content_encoding(headers, body)
    text = decoded_body.decode("utf-8", errors="replace")
    if "json" in content_type:
        try:
            return json.loads(text), "json"
        except json.JSONDecodeError:
            return text, "raw"
    if content_type == "application/x-www-form-urlencoded":
        return dict(parse_qsl(text, keep_blank_values=True)), "form"
    return text, "raw"


def _is_chunked(headers: dict[str, str]) -> bool:
    return "chunked" in _header_tokens(headers.get("transfer-encoding", ""))


def _header_tokens(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _decompress_content_encoding(headers: dict[str, str], body: bytes) -> bytes:
    decoded = body
    for encoding in reversed(_header_tokens(headers.get("content-encoding", ""))):
        if encoding in {"identity", ""}:
            continue
        try:
            if encoding in {"gzip", "x-gzip"}:
                decoded = gzip.decompress(decoded)
            elif encoding == "deflate":
                decoded = _decompress_deflate(decoded)
            else:
                return decoded
        except (OSError, zlib.error):
            return body
    return decoded


def _decompress_deflate(body: bytes) -> bytes:
    try:
        return zlib.decompress(body)
    except zlib.error:
        return zlib.decompress(body, -zlib.MAX_WBITS)
