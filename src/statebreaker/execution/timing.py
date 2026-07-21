"""High-resolution timing helpers for precision scheduling."""

from __future__ import annotations

import time

from statebreaker.models.execution import TimelineEvent


def now_ns() -> int:
    """Monotonic clock in nanoseconds; the only clock used for timelines."""
    return time.perf_counter_ns()


def release_delays(offsets: list[float], count: int) -> list[tuple[int, float]]:
    """Firing schedule for staged requests, in release order.

    Returns ``(request_index, delay_seconds)`` pairs sorted by offset ascending
    (ties keep input order). Each delay is measured from the smallest offset in
    the batch, so the earliest request fires immediately and the rest trail it
    by their relative offset. Missing offsets are treated as ``0``. Both raw
    gates share this so their offset handling can never drift apart.
    """
    resolved = [offsets[index] if index < len(offsets) else 0.0 for index in range(count)]
    base = min(resolved) if resolved else 0.0
    order = sorted(range(count), key=lambda index: resolved[index])
    return [(index, (resolved[index] - base) / 1000.0) for index in order]


def release_spread_ms(timeline: list[TimelineEvent], *, event: str = "released") -> float:
    """Worst-case skew between the first and last release of a race."""
    stamps = [entry.at_ns for entry in timeline if entry.event == event]
    if len(stamps) < 2:
        return 0.0
    return (max(stamps) - min(stamps)) / 1_000_000
