"""Repeatability statistics for a minimized attack plan (spec §14.4)."""

from __future__ import annotations

import statistics
from collections.abc import Awaitable, Callable

from statebreaker.models.findings import RunStatistics


class TrialSignal:
    """One measured attack round: did it trigger, and how clean was the fire."""

    def __init__(
        self,
        *,
        triggered: bool,
        release_skew_ms: float = 0.0,
        elapsed_ms: float = 0.0,
    ) -> None:
        self.triggered = triggered
        self.release_skew_ms = release_skew_ms
        self.elapsed_ms = elapsed_ms


SignalSource = Callable[[], Awaitable[TrialSignal]]


async def measure_run_statistics(
    run_once: SignalSource,
    *,
    rounds: int = 10,
) -> RunStatistics:
    """Run ``run_once`` ``rounds`` times and aggregate success/timing numbers."""
    signals = [await run_once() for _ in range(max(1, rounds))]
    successes = sum(1 for signal in signals if signal.triggered)
    skews = [signal.release_skew_ms for signal in signals]
    durations = [signal.elapsed_ms for signal in signals]
    return RunStatistics(
        rounds=len(signals),
        successes=successes,
        success_rate=round(successes / len(signals), 3),
        median_release_skew_ms=round(statistics.median(skews), 4) if skews else 0.0,
        mean_trigger_time_ms=round(statistics.fmean(durations), 4) if durations else 0.0,
    )
