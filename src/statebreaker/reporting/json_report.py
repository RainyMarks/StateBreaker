"""JSON evidence bundle for one finding (spec §15.1)."""

from __future__ import annotations

from typing import Any

from statebreaker.models.discovery import AttackPlan
from statebreaker.models.execution import ExecutionTrial
from statebreaker.models.findings import Finding


def _trial_summary(trial: ExecutionTrial) -> dict[str, Any]:
    return {
        "trial_id": trial.trial_id,
        "role": trial.control_or_attack,
        "plan_id": trial.plan_id,
        "response_statuses": [response.status for response in trial.responses],
        "response_bodies": [response.body for response in trial.responses],
        "before_state": [snapshot.normalized or snapshot.raw for snapshot in trial.before_state],
        "after_state": [snapshot.normalized or snapshot.raw for snapshot in trial.after_state],
        "timeline": [
            {"instance_id": event.instance_id, "event": event.event, "at_ns": event.at_ns}
            for event in trial.timeline
        ],
    }


def build_json_report(
    finding: Finding,
    plan: AttackPlan,
    control: ExecutionTrial | None,
    attacks: list[ExecutionTrial],
) -> dict[str, Any]:
    """Portable evidence: verdict, minimized plan, control vs attack trials."""
    return {
        "schema_version": finding.schema_version,
        "finding": finding.model_dump(mode="json"),
        "minimized_plan": plan.model_dump(mode="json"),
        "control_group": _trial_summary(control) if control else None,
        "attack_group": [_trial_summary(trial) for trial in attacks],
    }
