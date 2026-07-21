"""Local HTTP proxy recorder tests."""

from __future__ import annotations

import asyncio
import gzip
import json
from contextlib import suppress

import pytest

from statebreaker.capture.proxy import (
    HttpProxyRecorder,
    is_loopback_listen_host,
    start_http_proxy_recorder,
)


def test_proxy_loopback_listen_host_detection() -> None:
    assert is_loopback_listen_host("127.0.0.1")
    assert is_loopback_listen_host("localhost")
    assert is_loopback_listen_host("[::1]")
    assert not is_loopback_listen_host("0.0.0.0")
    assert not is_loopback_listen_host("example.test")


@pytest.mark.asyncio
async def test_http_proxy_rejects_public_bind_by_default() -> None:
    recorder = HttpProxyRecorder(
        capture_id="cap-public",
        listen_host="0.0.0.0",
        listen_port=0,
    )

    with pytest.raises(ValueError, match="non-loopback host"):
        await recorder.start()


@pytest.mark.asyncio
async def test_http_proxy_records_forwarded_exchange() -> None:
    origin = await asyncio.start_server(_origin_handler, "127.0.0.1", 0)
    origin_port = int(origin.sockets[0].getsockname()[1])
    recorder = await start_http_proxy_recorder(
        capture_id="cap-proxy",
        project="demo",
        listen_port=0,
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", recorder.bound_port)
        body = json.dumps({"name": "widget"}).encode()
        writer.write(
            (
                f"POST http://127.0.0.1:{origin_port}/api/items?debug=1 HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{origin_port}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "\r\n"
            ).encode()
            + body
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        assert b"HTTP/1.1 201 Created" in response
        await recorder.wait_for_exchanges(1)
        trace = await recorder.stop()
    finally:
        origin.close()
        await origin.wait_closed()

    assert trace.capture_id == "cap-proxy"
    assert trace.source == "proxy"
    assert len(trace.exchanges) == 1
    exchange = trace.exchanges[0]
    assert exchange.exchange_id == "proxy-1"
    assert exchange.method == "POST"
    assert exchange.url == f"http://127.0.0.1:{origin_port}/api/items?debug=1"
    assert exchange.request_body == {"name": "widget"}
    assert exchange.request_body_encoding == "json"
    assert exchange.response_status == 201
    assert exchange.response_body == {"created": True}
    assert exchange.response_body_encoding == "json"


@pytest.mark.asyncio
async def test_http_proxy_dechunks_response_before_forwarding_and_recording() -> None:
    origin = await asyncio.start_server(_chunked_response_origin_handler, "127.0.0.1", 0)
    origin_port = int(origin.sockets[0].getsockname()[1])
    recorder = await start_http_proxy_recorder(capture_id="cap-chunked-response", listen_port=0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", recorder.bound_port)
        writer.write(
            (
                f"GET http://127.0.0.1:{origin_port}/chunked HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{origin_port}\r\n"
                "\r\n"
            ).encode()
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        await recorder.wait_for_exchanges(1)
        trace = await recorder.stop()
    finally:
        origin.close()
        await origin.wait_closed()

    assert b"HTTP/1.1 200 OK" in response
    assert b"Transfer-Encoding" not in response
    assert b"Content-Length: 12" in response
    assert response.endswith(b'{"ok": true}')
    exchange = trace.exchanges[0]
    assert exchange.response_body == {"ok": True}
    assert exchange.response_headers["content-length"] == "12"
    assert "transfer-encoding" not in exchange.response_headers


@pytest.mark.asyncio
async def test_http_proxy_dechunks_request_before_forwarding_and_recording() -> None:
    origin = await asyncio.start_server(_chunked_request_origin_handler, "127.0.0.1", 0)
    origin_port = int(origin.sockets[0].getsockname()[1])
    recorder = await start_http_proxy_recorder(capture_id="cap-chunked-request", listen_port=0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", recorder.bound_port)
        writer.write(
            (
                f"POST http://127.0.0.1:{origin_port}/chunked HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{origin_port}\r\n"
                "Content-Type: application/json\r\n"
                "Transfer-Encoding: chunked\r\n"
                "\r\n"
                "7\r\n{\"name\"\r\n"
                "A\r\n: \"chunk\"}\r\n"
                "0\r\n"
                "\r\n"
            ).encode()
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        await recorder.wait_for_exchanges(1)
        trace = await recorder.stop()
    finally:
        origin.close()
        await origin.wait_closed()

    assert b"HTTP/1.1 200 OK" in response
    exchange = trace.exchanges[0]
    assert exchange.request_body == {"name": "chunk"}
    assert "transfer-encoding" not in exchange.request_headers
    assert exchange.response_body == {"received": True}


@pytest.mark.asyncio
async def test_http_proxy_decodes_gzip_json_response_for_capture() -> None:
    origin = await asyncio.start_server(_gzip_response_origin_handler, "127.0.0.1", 0)
    origin_port = int(origin.sockets[0].getsockname()[1])
    recorder = await start_http_proxy_recorder(capture_id="cap-gzip", listen_port=0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", recorder.bound_port)
        writer.write(
            (
                f"GET http://127.0.0.1:{origin_port}/gzip HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{origin_port}\r\n"
                "Accept-Encoding: gzip, br\r\n"
                "\r\n"
            ).encode()
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        await recorder.wait_for_exchanges(1)
        trace = await recorder.stop()
    finally:
        origin.close()
        await origin.wait_closed()

    assert b"Content-Encoding: gzip" in response
    exchange = trace.exchanges[0]
    assert exchange.response_body == {"compressed": True}
    assert exchange.response_body_encoding == "json"


@pytest.mark.asyncio
async def test_http_proxy_tunnels_https_connect_without_recording() -> None:
    origin = await asyncio.start_server(_tcp_echo_handler, "127.0.0.1", 0)
    origin_port = int(origin.sockets[0].getsockname()[1])
    recorder = await start_http_proxy_recorder(capture_id="cap-connect", listen_port=0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", recorder.bound_port)
        writer.write(
            (
                f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{origin_port}\r\n"
                "\r\n"
            ).encode()
        )
        await writer.drain()
        preamble = await reader.readuntil(b"\r\n\r\n")
        writer.write(b"ping-tunnel")
        await writer.drain()
        echoed = await reader.readexactly(len(b"ping-tunnel"))
        writer.close()
        await writer.wait_closed()
        trace = await recorder.stop()
    finally:
        origin.close()
        await origin.wait_closed()
        with suppress(Exception):
            await recorder.stop()

    assert b"200 Connection Established" in preamble
    assert echoed == b"ping-tunnel"
    assert trace.exchanges == []


async def _tcp_echo_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    data = await reader.read(65536)
    writer.write(data)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_http_proxy_stop_cancels_idle_client_handlers() -> None:
    recorder = await start_http_proxy_recorder(capture_id="cap-idle-stop", listen_port=0)
    reader, writer = await asyncio.open_connection("127.0.0.1", recorder.bound_port)
    try:
        await asyncio.sleep(0.05)
        trace = await recorder.stop()
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
        await recorder.stop()

    assert trace.exchanges == []
    assert recorder._client_tasks == set()


async def _origin_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    header_blob = await reader.readuntil(b"\r\n\r\n")
    header_text = header_blob.decode("iso-8859-1")
    content_length = 0
    for line in header_text.splitlines():
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
            break
    body = await reader.readexactly(content_length)
    assert json.loads(body.decode()) == {"name": "widget"}
    response_body = json.dumps({"created": True}).encode()
    writer.write(
        b"HTTP/1.1 201 Created\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(response_body)}\r\n".encode()
        + b"\r\n"
        + response_body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _chunked_response_origin_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    await reader.readuntil(b"\r\n\r\n")
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
        b"6\r\n{\"ok\":\r\n"
        b"6\r\n true}\r\n"
        b"0\r\n"
        b"\r\n"
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _chunked_request_origin_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    header_blob = await reader.readuntil(b"\r\n\r\n")
    header_text = header_blob.decode("iso-8859-1")
    headers = _parse_raw_headers(header_text)
    assert headers.get("accept-encoding") == "identity"
    assert "transfer-encoding" not in headers
    assert headers.get("content-length") == "17"
    body = await reader.readexactly(17)
    assert json.loads(body.decode()) == {"name": "chunk"}
    response_body = json.dumps({"received": True}).encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(response_body)}\r\n".encode()
        + b"\r\n"
        + response_body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _gzip_response_origin_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    header_blob = await reader.readuntil(b"\r\n\r\n")
    headers = _parse_raw_headers(header_blob.decode("iso-8859-1"))
    assert headers.get("accept-encoding") == "identity"
    body = gzip.compress(json.dumps({"compressed": True}).encode())
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Encoding: gzip\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"\r\n"
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def _parse_raw_headers(header_text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_text.splitlines()[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    return headers
