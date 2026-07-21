"""Phase 4 acceptance: precision gates beat plain concurrency (spec §11, §25).

Scenario: a check-then-act counter endpoint with a tunable race window. At a
wide window any scheduler races; at a narrow window only sub-millisecond
release synchronization still triggers the lost update.
"""

from __future__ import annotations

from support.h2server import H2RaceServer
from support.rawserver import RawRaceServer

from statebreaker.config.loader import ScopeGuard
from statebreaker.config.models import ProjectConfig
from statebreaker.execution.sessions import SessionManager
from statebreaker.execution.timing import release_spread_ms
from statebreaker.execution.transports.async_http import AsyncHttpBackend
from statebreaker.execution.transports.base import SchedulerBackend
from statebreaker.execution.transports.http1_gate import Http1LastByteBackend
from statebreaker.execution.transports.http2_gate import Http2StreamGateBackend
from statebreaker.models.execution import HttpResponseRecord, PreparedRequest

_WIDE_WINDOW_S = 0.05
_NARROW_WINDOW_S = 0.002
_TRIALS = 5


def _scope() -> ScopeGuard:
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "gates", "base_url": "http://127.0.0.1"},
            "scope": {"allowed_hosts": ["127.0.0.1"]},
        }
    )
    return ScopeGuard(config)


def _requests(port: int, count: int) -> list[PreparedRequest]:
    return [
        PreparedRequest(
            instance_id=f"inst-{index}",
            method="POST",
            url=f"http://127.0.0.1:{port}/race",
            headers={"content-type": "application/json"},
            body=b"{}",
        )
        for index in range(count)
    ]


def _raced(responses: list[HttpResponseRecord]) -> bool:
    observed = [
        response.body["observed"]
        for response in responses
        if isinstance(response.body, dict) and "observed" in response.body
    ]
    return len(observed) >= 2 and len(set(observed)) < len(observed)


async def _run_trials(
    backend: SchedulerBackend,
    port: int,
    *,
    trials: int = _TRIALS,
    offsets_ms: list[float] | None = None,
) -> tuple[int, float]:
    """Fire ``trials`` two-request races; return (races, worst release spread)."""
    races = 0
    worst_spread = 0.0
    for _ in range(trials):
        race = await backend.prepare(_requests(port, 2))
        if offsets_ms is not None:
            race = race.model_copy(update={"offsets_ms": offsets_ms})
        result = await backend.release(race)
        assert len(result.responses) == 2
        assert all(response.status == 200 for response in result.responses)
        if _raced(result.responses):
            races += 1
        worst_spread = max(worst_spread, release_spread_ms(result.timeline))
    return races, worst_spread


async def test_http1_gate_releases_tight_and_races() -> None:
    server = RawRaceServer(window_s=_WIDE_WINDOW_S)
    await server.start()
    try:
        backend = Http1LastByteBackend(_scope())
        races, spread = await _run_trials(backend, server.port)
        assert races >= _TRIALS - 1, f"gate should race reliably: {races}/{_TRIALS}"
        assert spread < 10.0, f"release spread too wide: {spread}ms"
    finally:
        await server.aclose()


async def test_http1_gate_timeline_covers_all_instances() -> None:
    server = RawRaceServer(window_s=_WIDE_WINDOW_S)
    await server.start()
    try:
        backend = Http1LastByteBackend(_scope())
        race = await backend.prepare(_requests(server.port, 2))
        result = await backend.release(race)
        events = {(event.instance_id, event.event) for event in result.timeline}
        for index in (0, 1):
            instance = f"inst-{index}"
            assert (instance, "released") in events
            assert (instance, "first_byte_received") in events
            assert (instance, "completed") in events
    finally:
        await server.aclose()


async def test_http1_gate_offsets_serialize_requests() -> None:
    # 50ms offset against a 20ms window: the second request reads only after
    # the first has written — the race disappears, proving offsets are applied.
    # (Windows timers can wake ~15ms early, so keep margins wide.)
    server = RawRaceServer(window_s=0.02)
    await server.start()
    try:
        backend = Http1LastByteBackend(_scope())
        races, spread = await _run_trials(
            backend, server.port, trials=3, offsets_ms=[0.0, 50.0]
        )
        assert races == 0, f"offset requests must behave sequentially: {races}/3 raced"
        assert spread >= 30.0, f"offset not visible in release spread: {spread}ms"
    finally:
        await server.aclose()


async def test_http2_gate_releases_tight_and_races() -> None:
    server = H2RaceServer(window_s=_WIDE_WINDOW_S)
    await server.start()
    try:
        backend = Http2StreamGateBackend(_scope())
        races, spread = await _run_trials(backend, server.port)
        assert races >= _TRIALS - 1, f"h2 gate should race reliably: {races}/{_TRIALS}"
        assert spread < 10.0, f"h2 release spread too wide: {spread}ms"
    finally:
        await server.aclose()


async def test_http2_gate_timeline_covers_gate_ready() -> None:
    server = H2RaceServer(window_s=_WIDE_WINDOW_S)
    await server.start()
    try:
        backend = Http2StreamGateBackend(_scope())
        race = await backend.prepare(_requests(server.port, 2))
        result = await backend.release(race)
        events = {(event.instance_id, event.event) for event in result.timeline}
        for index in (0, 1):
            instance = f"inst-{index}"
            assert (instance, "gate_ready") in events
            assert (instance, "released") in events
            assert (instance, "completed") in events
    finally:
        await server.aclose()


async def test_scanner_registers_precision_backends(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from statebreaker.artifacts.store import ArtifactStore
    from statebreaker.execution.client import BudgetTracker
    from statebreaker.models.execution import ScanBudget
    from statebreaker.orchestration.scanner import AutoRaceScanner

    scanner = AutoRaceScanner(ArtifactStore(tmp_path / "project"))
    sessions = SessionManager("http://127.0.0.1:1")
    backends = scanner._backends(sessions, _scope(), BudgetTracker(ScanBudget()))
    assert set(backends) >= {"async-http", "http1-last-byte", "http2-stream-gate"}
    await sessions.aclose()


async def test_narrow_window_gates_beat_plain_concurrency() -> None:
    # The Phase 4 acceptance scenario: at a 2ms window, barrier-released httpx
    # requests mostly miss each other while the last-byte gate still collides.
    server = RawRaceServer(window_s=_NARROW_WINDOW_S)
    await server.start()
    try:
        sessions = SessionManager(f"http://127.0.0.1:{server.port}")
        async_backend = AsyncHttpBackend(sessions, _scope())
        gate_backend = Http1LastByteBackend(_scope())

        async_races, _ = await _run_trials(async_backend, server.port)
        server.count = 0
        gate_races, _ = await _run_trials(gate_backend, server.port)

        await sessions.aclose()
        assert gate_races >= async_races
        assert gate_races >= _TRIALS - 1, f"gate must race at 2ms window: {gate_races}/{_TRIALS}"
    finally:
        await server.aclose()
