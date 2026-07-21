"""Baseline scheduler: barrier-synchronized asyncio concurrency over httpx.

This is the fast, portable backend (spec §11.2 Backend A). It cannot promise
network-level simultaneity; precise gates live in the http1/http2 backends.
"""

from __future__ import annotations

import time

import anyio
import httpx

from statebreaker.config.loader import ScopeGuard
from statebreaker.errors import ExecutionError
from statebreaker.execution.client import BudgetTracker
from statebreaker.execution.sessions import SessionManager
from statebreaker.execution.transports.base import RaceResult, decode_response_body
from statebreaker.models.execution import (
    HttpResponseRecord,
    PreparedRace,
    PreparedRequest,
    TimelineEvent,
    TimelineEventKind,
)


class AsyncHttpBackend:
    """Fire all staged requests on one barrier release."""

    scheduler_id = "async-http"

    def __init__(
        self,
        sessions: SessionManager,
        scope: ScopeGuard,
        *,
        budget: BudgetTracker | None = None,
    ) -> None:
        self._sessions = sessions
        self._scope = scope
        self._budget = budget

    async def prepare(self, requests: list[PreparedRequest]) -> PreparedRace:
        for request in requests:
            self._scope.check_url(request.url)
        return PreparedRace(
            race_id=f"race-{time.perf_counter_ns()}",
            scheduler=self.scheduler_id,
            requests=requests,
            offsets_ms=[0.0] * len(requests),
            connection_strategy="separate_connections",
        )

    async def release(self, race: PreparedRace) -> RaceResult:
        gate = anyio.Event()
        ready_count = 0
        ready_event = anyio.Event()
        timeline: list[TimelineEvent] = []
        responses: list[HttpResponseRecord | None] = [None] * len(race.requests)
        offsets = race.offsets_ms or [0.0] * len(race.requests)

        def mark(instance_id: str, event: TimelineEventKind) -> None:
            timeline.append(
                TimelineEvent(
                    instance_id=instance_id,
                    event=event,
                    at_ns=time.perf_counter_ns(),
                )
            )

        async def fire(index: int, prepared: PreparedRequest) -> None:
            nonlocal ready_count
            client = self._sessions.client_for(prepared.session_id)
            request = client.build_request(
                prepared.method,
                prepared.url,
                headers=prepared.headers,
                content=prepared.body,
            )
            ready_count += 1
            mark(prepared.instance_id, "gate_ready")
            if ready_count == len(race.requests):
                ready_event.set()
            await gate.wait()

            offset_ms = offsets[index] if index < len(offsets) else 0.0
            if offset_ms > 0:
                await anyio.sleep(offset_ms / 1000.0)
            mark(prepared.instance_id, "released")
            if self._budget is not None:
                self._budget.count_request()
            started = time.perf_counter_ns()
            try:
                response = await client.send(request, stream=True)
                mark(prepared.instance_id, "first_byte_received")
                body_bytes = await response.aread()
                await response.aclose()
                completed = time.perf_counter_ns()
                mark(prepared.instance_id, "completed")
                responses[index] = _record(prepared, response, body_bytes, started, completed)
            except httpx.HTTPError as exc:
                completed = time.perf_counter_ns()
                responses[index] = HttpResponseRecord(
                    instance_id=prepared.instance_id,
                    started_at_ns=started,
                    completed_at_ns=completed,
                    error=str(exc),
                )

        async with anyio.create_task_group() as task_group:
            for index, prepared in enumerate(race.requests):
                task_group.start_soon(fire, index, prepared)
            await ready_event.wait()
            gate.set()

        missing = [r for r in responses if r is None]
        if missing:
            raise ExecutionError("race release produced no response for an instance")
        return RaceResult(
            responses=[r for r in responses if r is not None],
            timeline=timeline,
        )


def _record(
    prepared: PreparedRequest,
    response: httpx.Response,
    body_bytes: bytes,
    started: int,
    completed: int,
) -> HttpResponseRecord:
    return HttpResponseRecord(
        instance_id=prepared.instance_id,
        status=response.status_code,
        headers={k.lower(): v for k, v in response.headers.items()},
        body=decode_response_body(body_bytes, response.headers.get("content-type", "")),
        started_at_ns=started,
        completed_at_ns=completed,
    )
