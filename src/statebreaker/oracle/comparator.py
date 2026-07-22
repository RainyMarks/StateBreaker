"""Trial effect summarization and control-vs-attack comparison (spec §13.2)."""

from __future__ import annotations

import re
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
    explicit_success_markers: int = 0
    response_value_series: dict[str, list[str]] = field(default_factory=dict)
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
            if _has_explicit_success_marker(response.body):
                summary.explicit_success_markers += 1
            for key, value in _response_observations(response.body).items():
                summary.response_value_series.setdefault(key, []).append(value)
    return summary


@dataclass
class Comparison:
    """Where the attack diverges from its sequential control group."""

    exceeded_numeric: dict[str, tuple[float, float]] = field(default_factory=dict)
    extra_transitions: dict[str, list[tuple[Any, Any]]] = field(default_factory=dict)
    attack_successes: int = 0
    control_successes: int = 0
    attack_explicit_successes: int = 0
    control_explicit_successes: int = 0
    stale_response_values: dict[str, tuple[list[str], list[str]]] = field(default_factory=dict)

    @property
    def has_state_anomaly(self) -> bool:
        return bool(self.exceeded_numeric or self.extra_transitions)

    @property
    def has_response_anomaly(self) -> bool:
        return (
            self.attack_successes > self.control_successes
            or self.attack_explicit_successes > self.control_explicit_successes
            or bool(self.stale_response_values)
        )


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
    comparison.control_explicit_successes = control.explicit_success_markers
    comparison.attack_explicit_successes = attack.explicit_success_markers
    for path, attack_values in attack.response_value_series.items():
        control_values = control.response_value_series.get(path, [])
        if len(attack_values) < 2 or len(control_values) < 2:
            continue
        if len(set(attack_values)) < len(set(control_values)):
            comparison.stale_response_values[path] = (control_values, attack_values)
    return comparison


_SUCCESS_KEY_PARTS = ("achiev", "success", "unlocked", "race", "attack")


def _has_explicit_success_marker(body: Any) -> bool:
    if isinstance(body, dict):
        for key, value in body.items():
            key_text = str(key).lower()
            if value is True and any(part in key_text for part in _SUCCESS_KEY_PARTS):
                return True
            if _has_explicit_success_marker(value):
                return True
    if isinstance(body, list):
        return any(_has_explicit_success_marker(item) for item in body)
    return False


_LABELED_NUMBER = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 _./:-]{2,80}?)\s*:\s*[$€£]?\s*"
    r"(?P<number>-?\d[\d,]*(?:\.\d+)?)\s*$"
)


def _response_observations(body: Any) -> dict[str, str]:
    observations: dict[str, str] = {}
    _collect_response_observations(body, observations, prefix="")
    return observations


def _collect_response_observations(
    body: Any,
    observations: dict[str, str],
    *,
    prefix: str,
) -> None:
    if isinstance(body, dict):
        for key, value in body.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            _collect_response_observations(value, observations, prefix=child_prefix)
        return
    if isinstance(body, list):
        for index, value in enumerate(body):
            _collect_response_observations(value, observations, prefix=f"{prefix}[{index}]")
        return
    if isinstance(body, str):
        for line in body.splitlines():
            match = _LABELED_NUMBER.match(line)
            if match is None:
                continue
            label = " ".join(match.group("label").lower().split())
            observations[f"{prefix}:{label}"] = match.group("number").replace(",", "")
