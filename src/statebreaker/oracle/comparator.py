"""Trial effect summarization and control-vs-attack comparison (spec §13.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from statebreaker.baseline.effect_model import response_signature
from statebreaker.intelligence.jsondiff import diff_flat
from statebreaker.models.execution import ExecutionTrial

_EPSILON = 1e-9


@dataclass
class EffectSummary:
    """The business-side effect of one trial, in normalized-state terms."""

    numeric_deltas: dict[str, float] = field(default_factory=dict)
    after_values: dict[str, Any] = field(default_factory=dict)
    before_values: dict[str, Any] = field(default_factory=dict)
    transitions: dict[str, list[tuple[Any, Any]]] = field(default_factory=dict)
    changed_paths: set[str] = field(default_factory=set)
    response_classes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def side_effect_count(self) -> float:
        """One number: |numeric deltas| + number of transitions."""
        return sum(abs(delta) for delta in self.numeric_deltas.values()) + sum(
            len(pairs) for pairs in self.transitions.values()
        )


def summarize_trial(trial: ExecutionTrial) -> EffectSummary:
    summary = EffectSummary()
    for before, after in zip(trial.before_state, trial.after_state, strict=False):
        if before.normalized is None or after.normalized is None:
            continue
        prior = before.normalized.fields
        later = after.normalized.fields
        for change in diff_flat(prior, later):
            summary.changed_paths.add(change.path)
            if change.delta is not None:
                summary.numeric_deltas[change.path] = (
                    summary.numeric_deltas.get(change.path, 0.0) + change.delta
                )
            else:
                summary.transitions.setdefault(change.path, []).append(
                    (change.before, change.after)
                )
        for path, value in prior.items():
            summary.before_values.setdefault(path, value)
        for path, value in later.items():
            summary.after_values[path] = value
    for response in trial.responses:
        if response.error:
            summary.errors.append(response.error)
        else:
            signature = response_signature(response.status, response.body)
            summary.response_classes.append(signature.semantic_class or "")
    return summary


@dataclass
class Comparison:
    """Where the attack diverges from its sequential control group."""

    exceeded_numeric: dict[str, tuple[float, float]] = field(default_factory=dict)
    extra_transitions: dict[str, list[tuple[Any, Any]]] = field(default_factory=dict)
    attack_successes: int = 0
    control_successes: int = 0

    @property
    def has_state_anomaly(self) -> bool:
        return bool(self.exceeded_numeric or self.extra_transitions)

    @property
    def has_response_anomaly(self) -> bool:
        return self.attack_successes > self.control_successes


def compare_effects(control: EffectSummary, attack: EffectSummary) -> Comparison:
    """Baseline-vs-attack contrast (spec §13.2: sequential vs concurrent)."""
    comparison = Comparison()
    for path, attack_delta in attack.numeric_deltas.items():
        control_delta = control.numeric_deltas.get(path, 0.0)
        if abs(attack_delta) > abs(control_delta) + _EPSILON:
            comparison.exceeded_numeric[path] = (control_delta, attack_delta)
    for path, pairs in attack.transitions.items():
        control_pairs = control.transitions.get(path, [])
        extra = [pair for pair in pairs if pair not in control_pairs]
        if extra:
            comparison.extra_transitions[path] = extra

    if control.response_classes:
        control_success_class = control.response_classes[0]
        comparison.control_successes = control.response_classes.count(control_success_class)
        comparison.attack_successes = attack.response_classes.count(control_success_class)
    return comparison
