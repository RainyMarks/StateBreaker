"""Verdict engine: turn control/attack evidence into findings (spec §13.3)."""

from __future__ import annotations

from statebreaker.models.discovery import RaceCandidate
from statebreaker.models.execution import ExecutionTrial
from statebreaker.models.findings import Finding, Verdict
from statebreaker.models.state import LearnedInvariant
from statebreaker.oracle.comparator import (
    Comparison,
    EffectSummary,
    compare_effects,
    summarize_trial,
)


class TrialEvidence:
    """What one attack trial showed, relative to its control group."""

    def __init__(
        self,
        trial: ExecutionTrial,
        comparison: Comparison,
        invariant_violations: list[str],
        state_usable: bool,
    ) -> None:
        self.trial = trial
        self.comparison = comparison
        self.invariant_violations = invariant_violations
        self.state_usable = state_usable

    @property
    def state_anomaly(self) -> bool:
        return self.state_usable and (
            self.comparison.has_state_anomaly or bool(self.invariant_violations)
        )

    @property
    def response_anomaly(self) -> bool:
        return self.comparison.has_response_anomaly


def check_invariants(
    invariants: list[LearnedInvariant],
    attack: EffectSummary,
    control: EffectSummary,
) -> list[str]:
    """Which learned rules did the attack break?"""
    violations: list[str] = []
    for invariant in invariants:
        for path in invariant.target_paths:
            if invariant.invariant_type == "numeric_bound":
                ceiling = float(invariant.parameters.get("max_abs_delta", 0.0))
                if abs(attack.numeric_deltas.get(path, 0.0)) > ceiling + 1e-9:
                    violations.append(invariant.invariant_id)
            elif invariant.invariant_type == "lower_bound":
                floor = float(invariant.parameters.get("min_observed", 0.0))
                after = attack.after_values.get(path)
                if (
                    isinstance(after, (int, float))
                    and not isinstance(after, bool)
                    and float(after) < floor - 1e-9
                ):
                    violations.append(invariant.invariant_id)
            elif invariant.invariant_type == "one_shot":
                attack_delta = abs(attack.numeric_deltas.get(path, 0.0))
                control_delta = abs(control.numeric_deltas.get(path, 0.0))
                if attack_delta > control_delta + 1e-9 and control_delta > 0:
                    violations.append(invariant.invariant_id)
            elif invariant.invariant_type == "state_transition":
                allowed = invariant.parameters.get("allowed_transitions", {})
                for old, new in attack.transitions.get(path, []):
                    if str(new) not in (allowed.get(str(old)) or []):
                        violations.append(invariant.invariant_id)
    return sorted(set(violations))


def evaluate_trial(
    invariants: list[LearnedInvariant],
    control_summary: EffectSummary,
    attack: ExecutionTrial,
) -> TrialEvidence:
    attack_summary = summarize_trial(attack)
    comparison = compare_effects(control_summary, attack_summary)
    violations = check_invariants(invariants, attack_summary, control_summary)
    state_usable = bool(attack.before_state) and any(
        snapshot.normalized is not None for snapshot in attack.before_state
    )
    return TrialEvidence(attack, comparison, violations, state_usable)


def evaluate_candidate(
    candidate: RaceCandidate,
    invariants: list[LearnedInvariant],
    control: ExecutionTrial,
    attacks: list[ExecutionTrial],
    *,
    plan_id: str = "",
    require_state_evidence: bool = True,
) -> Finding:
    """Aggregate per-trial evidence into one verdict (spec §13.3/§13.4)."""
    control_summary = summarize_trial(control)
    evidences = [
        evaluate_trial(invariants, control_summary, attack) for attack in attacks
    ]

    state_hits = [e for e in evidences if e.state_anomaly]
    response_hits = [e for e in evidences if e.response_anomaly]
    usable = [e for e in evidences if e.state_usable]

    explanation: list[str] = []
    violated: list[str] = []
    if state_hits:
        best = state_hits[0]
        for path, (control_delta, attack_delta) in best.comparison.exceeded_numeric.items():
            explanation.append(
                f"concurrent effect on {path} = {attack_delta:g} exceeds "
                f"sequential control = {control_delta:g}"
            )
        for path, pairs in best.comparison.extra_transitions.items():
            explanation.append(f"unexpected transitions on {path}: {pairs}")
        for evidence in state_hits:
            violated.extend(evidence.invariant_violations)
        for invariant_id in sorted(set(violated)):
            explanation.append(f"violated learned rule: {invariant_id}")
    elif response_hits:
        best = response_hits[0]
        explanation.append(
            f"{best.comparison.attack_successes} concurrent responses matched the "
            f"success class vs {best.comparison.control_successes} in the control group"
        )

    success_rate = len(state_hits or response_hits) / len(attacks) if attacks else 0.0

    if not usable:
        verdict: Verdict = "inconclusive"
        confidence = 0.2
        explanation.append("no usable state probes; evidence is response-only")
    elif state_hits:
        verdict = "confirmed"
        confidence = min(0.7 + 0.05 * len(state_hits), 0.99)
    elif response_hits and not require_state_evidence:
        verdict = "confirmed"
        confidence = 0.6
        explanation.append("confirmed on response semantics only (state evidence disabled)")
    elif response_hits:
        verdict = "probable"
        confidence = 0.5
        explanation.append("state evidence missing or unchanged; response anomaly only")
    else:
        verdict = "rejected"
        confidence = 0.8
        explanation.append("concurrent group matches the sequential control group")

    trial_refs = [control.trial_id] + [attack.trial_id for attack in attacks]
    return Finding(
        finding_id=f"finding-{plan_id or candidate.candidate_id}",
        verdict=verdict,
        confidence=round(confidence, 2),
        candidate=candidate,
        evidence_refs=trial_refs,
        explanation=explanation,
        violated_invariant_ids=sorted(set(violated)),
        success_rate=round(success_rate, 3),
    )
