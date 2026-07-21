"""Scheduler backend boundary: stage requests, then release them together."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from statebreaker.models.execution import (
    HttpResponseRecord,
    PreparedRace,
    PreparedRequest,
    TimelineEvent,
)


class RaceResult:
    """What a backend produces: responses plus a high-resolution timeline."""

    def __init__(
        self,
        responses: list[HttpResponseRecord],
        timeline: list[TimelineEvent],
    ) -> None:
        self.responses = responses
        self.timeline = timeline


@runtime_checkable
class SchedulerBackend(Protocol):
    """A precision scheduling mechanism (spec §11.1)."""

    scheduler_id: str

    async def prepare(self, requests: list[PreparedRequest]) -> PreparedRace:
        ...

    async def release(self, race: PreparedRace) -> RaceResult:
        ...


def decode_response_body(raw: bytes, content_type: str) -> Any:
    """Decode raw response bytes into a Python value the oracle can compare.

    JSON bodies become dict/list (falling back to text on parse errors); every
    other body stays text. Empty bodies are ``None``. Shared by all backends so
    the wire-decoding rule lives in exactly one place.
    """
    if not raw:
        return None
    text = bytes(raw).decode(errors="replace")
    if "json" in content_type:
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text
