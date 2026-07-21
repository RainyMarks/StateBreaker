"""Minimal h2c (cleartext HTTP/2) race server for stream-gate tests.

Same check-then-act counter semantics as the HTTP/1.1 raw server, but spoken
over a single multiplexed HTTP/2 connection using hyper-h2. Responses are
deferred through asyncio tasks so concurrent streams overlap inside the
configurable race window.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import h2.config
import h2.connection
import h2.events
import h2.exceptions


class H2RaceServer:
    """HTTP/2 (h2c, prior knowledge) server with a widening race window."""

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
        config = h2.config.H2Configuration(client_side=False, header_encoding="utf-8")
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()
        writer.write(conn.data_to_send())
        await writer.drain()
        streams: dict[int, dict[str, Any]] = {}
        tasks: set[asyncio.Task[None]] = set()
        try:
            while True:
                data = await reader.read(65535)
                if not data:
                    break
                for event in conn.receive_data(data):
                    if isinstance(event, h2.events.RequestReceived):
                        streams[event.stream_id] = {
                            "headers": dict(event.headers),
                        }
                    elif isinstance(event, h2.events.DataReceived):
                        conn.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                    elif isinstance(event, h2.events.StreamEnded):
                        state = streams.pop(event.stream_id, {"headers": {}})
                        task = asyncio.create_task(
                            self._respond(conn, writer, event.stream_id, state)
                        )
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                out = conn.data_to_send()
                if out:
                    writer.write(out)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()

    async def _respond(
        self,
        conn: h2.connection.H2Connection,
        writer: asyncio.StreamWriter,
        stream_id: int,
        state: dict[str, Any],
    ) -> None:
        headers = state["headers"]
        method = headers.get(":method", "GET")
        path = headers.get(":path", "/")
        if method == "POST" and path == "/race":
            observed = self.count
            await asyncio.sleep(self.window_s)
            self.count = observed + 1
            status = "200"
            payload = json.dumps({"observed": observed, "new": observed + 1}).encode()
        elif method == "POST" and path == "/reset":
            self.count = 0
            status, payload = "200", b'{"ok": true}'
        elif method == "GET" and path == "/state":
            status, payload = "200", json.dumps({"count": self.count}).encode()
        else:
            status, payload = "404", b'{"error": "not found"}'
        try:
            conn.send_headers(
                stream_id,
                [
                    (":status", status),
                    ("content-type", "application/json"),
                    ("content-length", str(len(payload))),
                ],
            )
            conn.send_data(stream_id, payload, end_stream=True)
            writer.write(conn.data_to_send())
            await writer.drain()
        except (ConnectionError, h2.exceptions.StreamClosedError):
            pass
