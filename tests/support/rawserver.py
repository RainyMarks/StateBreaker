"""Minimal asyncio HTTP/1.1 race server for precision-scheduler tests.

Implements a check-then-act counter with a configurable sleep between the
read and the write, so concurrent requests lose updates exactly like a real
race-prone endpoint. One request per connection (``Connection: close``).
"""

from __future__ import annotations

import asyncio
import json


class RawRaceServer:
    """HTTP/1.1 server whose POST /race handler has a widening race window."""

    def __init__(self, *, window_s: float = 0.005) -> None:
        self.window_s = window_s
        self.count = 0
        self._server: asyncio.AbstractServer | None = None

    @property
    def port(self) -> int:
        assert self._server is not None and self._server.sockets
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        status, payload = 400, b'{"error": "bad request"}'
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            request_line, _, header_block = head.decode("latin-1").partition("\r\n")
            parts = request_line.split(" ")
            if len(parts) < 2:
                raise ValueError("malformed request line")
            method, target = parts[0], parts[1]
            headers: dict[str, str] = {}
            for line in header_block.split("\r\n"):
                if ": " in line:
                    name, _, value = line.partition(": ")
                    headers[name.lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            if length:
                await reader.readexactly(length)
            status, payload = await self._route(method, target.split("?")[0])
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            ValueError,
        ):
            pass
        reason = "OK" if status == 200 else "Error"
        response = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "content-type: application/json\r\n"
            f"content-length: {len(payload)}\r\n"
            "connection: close\r\n"
            "\r\n"
        ).encode() + payload
        try:
            writer.write(response)
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def _route(self, method: str, path: str) -> tuple[int, bytes]:
        if method == "POST" and path == "/race":
            observed = self.count
            await asyncio.sleep(self.window_s)
            self.count = observed + 1
            return 200, json.dumps({"observed": observed, "new": observed + 1}).encode()
        if method == "POST" and path == "/reset":
            self.count = 0
            return 200, b'{"ok": true}'
        if method == "GET" and path == "/state":
            return 200, json.dumps({"count": self.count}).encode()
        return 404, b'{"error": "not found"}'
