"""HTTP/2 stream-synchronization gate (spec §11.2 Backend C).

All race requests are multiplexed on a single pre-warmed h2c connection, one
stream each. During ``prepare`` the headers and body of every request are sent
with ``END_STREAM`` withheld; ``release`` emits the final empty DATA frames
back-to-back, so the server sees every request complete inside one send
window. Cleartext h2c with prior knowledge only — no TLS, no upgrade dance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import h2.config
import h2.connection
import h2.events

from statebreaker.config.loader import ScopeGuard
from statebreaker.errors import ExecutionError
from statebreaker.execution.client import BudgetTracker
from statebreaker.execution.timing import now_ns, release_delays
from statebreaker.execution.transports.base import RaceResult, decode_response_body
from statebreaker.models.execution import (
    HttpResponseRecord,
    PreparedRace,
    PreparedRequest,
    TimelineEvent,
    TimelineEventKind,
)


class _StreamState:
    __slots__ = ("prepared", "stream_id", "status", "headers", "body", "started", "completed")

    def __init__(self, prepared: PreparedRequest, stream_id: int) -> None:
        self.prepared = prepared
        self.stream_id = stream_id
        self.status = 0
        self.headers: dict[str, str] = {}
        self.body = bytearray()
        self.started = 0
        self.completed = 0


class Http2StreamGateBackend:
    """Stage requests on one h2 connection; release all END_STREAM frames at once."""

    scheduler_id = "http2-stream-gate"

    def __init__(
        self,
        scope: ScopeGuard,
        *,
        budget: BudgetTracker | None = None,
        read_timeout: float = 15.0,
    ) -> None:
        self._scope = scope
        self._budget = budget
        self._read_timeout = read_timeout
        self._staged: dict[str, _H2Gate] = {}

    async def prepare(self, requests: list[PreparedRequest]) -> PreparedRace:
        for request in requests:
            self._scope.check_url(request.url)
        race_id = f"race-h2-{now_ns()}"
        gate = _H2Gate(read_timeout=self._read_timeout)
        try:
            await gate.open(requests)
        except Exception:
            await gate.aclose()
            raise
        self._staged[race_id] = gate
        return PreparedRace(
            race_id=race_id,
            scheduler=self.scheduler_id,
            requests=requests,
            offsets_ms=[0.0] * len(requests),
            connection_strategy="same_connection",
        )

    async def release(self, race: PreparedRace) -> RaceResult:
        gate = self._staged.pop(race.race_id, None)
        if gate is None or len(gate.streams) != len(race.requests):
            raise ExecutionError(f"race {race.race_id} was not prepared")
        try:
            return await gate.release(race, budget=self._budget)
        finally:
            await gate.aclose()


class _H2Gate:
    def __init__(self, *, read_timeout: float) -> None:
        self._read_timeout = read_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._conn: h2.connection.H2Connection | None = None
        self.streams: list[_StreamState] = []
        self._staged_events: list[TimelineEvent] = []

    async def open(self, requests: list[PreparedRequest]) -> None:
        parsed = urlparse(requests[0].url)
        if parsed.scheme != "http":
            raise ExecutionError("http2-stream-gate supports cleartext http (h2c) targets only")
        for request in requests[1:]:
            other = urlparse(request.url)
            if (other.scheme, other.hostname, other.port) != (
                parsed.scheme,
                parsed.hostname,
                parsed.port,
            ):
                raise ExecutionError("http2-stream-gate needs one origin per race")
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        self._reader, self._writer = await asyncio.open_connection(host, port)
        config = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()
        self._writer.write(conn.data_to_send())
        await self._writer.drain()
        self._conn = conn
        self._mark(requests[0].instance_id, "connection_opened")

        authority = parsed.netloc
        for request in requests:
            stream_id = conn.get_next_available_stream_id()
            request_url = urlparse(request.url)
            path = request_url.path or "/"
            if request_url.query:
                path = f"{path}?{request_url.query}"
            headers: list[tuple[str, str]] = [
                (":method", request.method),
                (":authority", authority),
                (":scheme", "http"),
                (":path", path),
            ]
            for name, value in request.headers.items():
                lowered = name.lower()
                if lowered in ("host", "connection", "content-length"):
                    continue
                headers.append((lowered, value))
            body = bytes(request.body or b"")
            headers.append(("content-length", str(len(body))))
            self._mark(request.instance_id, "headers_started")
            conn.send_headers(stream_id, headers, end_stream=False)
            if body:
                self._mark(request.instance_id, "body_started")
                conn.send_data(stream_id, body, end_stream=False)
            self.streams.append(_StreamState(request, stream_id))
            self._mark(request.instance_id, "gate_ready")
        self._writer.write(conn.data_to_send())
        await self._writer.drain()

    async def release(
        self,
        race: PreparedRace,
        *,
        budget: BudgetTracker | None = None,
    ) -> RaceResult:
        conn = self._conn
        reader = self._reader
        writer = self._writer
        assert conn is not None and reader is not None and writer is not None
        timeline = list(self._staged_events)
        offsets = race.offsets_ms or [0.0] * len(self.streams)

        def mark(instance_id: str, event: TimelineEventKind) -> None:
            timeline.append(
                TimelineEvent(instance_id=instance_id, event=event, at_ns=now_ns())
            )

        for index, delay_s in release_delays(offsets, len(self.streams)):
            stream = self.streams[index]
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            if budget is not None:
                budget.count_request()
            conn.end_stream(stream.stream_id)
            writer.write(conn.data_to_send())
            await writer.drain()
            stream.started = now_ns()
            mark(stream.prepared.instance_id, "released")

        failure: str | None = None
        pending = len(self.streams)
        try:
            while pending > 0:
                data = await asyncio.wait_for(reader.read(65535), timeout=self._read_timeout)
                if not data:
                    raise ConnectionError("http2 gate: connection closed by peer")
                for event in conn.receive_data(data):
                    if isinstance(event, h2.events.ResponseReceived | h2.events.TrailersReceived):
                        found = self._by_stream(event.stream_id)
                        if found is not None:
                            self._apply_headers(found, event.headers)
                            if isinstance(event, h2.events.ResponseReceived):
                                mark(found.prepared.instance_id, "first_byte_received")
                    elif isinstance(event, h2.events.DataReceived):
                        found = self._by_stream(event.stream_id)
                        if found is not None:
                            found.body.extend(event.data)
                            conn.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                    elif isinstance(event, h2.events.StreamEnded | h2.events.StreamReset):
                        found = self._by_stream(event.stream_id)
                        if found is not None and found.completed == 0:
                            found.completed = now_ns()
                            mark(found.prepared.instance_id, "completed")
                            pending -= 1
                out = conn.data_to_send()
                if out:
                    writer.write(out)
                    await writer.drain()
        except (TimeoutError, OSError, ConnectionError) as exc:
            failure = str(exc)

        responses: list[HttpResponseRecord] = []
        for stream in self.streams:
            instance = stream.prepared.instance_id
            if stream.completed == 0 and failure is not None:
                responses.append(
                    HttpResponseRecord(
                        instance_id=instance,
                        started_at_ns=stream.started,
                        completed_at_ns=now_ns(),
                        error=failure,
                    )
                )
                continue
            responses.append(
                HttpResponseRecord(
                    instance_id=instance,
                    status=stream.status,
                    headers=dict(stream.headers),
                    body=decode_response_body(
                        bytes(stream.body), stream.headers.get("content-type", "")
                    ),
                    started_at_ns=stream.started,
                    completed_at_ns=stream.completed,
                )
            )
        return RaceResult(responses=responses, timeline=timeline)

    async def aclose(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # pragma: no cover - best effort cleanup
                pass

    def _mark(self, instance_id: str, event: TimelineEventKind) -> None:
        self._staged_events.append(
            TimelineEvent(instance_id=instance_id, event=event, at_ns=now_ns())
        )

    def _by_stream(self, stream_id: int) -> _StreamState | None:
        for stream in self.streams:
            if stream.stream_id == stream_id:
                return stream
        return None

    @staticmethod
    def _apply_headers(stream: _StreamState, headers: Iterable[tuple[Any, Any]]) -> None:
        for name, value in headers:
            key = name.decode("latin-1") if isinstance(name, bytes) else str(name)
            text = value.decode("latin-1") if isinstance(value, bytes) else str(value)
            lowered = key.lower()
            if lowered == ":status":
                stream.status = int(text)
            elif not lowered.startswith(":"):
                stream.headers[lowered] = text
