"""Structural JSON flatten/diff helpers shared by probes and baseline learning."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from statebreaker.intelligence.lineage import iter_json_leaves
from statebreaker.models.state import FieldChange


def flatten_state(value: Any) -> dict[str, Any]:
    """Flatten a JSON-like value into ``{jsonpath: scalar}``."""
    return {path: leaf for path, leaf in iter_json_leaves(value)}


def diff_flat(
    flat_before: Mapping[str, Any], flat_after: Mapping[str, Any]
) -> list[FieldChange]:
    """Field-level diff of two already-flattened states."""
    changes: list[FieldChange] = []
    for path in sorted(set(flat_before) | set(flat_after)):
        old = flat_before.get(path)
        new = flat_after.get(path)
        if old == new and path in flat_before and path in flat_after:
            continue
        delta: float | None = None
        if (
            isinstance(old, (int, float))
            and isinstance(new, (int, float))
            and not isinstance(old, bool)
            and not isinstance(new, bool)
        ):
            delta = float(new) - float(old)
        changes.append(FieldChange(path=path, before=old, after=new, delta=delta))
    return changes


def diff_states(before: Any, after: Any) -> list[FieldChange]:
    """Field-level diff of two raw states; numeric deltas computed when possible."""
    return diff_flat(flatten_state(before), flatten_state(after))
