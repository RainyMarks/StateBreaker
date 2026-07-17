"""Minimal verifier: compare before/after state against Invariant rules."""

from __future__ import annotations

import re
from typing import Any

from statebreaker.errors import PluginError
from statebreaker.models import (
    Finding,
    FindingVerdict,
    Invariant,
    PluginManifest,
    RawAttackResult,
    ResponseRecord,
)


class BasicVerifierPlugin:
    """Produce formal Findings from attack evidence and invariants."""

    manifest = PluginManifest(
        plugin_id="team.basic-verifier",
        name="Basic state-evidence verifier",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.verifier",
        capabilities=[
            "max-delta",
            "min-value",
            "count-limit",
            "single-use",
            "state-transition",
            "state-evidence",
        ],
        description=(
            "Evaluates invariants against before/after state snapshots and response "
            "evidence. Emits confirmed / probable / rejected Findings."
        ),
    )

    async def verify(
        self,
        result: RawAttackResult,
        invariants: list[Invariant],
    ) -> list[Finding]:
        if not invariants:
            raise PluginError("basic verifier requires at least one invariant")

        findings: list[Finding] = []
        evidence_refs = _evidence_refs(result)
        for invariant in sorted(invariants, key=lambda item: item.id):
            findings.append(_finding_for_invariant(result, invariant, evidence_refs))
        return findings


def _evidence_refs(result: RawAttackResult) -> list[str]:
    refs: list[str] = []
    for record in result.responses:
        refs.append(f"response:{record.correlation_id}")
    for event in result.events:
        refs.append(f"event:{event.event_id}")
    if result.before_state:
        refs.append("state:before")
    if result.after_state:
        refs.append("state:after")
    # Cap length while keeping deterministic order.
    return refs[:32]


def _finding_for_invariant(
    result: RawAttackResult,
    invariant: Invariant,
    evidence_refs: list[str],
) -> Finding:
    violated, evaluable, details = _evaluate(result, invariant)
    heuristic = bool(result.plugin_data.get("vulnerability_observed"))
    success_count = sum(1 for item in result.responses if 200 <= item.status_code < 300)

    if evaluable and violated:
        verdict = FindingVerdict.CONFIRMED
        title = f"Invariant {invariant.id} violated by state evidence"
    elif evaluable and not violated:
        verdict = FindingVerdict.REJECTED
        title = f"Invariant {invariant.id} held under observed state"
    elif heuristic or success_count > 1:
        verdict = FindingVerdict.PROBABLE
        title = f"Invariant {invariant.id} suspicious without full state proof"
    else:
        verdict = FindingVerdict.REJECTED
        title = f"Invariant {invariant.id} not confirmed (insufficient evidence)"

    details = {
        **details,
        "attack_plan_id": result.attack_plan_id,
        "run_id": result.run_id,
        "evaluable": evaluable,
        "violated": violated,
        "heuristic_vulnerability_observed": heuristic,
        "successful_responses": success_count,
        "verifier": "team.basic-verifier",
    }
    return Finding(
        id=f"finding.{invariant.id}",
        verdict=verdict,
        title=title,
        invariant_id=invariant.id,
        evidence_refs=evidence_refs,
        details=details,
    )


def _evaluate(
    result: RawAttackResult,
    invariant: Invariant,
) -> tuple[bool, bool, dict[str, Any]]:
    """Return (violated, evaluable, details)."""

    selector = invariant.selector
    before_value = _select_value(result.before_state, selector)
    after_value = _select_value(result.after_state, selector)
    parameters = dict(invariant.parameters)
    details: dict[str, Any] = {
        "kind": invariant.kind,
        "selector": selector,
        "before_value": before_value,
        "after_value": after_value,
        "parameters": parameters,
        "description": invariant.description,
    }

    kind = invariant.kind
    if kind == "max-delta":
        max_delta = parameters.get("max_delta")
        if not _is_numeric(max_delta):
            return False, False, {**details, "reason": "max_delta missing or non-numeric"}
        if not _is_numeric(after_value):
            return False, False, {**details, "reason": "after value not numeric"}
        start = float(before_value) if _is_numeric(before_value) else 0.0
        delta = float(after_value) - start
        details["observed_delta"] = delta
        return delta > float(max_delta), True, details

    if kind == "min-value":
        min_value = parameters.get("min_value", 0)
        if not _is_numeric(min_value):
            return False, False, {**details, "reason": "min_value missing or non-numeric"}
        values = [float(v) for v in (before_value, after_value) if _is_numeric(v)]
        if not values:
            return False, False, {**details, "reason": "no numeric values for selector"}
        observed_min = min(values)
        details["observed_min"] = observed_min
        return observed_min < float(min_value), True, details

    if kind == "count-limit":
        limit = parameters.get("max_count", parameters.get("limit", parameters.get("max_delta")))
        if not _is_numeric(limit):
            return False, False, {**details, "reason": "count limit parameter missing"}
        if _is_numeric(after_value) and _is_numeric(before_value):
            observed = float(after_value) - float(before_value)
        elif _is_numeric(after_value):
            observed = float(after_value)
        else:
            observed = float(_success_count(result.responses))
        details["observed_count"] = observed
        return observed > float(limit), True, details

    if kind == "single-use":
        successes = _success_count(result.responses)
        details["successful_target_responses"] = successes
        if _is_numeric(before_value) and _is_numeric(after_value):
            numeric_delta = float(after_value) - float(before_value)
            details["numeric_delta"] = numeric_delta
            return (numeric_delta > 1 or successes > 1), True, details
        if successes > 0:
            return successes > 1, True, details
        return False, False, {**details, "reason": "no usable single-use signal"}

    if kind == "state-transition":
        expected_from = parameters.get("from")
        expected_to = parameters.get("to")
        if expected_from is None and expected_to is None:
            return False, False, {**details, "reason": "from/to parameters missing"}
        # Violation = observed transition differs from the single allowed normal change,
        # or multi-success while holding the transition (side-effect race).
        unexpected_before = (
            before_value is not None
            and expected_from is not None
            and before_value != expected_from
        )
        unexpected_after = (
            after_value is not None and expected_to is not None and after_value != expected_to
        )
        multi_success = _success_count(result.responses) > 1
        details["unexpected_before"] = unexpected_before
        details["unexpected_after"] = unexpected_after
        details["multi_success"] = multi_success
        if unexpected_before or unexpected_after:
            return True, True, details
        # Transition matched; still flag multi-success as probable path via caller
        # when not fully evaluable — here treat multi-success as violation of
        # "single transition once" intent when both endpoints known.
        if before_value is not None and after_value is not None:
            return multi_success, True, details
        return False, False, {**details, "reason": "incomplete transition endpoints"}

    return False, False, {**details, "reason": f"unsupported invariant kind: {kind}"}


def _success_count(responses: list[ResponseRecord]) -> int:
    return sum(1 for record in responses if 200 <= record.status_code < 300)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _select_value(state: dict[str, Any], selector: str) -> Any:
    if not state or not selector:
        return None
    path = selector.strip()
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:].lstrip(".")
    if not path:
        return state

    current: Any = state
    for part in path.split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?", part)
        if match is None:
            return None
        key, index = match.group(1), match.group(2)
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
        if index is not None:
            if not isinstance(current, list):
                return None
            position = int(index)
            if position < 0 or position >= len(current):
                return None
            current = current[position]
    return current
