"""Budget-aware plan scheduling helpers."""

from __future__ import annotations

from statebreaker.models.discovery import AttackPlan
from statebreaker.models.execution import ScanBudget


def estimate_requests(plan: AttackPlan, repetitions: int) -> int:
    """Rough request cost of a plan: setup + probes + fire, times repetitions."""
    setup = len(plan.setup_action_ids)
    probes = len(plan.state_probe_ids) * 2
    fire = len(plan.action_instances)
    per_trial = setup + probes + fire
    control = per_trial
    return control + per_trial * max(1, repetitions)


def affordable_plans(
    plans: list[AttackPlan],
    budget: ScanBudget,
    repetitions: int,
) -> list[AttackPlan]:
    """Keep as many top-priority plans as the request budget allows."""
    chosen: list[AttackPlan] = []
    spent = 0
    for plan in plans:
        cost = estimate_requests(plan, repetitions)
        if chosen and spent + cost > budget.maximum_requests:
            continue
        chosen.append(plan)
        spent += cost
    return chosen
