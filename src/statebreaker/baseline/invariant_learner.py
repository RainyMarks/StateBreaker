"""Learn candidate business invariants from baseline experiments.

Every learned rule carries the trial ids that support it — never hand-written,
never business-specific (spec §8.4).
"""

from __future__ import annotations

from statebreaker.models.execution import ExecutionTrial
from statebreaker.models.state import LearnedInvariant, OperationEffect


def learn_invariants(
    effects: list[OperationEffect],
    trials: list[ExecutionTrial],
) -> list[LearnedInvariant]:
    """Derive candidate invariants from learned effects and their trials."""
    invariants: list[LearnedInvariant] = []

    for effect in effects:
        if effect.repeat_behavior == "once":
            invariants.append(
                LearnedInvariant(
                    invariant_id=f"inv-oneshot-{effect.action_id}",
                    invariant_type="one_shot",
                    target_paths=[change.path for change in effect.state_changes],
                    parameters={
                        "action_id": effect.action_id,
                        "meaning": (
                            "sequential repetition produces no further state change; "
                            "concurrent repetition must not either"
                        ),
                    },
                    confidence=0.8,
                    supporting_trials=list(effect.supporting_trial_ids),
                )
            )

    numeric_deltas: dict[str, list[float]] = {}
    numeric_after_values: dict[str, list[float]] = {}
    transitions: dict[str, set[tuple[str, str]]] = {}
    transition_trials: dict[str, list[str]] = {}

    for trial in trials:
        for before, after in zip(trial.before_state, trial.after_state, strict=False):
            if before.normalized is None or after.normalized is None:
                continue
            prior = before.normalized.fields
            later = after.normalized.fields
            for path in set(prior) & set(later):
                old, new = prior[path], later[path]
                if (
                    isinstance(old, (int, float))
                    and isinstance(new, (int, float))
                    and not isinstance(old, bool)
                    and not isinstance(new, bool)
                ):
                    numeric_deltas.setdefault(path, []).append(abs(float(new) - float(old)))
                    numeric_after_values.setdefault(path, []).append(float(new))
                elif isinstance(old, str) and isinstance(new, str) and old != new:
                    transitions.setdefault(path, set()).add((old, new))
                    transition_trials.setdefault(path, [])
                    if trial.trial_id not in transition_trials[path]:
                        transition_trials[path].append(trial.trial_id)

    for path, deltas in sorted(numeric_deltas.items()):
        if not deltas:
            continue
        invariants.append(
            LearnedInvariant(
                invariant_id=f"inv-numeric-{len(invariants)}",
                invariant_type="numeric_bound",
                target_paths=[path],
                parameters={"max_abs_delta": max(deltas)},
                confidence=0.7,
                supporting_trials=[t.trial_id for t in trials],
            )
        )
        after_values = numeric_after_values.get(path, [])
        if after_values:
            invariants.append(
                LearnedInvariant(
                    invariant_id=f"inv-lower-{len(invariants)}",
                    invariant_type="lower_bound",
                    target_paths=[path],
                    parameters={"min_observed": min(after_values)},
                    confidence=0.6,
                    supporting_trials=[t.trial_id for t in trials],
                )
            )

    for path, pairs in sorted(transitions.items()):
        allowed: dict[str, list[str]] = {}
        for old, new in sorted(pairs):
            allowed.setdefault(old, []).append(new)
        invariants.append(
            LearnedInvariant(
                invariant_id=f"inv-transition-{len(invariants)}",
                invariant_type="state_transition",
                target_paths=[path],
                parameters={"allowed_transitions": allowed},
                confidence=0.7,
                supporting_trials=transition_trials.get(path, []),
            )
        )

    return invariants


def invariant_index(invariants: list[LearnedInvariant]) -> dict[str, LearnedInvariant]:
    return {invariant.invariant_id: invariant for invariant in invariants}
