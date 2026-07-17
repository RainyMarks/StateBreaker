"""One class per candidate Invariant kind, orchestrated by the plugin's main loop.

Adding a new candidate rule kind means adding a new ``InvariantProposer`` and
listing it in ``DEFAULT_PROPOSERS`` -- the sampling and profiling stages never
need to change. ``single-use``, ``rate-limit``, ``uniqueness`` and ``ownership``
are intentionally not implemented here: none of them have an observable signal
in a single before/after probe pair without either repeating an action within
one baseline round (which starts to look like an attack, not a baseline) or
comparing against a session identity the runtime does not currently expose.
They are documented extension points, not stubs.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from statebreaker.models import Invariant

from .profiling import flatten
from .sampling import MIN_SAMPLE_SUPPORT, NormalSample, ProbePair


def _is_numeric(value: Any) -> bool:
    # bool is a subclass of int; treat it as categorical, not numeric.
    return isinstance(value, int | float) and not isinstance(value, bool)


def _slug(key: str) -> str:
    trimmed = key.removeprefix("$.")
    return re.sub(r"[^A-Za-z0-9]+", "-", trimmed).strip("-").lower() or "value"


def _paired_values(
    samples: list[NormalSample], pair: ProbePair, key: str
) -> list[tuple[Any, Any]]:
    """Same-round (before, after) values for one field, skipping incomplete rounds."""

    paired: list[tuple[Any, Any]] = []
    for sample in samples:
        before_body = sample.probes.get(pair.before_step)
        after_body = sample.probes.get(pair.after_step)
        if before_body is None or after_body is None:
            continue
        before_flat = flatten(before_body)
        after_flat = flatten(after_body)
        if key not in before_flat or key not in after_flat:
            continue
        paired.append((before_flat[key], after_flat[key]))
    return paired


class InvariantProposer(Protocol):
    kind: str

    def propose(
        self, *, key: str, pair: ProbePair, samples: list[NormalSample]
    ) -> Invariant | None: ...


class MaxDeltaProposer:
    """Numeric field whose observed increase is bounded, e.g. discount +50 max."""

    kind = "max-delta"

    def propose(
        self, *, key: str, pair: ProbePair, samples: list[NormalSample]
    ) -> Invariant | None:
        paired = _paired_values(samples, pair, key)
        if len(paired) < MIN_SAMPLE_SUPPORT:
            return None
        if not all(_is_numeric(before) and _is_numeric(after) for before, after in paired):
            return None
        deltas = [after - before for before, after in paired]
        if all(delta == 0 for delta in deltas):
            return None  # a field that never moves is not evidence of a bound
        max_delta = max(deltas)
        confidence = sum(1 for delta in deltas if delta == max_delta) / len(deltas)
        return Invariant(
            id=f"learned-max-delta-{_slug(key)}",
            kind=self.kind,
            selector=key,
            before_probe=pair.before_step,
            after_probe=pair.after_step,
            parameters={
                # Observed upper bound from baseline traffic — not a proven business ceiling.
                "max_delta": max_delta,
                "bound_source": "observed_maximum",
                "confidence": round(confidence, 3),
                "sample_count": len(deltas),
            },
            description=(
                f"在 {len(deltas)} 次正常采样中，{key} 从 {pair.before_step} 到 "
                f"{pair.after_step} 的观测最大变化量为 {max_delta}（非已证明的业务上界）"
            ),
        )


class MinValueProposer:
    """Numeric field never observed below zero, e.g. a balance or a counter."""

    kind = "min-value"

    def propose(
        self, *, key: str, pair: ProbePair, samples: list[NormalSample]
    ) -> Invariant | None:
        paired = _paired_values(samples, pair, key)
        if len(paired) < MIN_SAMPLE_SUPPORT:
            return None
        values = [value for observed in paired for value in observed]
        if not all(_is_numeric(value) for value in values):
            return None
        observed_min = min(values)
        if observed_min != 0:
            return None  # only claim the common, easily falsifiable floor of zero
        confidence = min(1.0, len(paired) / 10)
        return Invariant(
            id=f"learned-min-value-{_slug(key)}",
            kind=self.kind,
            selector=key,
            before_probe=pair.before_step,
            after_probe=pair.after_step,
            parameters={
                "min_value": 0,
                "bound_source": "observed_floor",
                "confidence": round(confidence, 3),
                "sample_count": len(paired),
            },
            description=f"在 {len(paired)} 次正常采样中，{key} 从未观察到负值（观测下界）",
        )


class StateTransitionProposer:
    """Low-cardinality field with a single, stable before -> after transition."""

    kind = "state-transition"

    def propose(
        self, *, key: str, pair: ProbePair, samples: list[NormalSample]
    ) -> Invariant | None:
        paired = _paired_values(samples, pair, key)
        if len(paired) < MIN_SAMPLE_SUPPORT:
            return None
        before_values = {before for before, _ in paired}
        after_values = {after for _, after in paired}
        if len(before_values) != 1 or len(after_values) != 1:
            return None
        (before_value,) = before_values
        (after_value,) = after_values
        if before_value == after_value:
            return None
        confidence = sum(
            1 for before, after in paired if before == before_value and after == after_value
        ) / len(paired)
        return Invariant(
            id=f"learned-state-transition-{_slug(key)}",
            kind=self.kind,
            selector=key,
            before_probe=pair.before_step,
            after_probe=pair.after_step,
            parameters={
                "from": before_value,
                "to": after_value,
                "confidence": round(confidence, 3),
                "sample_count": len(paired),
            },
            description=(
                f"在 {len(paired)} 次正常采样中，{key} 稳定从 {before_value!r} "
                f"变为 {after_value!r}"
            ),
        )


DEFAULT_PROPOSERS: tuple[InvariantProposer, ...] = (
    MaxDeltaProposer(),
    MinValueProposer(),
    StateTransitionProposer(),
)
