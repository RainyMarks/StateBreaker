"""State normalization: separate stable business fields from volatile noise.

Volatility is learned from samples, not hardcoded: a field that changes
between two observations of the *same* state (control probes with no action
in between) carries no business meaning and is ignored in comparisons.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from statebreaker.intelligence.jsondiff import flatten_state
from statebreaker.models.state import NormalizedState


class StateNormalizer:
    """Normalizes raw probe bodies by dropping learned-unstable fields."""

    def __init__(self, volatile_paths: Iterable[str] = ()) -> None:
        self._volatile_paths = set(volatile_paths)

    @property
    def volatile_paths(self) -> set[str]:
        return set(self._volatile_paths)

    @classmethod
    def from_control_samples(cls, samples: list[Any]) -> StateNormalizer:
        """Learn unstable fields from repeated observations of unchanged state.

        A path is volatile if its value differs between any two consecutive
        control samples, or it always holds a volatile-looking value type
        (timestamps, per-call tokens) that is never equal twice.
        """
        flattened = [flatten_state(sample) for sample in samples]
        volatile: set[str] = set()
        for earlier, later in zip(flattened, flattened[1:], strict=False):
            for path in set(earlier) | set(later):
                if earlier.get(path) != later.get(path):
                    volatile.add(path)
        return cls(volatile)

    def normalize(self, raw_state: object) -> NormalizedState:
        flat = flatten_state(raw_state)
        kept = {
            path: value
            for path, value in flat.items()
            if path not in self._volatile_paths
        }
        ignored = sorted(path for path in flat if path in self._volatile_paths)
        return NormalizedState(fields=kept, ignored_paths=ignored)
