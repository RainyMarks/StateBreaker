"""Flatten probe response bodies and classify fields as stable or volatile.

Classification is purely statistical (type consistency + cross-sample behaviour);
it never encodes assumptions about what a field is supposed to mean. That judgement
is left to the ``InvariantProposer`` implementations in ``proposers.py``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from statebreaker.models import StateProfile

from .sampling import NormalSample

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{16,}$")


def flatten(value: Any, prefix: str = "$") -> dict[str, Any]:
    """Flatten nested JSON into JSONPath-style leaf keys, e.g. ``$.discount_yuan``.

    The key syntax matches ``Invariant.selector`` so a proposed invariant's selector
    can be used as-is by a JSONPath-based verifier.
    """

    if isinstance(value, dict):
        if not value:
            return {prefix: value}
        flat: dict[str, Any] = {}
        for field_name, field_value in value.items():
            flat.update(flatten(field_value, f"{prefix}.{field_name}"))
        return flat
    if isinstance(value, list):
        if not value:
            return {prefix: value}
        flat = {}
        for position, item in enumerate(value):
            flat.update(flatten(item, f"{prefix}[{position}]"))
        return flat
    return {prefix: value}


def _looks_volatile(values: list[Any]) -> bool:
    """True for fields that must never be turned into invariant candidates.

    Only judges a single probe's value stream in isolation (never pooled across
    probes), because "before" and "after" naturally share the same run identity
    and would otherwise look artificially stable when interleaved.
    """

    if len(values) < 2:
        return False
    if not all(isinstance(item, str) for item in values):
        return False
    strings: list[str] = values
    if any(
        _UUID_RE.match(item) or _ISO_TIMESTAMP_RE.match(item) or _HEX_TOKEN_RE.match(item)
        for item in strings
    ):
        return True
    return len(set(strings)) == len(strings)


def build_state_profile(workflow_name: str, samples: list[NormalSample]) -> StateProfile:
    """Classify probed fields as stable or ignored based on cross-sample behaviour."""

    raw_samples = [dict(sample.probes) for sample in samples]

    # One value stream per (probe_id, key), kept separate per probe: "before" and
    # "after" share the same run identity within a round, so pooling them together
    # would make a volatile field like run_id look artificially stable.
    streams_by_key: dict[str, list[list[Any]]] = defaultdict(list)
    probe_ids = sorted({probe_id for sample in samples for probe_id in sample.probes})
    for probe_id in probe_ids:
        values_by_key: dict[str, list[Any]] = defaultdict(list)
        for sample in samples:
            body = sample.probes.get(probe_id)
            if body is None:
                continue
            for key, value in flatten(body).items():
                values_by_key[key].append(value)
        for key, values in values_by_key.items():
            streams_by_key[key].append(values)

    stable_fields: list[str] = []
    ignored_fields: list[str] = []
    for key, streams in streams_by_key.items():
        if any(_looks_volatile(stream) for stream in streams):
            ignored_fields.append(key)
        else:
            stable_fields.append(key)

    return StateProfile(
        workflow_name=workflow_name,
        samples=raw_samples,
        stable_fields=sorted(stable_fields),
        ignored_fields=sorted(ignored_fields),
    )
