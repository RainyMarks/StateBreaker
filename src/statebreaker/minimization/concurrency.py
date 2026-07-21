"""Concurrency minimization: 16 -> 8 -> 4 -> 2 until the race stops (spec §14.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from statebreaker.models.discovery import AttackPlan

TriggerCheck = Callable[[AttackPlan], Awaitable[bool]]


def trim_plan(plan: AttackPlan, concurrency: int) -> AttackPlan:
    """Shrink a plan to ``concurrency`` instances.

    The first instance of every distinct action is always kept (a cross-action
    plan stays meaningful); duplicate instances of the dominant action are
    dropped from the end. Offsets keep their per-instance alignment.
    """
    instances = plan.action_instances
    if concurrency >= len(instances):
        return plan
    first_index: dict[str, int] = {}
    for index, instance in enumerate(instances):
        first_index.setdefault(instance.action_id, index)
    keep = set(first_index.values())
    if concurrency < len(keep):
        return plan
    for index in range(len(instances) - 1, -1, -1):
        if len(keep) >= concurrency:
            break
        keep.add(index)
    ordered = sorted(keep)
    offsets = plan.offsets_ms
    new_offsets = [offsets[i] for i in ordered] if len(offsets) == len(instances) else []
    return plan.model_copy(
        update={
            "action_instances": [instances[i] for i in ordered],
            "concurrency": len(ordered),
            "offsets_ms": new_offsets,
        }
    )


async def minimize_concurrency(
    triggers: TriggerCheck,
    plan: AttackPlan,
    *,
    floor: int = 2,
) -> AttackPlan:
    """Halve the instance count while the race still triggers."""
    best = plan
    candidate_size = len(plan.action_instances) // 2
    while candidate_size >= floor:
        variant = trim_plan(best, candidate_size)
        if len(variant.action_instances) == len(best.action_instances):
            break
        if await triggers(variant):
            best = variant
            candidate_size //= 2
        else:
            break
    return best
