"""HTTP/1.1 last-byte gate (spec §11.2 Backend B).

Every request is staged on its own pre-opened TCP connection: all headers and
all body bytes except a small tail are sent in advance. At release time the
remaining tail bytes are written back-to-back, so the requests complete on the
wire within a sub-millisecond window.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

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


class _StagedRequest:
    __slots__ = ("prepared", "reader", "writer", "tail")

    def __init__(
        self,
        prepared: PreparedRequest,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        tail: bytes,
    ) -> None:
        self.prepared = prepared
        self.reader = reader
        self.writer = writer
        self.tail = tail


class Http1LastByteBackend:
    """Stage full requests on warm connections; release only the tails."""

    scheduler_id = "http1-last-byte"

    def __init__(
        self,
        scope: ScopeGuard,
        *,
        budget: BudgetTracker | None = None,
        tail_bytes: int = 1,
        read_timeout: float = 15.0,
    ) -> None:
        if tail_bytes < 1:
            raise ValueError("tail_bytes must be >= 1")
        self._scope = scope
        self._budget = budget
        self._tail_bytes = tail_bytes
        self._read_timeout = read_timeout
        self._staged: dict[str, list[_StagedRequest]] = {}

    async def prepare(self, requests: list[PreparedRequest]) -> PreparedRace:
        race_id = f"race-h1-{now_ns()}"
        staged: list[_StagedRequest] = []
        try:
            for prepared in requests:
                self._scope.check_url(prepared.url)
                staged.append(await self._stage(prepared))
        except Exception:
            for entry in staged:
                entry.writer.close()
            raise
        self._staged[race_id] = staged
        return PreparedRace(
            race_id=race_id,
            scheduler=self.scheduler_id,
            requests=requests,
            offsets_ms=[0.0] * len(requests),
            connection_strategy="separate_connections",
        )

    async def release(self, race: PreparedRace) -> RaceResult:
        staged = self._staged.pop(race.race_id, None)
        if staged is None or len(staged) != len(race.requests):
            raise ExecutionError(f"race {race.race_id} was not prepared")
        timeline: list[TimelineEvent] = []
        offsets = race.offsets_ms or [0.0] * len(staged)

        def mark(instance_id: str, event: TimelineEventKind) -> None:
            timeline.append(
                TimelineEvent(instance_id=instance_id, event=event, at_ns=now_ns())
            )

        # fire tails back-to-back; offsets (if any) are applied per request
        for index, delay_s in release_delays(offsets, len(staged)):
            entry = staged[index]
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            if self._budget is not None:
                self._budget.count_request()
            entry.writer.write(entry.tail)
            await entry.writer.drain()
            mark(entry.prepared.instance_id, "released")

        responses: list[HttpResponseRecord | None] = await asyncio.gather(
            *(self._read_response(index, entry, mark) for index, entry in enumerate(staged))
        )
        for entry in staged:
            try:
                entry.writer.close()
                await entry.writer.wait_closed()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        return RaceResult(
            responses=[r for r in responses if r is not None],
            timeline=timeline,
        )

    # -- internals -------------------------------------------------------------

    async def _stage(self, prepared: PreparedRequest) -> _StagedRequest:
        parsed = urlparse(prepared.url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.scheme == "https":
            raise ExecutionError("http1-last-byte gate supports plain HTTP only")
        reader, writer = await asyncio.open_connection(host, port)
        payload = self._serialize(prepared, parsed)
        head, tail = payload[: -self._tail_bytes], payload[-self._tail_bytes :]
        writer.write(head)
        await writer.drain()
        return _StagedRequest(prepared, reader, writer, tail)

    @staticmethod
    def _serialize(prepared: PreparedRequest, parsed: Any) -> bytes:
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        body = prepared.body or b""
        headers = {
            "Host": parsed.netloc,
            "Connection": "close",
            "Content-Length": str(len(body)),
            **prepared.headers,
        }
        head = f"{prepared.method} {path} HTTP/1.1\r\n"
        head += "".join(f"{name}: {value}\r\n" for name, value in headers.items())
        head += "\r\n"
        return head.encode() + body

    async def _read_response(
        self,
        index: int,
        entry: _StagedRequest,
        mark: Any,
    ) -> HttpResponseRecord:
        started = now_ns()
        instance = entry.prepared.instance_id
        try:
            raw_head = await asyncio.wait_for(
                entry.reader.readuntil(b"\r\n\r\n"), timeout=self._read_timeout
            )
            mark(instance, "first_byte_received")
            head_text = raw_head.decode(errors="replace")
            status_line, _, header_block = head_text.partition("\r\n")
            status = int(status_line.split(" ")[1]) if " " in status_line else 0
            headers = {}
            for line in header_block.split("\r\n"):
                if ": " in line:
                    name, _, value = line.partition(": ")
                    headers[name.lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body_bytes = b""
            if length > 0:
                body_bytes = await asyncio.wait_for(
                    entry.reader.readexactly(length), timeout=self._read_timeout
                )
            completed = now_ns()
            mark(instance, "completed")
        except (TimeoutError, OSError, ValueError) as exc:
            return HttpResponseRecord(
                instance_id=instance,
                started_at_ns=started,
                completed_at_ns=now_ns(),
                error=str(exc),
            )

        return HttpResponseRecord(
            instance_id=instance,
            status=status,
            headers=headers,
            body=decode_response_body(body_bytes, headers.get("content-type", "")),
            started_at_ns=started,
            completed_at_ns=completed,
        )
