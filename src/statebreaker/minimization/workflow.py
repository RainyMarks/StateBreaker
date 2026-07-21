"""Workflow minimization: delta-debug setup steps a confirmed race needs (§14.2).

Attack instances and state probes are never removed — only the setup chain
(``setup_action_ids``) is delta-debugged, and each candidate subset is checked
with a real trial through the injected ``triggers`` predicate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from statebreaker.models.discovery import AttackPlan

SubsetCheck = Callable[[list[str]], Awaitable[bool]]
TriggerCheck = Callable[[AttackPlan], Awaitable[bool]]


async def ddmin(
    items: Sequence[str],
    test: SubsetCheck,
    *,
    max_tests: int = 12,
) -> list[str]:
    """Classic delta debugging: shrink ``items`` while ``test`` keeps passing.

    ``test(subset)`` must return True when the phenomenon still occurs with
    only ``subset`` present. The number of real tests is capped by
    ``max_tests`` so the search respects the scan budget.
    """
    current = list(items)
    tests_run = 0
    granularity = 2
    while len(current) >= 2 and tests_run < max_tests:
        chunk = max(1, len(current) // granularity)
        reduced = False
        for start in range(0, len(current), chunk):
            subset = current[:start] + current[start + chunk :]
            if not subset:
                continue
            tests_run += 1
            if await test(subset):
                current = subset
                granularity = max(2, granularity - 1)
                reduced = True
                break
            if tests_run >= max_tests:
                break
        if not reduced:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)
    return current


def plan_with_setup(plan: AttackPlan, setup_action_ids: list[str]) -> AttackPlan:
    return plan.model_copy(update={"setup_action_ids": list(setup_action_ids)})


async def minimize_setup_steps(
    triggers: TriggerCheck,
    plan: AttackPlan,
    *,
    max_tests: int = 12,
) -> AttackPlan:
    """Drop setup steps that the confirmed race does not depend on."""
    if not plan.setup_action_ids:
        return plan
    kept = await ddmin(
        plan.setup_action_ids,
        lambda subset: triggers(plan_with_setup(plan, subset)),
        max_tests=max_tests,
    )
    return plan_with_setup(plan, kept)
