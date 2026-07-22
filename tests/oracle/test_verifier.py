"""Oracle verdict tests for response-only evidence."""

from __future__ import annotations

from statebreaker.models.discovery import RaceCandidate
from statebreaker.models.execution import ExecutionTrial, HttpResponseRecord
from statebreaker.oracle.verifier import evaluate_candidate


def _trial(
    trial_id: str,
    role: str,
    bodies: list[dict[str, object]],
) -> ExecutionTrial:
    return ExecutionTrial(
        trial_id=trial_id,
        control_or_attack=role,  # type: ignore[arg-type]
        responses=[
            HttpResponseRecord(instance_id=f"inst-{index}", status=200, body=body)
            for index, body in enumerate(bodies, start=1)
        ],
    )


def test_explicit_success_response_marker_confirms_without_state_probe() -> None:
    candidate = RaceCandidate(
        candidate_id="cand-response-marker",
        kind="same_action",
        action_ids=["act"],
    )
    control = _trial(
        "control",
        "control",
        [{"attack_achieved": False}, {"attack_achieved": False}],
    )
    attack = _trial(
        "attack",
        "attack",
        [{"attack_achieved": True}, {"attack_achieved": True}],
    )

    finding = evaluate_candidate(candidate, [], control, [attack], plan_id="plan")

    assert finding.verdict == "confirmed"
    assert "explicit application response marker" in " ".join(finding.explanation)


def test_repeated_response_value_is_probable_without_state_probe() -> None:
    candidate = RaceCandidate(
        candidate_id="cand-stale-response",
        kind="same_action",
        action_ids=["act"],
    )
    control = _trial(
        "control",
        "control",
        [
            {"output": "Initial Balance: $100.00\nFinal Balance: $90.00\n"},
            {"output": "Initial Balance: $90.00\nFinal Balance: $80.00\n"},
        ],
    )
    attack = _trial(
        "attack",
        "attack",
        [
            {"output": "Initial Balance: $100.00\nFinal Balance: $90.00\n"},
            {"output": "Initial Balance: $100.00\nFinal Balance: $90.00\n"},
        ],
    )

    finding = evaluate_candidate(candidate, [], control, [attack], plan_id="plan")

    assert finding.verdict == "probable"
    assert "repeated stale value" in " ".join(finding.explanation)
