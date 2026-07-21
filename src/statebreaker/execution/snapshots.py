"""Shared probe snapshot runner used by baseline and attack trials."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Protocol

from statebreaker.errors import TemplateError
from statebreaker.execution.client import HttpSender
from statebreaker.intelligence.dependency_inference import send_template
from statebreaker.models.state import NormalizedState, StateProbe, StateSnapshot


class StateNormalizerLike(Protocol):
    """Minimal normalizer interface needed by probe snapshots."""

    def normalize(self, raw_state: object) -> NormalizedState:
        ...


class ProbeSnapshotRunner:
    """Render state probes and capture their current observable state."""

    def __init__(
        self,
        sender: HttpSender,
        probes: list[StateProbe],
        *,
        session_id: str = "default",
    ) -> None:
        self._sender = sender
        self._probes = list(probes)
        self._session_id = session_id

    async def snapshot(
        self,
        variables: Mapping[str, Any],
        normalizers: Mapping[str, StateNormalizerLike] | None = None,
    ) -> list[StateSnapshot]:
        snapshots: list[StateSnapshot] = []
        active_normalizers = normalizers or {}
        for probe in self._probes:
            try:
                exchange = await send_template(
                    probe.request_template,
                    variables,
                    self._sender,
                    session_id=self._session_id,
                )
            except TemplateError:
                continue
            normalizer = active_normalizers.get(probe.probe_id)
            taken_at = time.perf_counter_ns()
            snapshots.append(
                StateSnapshot(
                    snapshot_id=f"snap-{probe.probe_id}-{taken_at}",
                    probe_id=probe.probe_id,
                    taken_at_ns=taken_at,
                    raw=exchange.response_body,
                    normalized=normalizer.normalize(exchange.response_body)
                    if normalizer
                    else None,
                )
            )
        return snapshots
