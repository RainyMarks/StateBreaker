"""Pure unit tests for executor helpers (no lab HTTP)."""

from __future__ import annotations

import pytest
from statebreaker.errors import PluginError
from statebreaker.models import AttackPlan, AttackSchedule, ResponseRecord

from statebreaker_race_executor.plugin import (
    RaceAttackExecutor,
    _evaluate_invariant_violation,
    _summarize,
)


def _plan_with_invariant(kind: str, parameters: dict, **options: object) -> AttackPlan:
    return AttackPlan(
        id="unit.plan",
        workflow_name="coupon-race-demo",
        attack_type="concurrent-replay",
        target_steps=["redeem-coupon"],
        schedule=AttackSchedule(concurrency=2, offsets_ms=[0.0, 0.0], options=dict(options)),
        invariant_ids=["coupon-max-delta"],
        metadata={
            "invariant": {
                "id": "coupon-max-delta",
                "kind": kind,
                "selector": "$.discount_yuan",
                "before_probe": "state-before",
                "after_probe": "state-after",
                "parameters": parameters,
            }
        },
    )


def _records(*codes: int) -> list[ResponseRecord]:
    return [
        ResponseRecord(
            correlation_id=f"c{i}",
            step_id="redeem-coupon",
            request_ordinal=i,
            status_code=code,
            elapsed_ms=1.0,
        )
        for i, code in enumerate(codes)
    ]


def test_max_delta_invariant_violation() -> None:
    plan = _plan_with_invariant("max-delta", {"max_delta": 50})
    violated, evidence = _evaluate_invariant_violation(
        plan,
        {"discount_yuan": 0},
        {"discount_yuan": 100},
        _records(200, 200),
    )
    assert violated is True
    assert evidence["observed_delta"] == 100


def test_max_delta_within_bound_is_not_violation() -> None:
    plan = _plan_with_invariant("max-delta", {"max_delta": 50})
    violated, evidence = _evaluate_invariant_violation(
        plan,
        {"discount_yuan": 0},
        {"discount_yuan": 50},
        _records(200, 409),
    )
    assert violated is False
    assert evidence["observed_delta"] == 50


def test_summarize_marks_heuristic_not_finding() -> None:
    plan = _plan_with_invariant("max-delta", {"max_delta": 50})
    plugin_data = _summarize(
        plan,
        _records(200, 200),
        {"discount_yuan": 0, "coupon_value": 50},
        {"discount_yuan": 100, "coupon_value": 50, "successful_redemptions": 2},
        [],
        [],
    )
    assert plugin_data["vulnerability_observed"] is True
    assert plugin_data["evaluation_mode"] == "invariant"
    assert plugin_data["is_formal_finding"] is False


@pytest.mark.asyncio
async def test_unsupported_attack_type_is_plugin_error() -> None:
    plan = AttackPlan(
        id="bad",
        workflow_name="x",
        attack_type="not-a-real-type",
        target_steps=["redeem-coupon"],
    )
    # Minimal workflow is validated by AttackPlan alone; execute needs runtime.workflow.
    from statebreaker.models import Workflow
    from statebreaker.runtime import ExecutionRuntime

    workflow = Workflow.model_validate(
        {
            "name": "x",
            "base_url": "http://example.test",
            "sessions": {"alice": {}},
            "steps": [
                {
                    "id": "redeem-coupon",
                    "session": "alice",
                    "request": {"method": "POST", "path": "/x"},
                }
            ],
        }
    )
    async with ExecutionRuntime(workflow) as runtime:
        with pytest.raises(PluginError, match="unsupported coupon attack type"):
            await RaceAttackExecutor().execute(plan, runtime)


def test_required_capability_is_enforced() -> None:
    import asyncio

    from statebreaker.models import Workflow
    from statebreaker.runtime import ExecutionRuntime

    plan = _plan_with_invariant(
        "max-delta",
        {"max_delta": 50},
        required_executor_capability="does-not-exist",
    )

    workflow = Workflow.model_validate(
        {
            "name": "coupon-race-demo",
            "base_url": "http://example.test",
            "sessions": {"default": {}},
            "steps": [
                {
                    "id": "redeem-coupon",
                    "request": {"method": "POST", "path": "/x"},
                }
            ],
        }
    )

    async def run() -> None:
        async with ExecutionRuntime(workflow) as runtime:
            await RaceAttackExecutor().execute(plan, runtime)

    with pytest.raises(PluginError, match="requires capability"):
        asyncio.run(run())
