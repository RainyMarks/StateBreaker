"""Minimization unit tests: concurrency, scheduler, workflow ddmin, statistics.

Each behaviour has a normal group (search shrinks what it can) and an anomaly
group (search refuses to shrink below the working configuration).
"""

from __future__ import annotations

from statebreaker.minimization import (
    TrialSignal,
    ddmin,
    measure_run_statistics,
    minimize_concurrency,
    minimize_setup_steps,
    simplest_scheduler,
    trim_plan,
)
from statebreaker.models.discovery import ActionInstance, AttackPlan


def _plan(action_ids: list[str], *, setup: list[str] | None = None) -> AttackPlan:
    instances = [
        ActionInstance(instance_id=f"inst-{index}", action_id=action_id)
        for index, action_id in enumerate(action_ids)
    ]
    return AttackPlan(
        plan_id="plan-test",
        candidate_id="cand-test",
        action_instances=instances,
        concurrency=len(instances),
        setup_action_ids=list(setup or []),
    )


# -- trim_plan ---------------------------------------------------------------


def test_trim_plan_shrinks_same_action_burst() -> None:
    plan = _plan(["act"] * 8)
    trimmed = trim_plan(plan, 4)
    assert len(trimmed.action_instances) == 4
    assert trimmed.concurrency == 4


def test_trim_plan_never_drops_a_distinct_action() -> None:
    plan = _plan(["a", "b", "a", "a"])
    assert len(trim_plan(plan, 3).action_instances) == 3
    two = trim_plan(plan, 2)
    assert {instance.action_id for instance in two.action_instances} == {"a", "b"}
    # cannot go below the number of distinct actions
    assert len(trim_plan(plan, 1).action_instances) == 4


# -- minimize_concurrency ----------------------------------------------------


async def test_minimize_concurrency_halves_while_triggering() -> None:
    plan = _plan(["act"] * 8)

    async def triggers(variant: AttackPlan) -> bool:
        return len(variant.action_instances) >= 4

    minimized = await minimize_concurrency(triggers, plan)
    assert len(minimized.action_instances) == 4


async def test_minimize_concurrency_keeps_original_when_shrink_fails() -> None:
    plan = _plan(["act"] * 8)

    async def triggers(variant: AttackPlan) -> bool:
        return len(variant.action_instances) == 8

    minimized = await minimize_concurrency(triggers, plan)
    assert len(minimized.action_instances) == 8


# -- simplest_scheduler ------------------------------------------------------


async def test_simplest_scheduler_prefers_plain_concurrency() -> None:
    async def triggers(scheduler: str) -> bool:
        return scheduler in {"async-http", "http1-last-byte"}

    chosen = await simplest_scheduler(triggers, ["async-http", "http1-last-byte"])
    assert chosen == "async-http"


async def test_simplest_scheduler_returns_none_when_nothing_triggers() -> None:
    async def never(scheduler: str) -> bool:
        return False

    chosen = await simplest_scheduler(never, ["async-http", "http1-last-byte"])
    assert chosen is None


# -- ddmin / setup minimization ----------------------------------------------


async def test_ddmin_shrinks_to_the_needed_step() -> None:
    async def still_triggers(subset: list[str]) -> bool:
        return "b" in subset

    kept = await ddmin(["a", "b", "c", "d"], still_triggers)
    assert kept == ["b"]


async def test_ddmin_keeps_everything_when_all_steps_matter() -> None:
    async def still_triggers(subset: list[str]) -> bool:
        return len(subset) == 4

    kept = await ddmin(["a", "b", "c", "d"], still_triggers)
    assert kept == ["a", "b", "c", "d"]


async def test_minimize_setup_steps_updates_the_plan() -> None:
    plan = _plan(["act", "act"], setup=["login", "seed", "noise"])

    async def triggers(variant: AttackPlan) -> bool:
        return "seed" in variant.setup_action_ids

    minimized = await minimize_setup_steps(triggers, plan)
    assert minimized.setup_action_ids == ["seed"]


async def test_minimize_setup_steps_noop_without_setup() -> None:
    plan = _plan(["act", "act"])

    async def triggers(variant: AttackPlan) -> bool:
        raise AssertionError("must not fire trials when there is nothing to minimize")

    minimized = await minimize_setup_steps(triggers, plan)
    assert minimized is plan


# -- statistics ---------------------------------------------------------------


async def test_measure_run_statistics_aggregates_rounds() -> None:
    outcomes = [
        TrialSignal(triggered=index % 2 == 0, release_skew_ms=0.2 + index * 0.01, elapsed_ms=5.0)
        for index in range(10)
    ]
    cursor = {"index": 0}

    async def run_once() -> TrialSignal:
        signal = outcomes[cursor["index"]]
        cursor["index"] += 1
        return signal

    stats = await measure_run_statistics(run_once, rounds=10)
    assert stats.rounds == 10
    assert stats.successes == 5
    assert stats.success_rate == 0.5
    assert stats.median_release_skew_ms > 0
    assert stats.mean_trigger_time_ms == 5.0
