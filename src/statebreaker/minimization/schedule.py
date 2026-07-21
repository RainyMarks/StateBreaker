"""Scheduler minimization: prefer the simplest transport that still races (§14.3)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

#: From dumbest to most precise. If a plain barrier already triggers the race
#: reliably, the PoC should not demand a last-byte gate.
SIMPLICITY_ORDER: tuple[str, ...] = (
    "async-http",
    "http1-last-byte",
    "http2-stream-gate",
    "http3-quic",
)

SchedulerCheck = Callable[[str], Awaitable[bool]]


async def simplest_scheduler(
    triggers: SchedulerCheck,
    available: list[str],
    *,
    attempts: int = 2,
) -> str | None:
    """Return the simplest available scheduler that triggers within ``attempts``."""
    for scheduler in SIMPLICITY_ORDER:
        if scheduler not in available:
            continue
        for _ in range(max(1, attempts)):
            if await triggers(scheduler):
                return scheduler
    return None
