from __future__ import annotations

from pathlib import Path

import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import Invariant, Workflow

from statebreaker_race_generator.plugin import (
    BusinessLogicAttackGenerator,
    GenericInvariantReplayGenerator,
    PaymentCallbackIdempotencyGenerator,
    RefundFulfillRaceGenerator,
)

ROOT = Path(__file__).resolve().parents[2]
CALLBACK_WORKFLOW = ROOT / "examples" / "payment-callback-idempotency" / "workflow.yaml"
CALLBACK_INVARIANTS = ROOT / "examples" / "payment-callback-idempotency" / "invariants.yaml"
REFUND_WORKFLOW = ROOT / "examples" / "refund-vs-fulfill-race" / "workflow.yaml"
REFUND_INVARIANTS = ROOT / "examples" / "refund-vs-fulfill-race" / "invariants.yaml"


@pytest.mark.asyncio
async def test_generates_payment_callback_idempotency_plans() -> None:
    workflow = load_model(CALLBACK_WORKFLOW, Workflow)
    invariants = load_typed(CALLBACK_INVARIANTS, list[Invariant])

    plans = await PaymentCallbackIdempotencyGenerator().generate(workflow, invariants)

    assert len(plans) == 8
    assert {plan.attack_type for plan in plans} == {
        "concurrent-replay",
        "burst-replay",
        "idempotency-key-reuse",
        "sequential-replay",
    }
    assert all(plan.target_steps == ["payment-callback"] for plan in plans)
    assert any(plan.id.endswith("simultaneous-c2") for plan in plans)


@pytest.mark.asyncio
async def test_generates_refund_fulfill_parallel_step_plan() -> None:
    workflow = load_model(REFUND_WORKFLOW, Workflow)
    invariants = load_typed(REFUND_INVARIANTS, list[Invariant])

    plans = await RefundFulfillRaceGenerator().generate(workflow, invariants)

    assert len(plans) == 3
    assert {plan.attack_type for plan in plans} == {"parallel-step-race"}
    assert {tuple(plan.target_steps) for plan in plans} == {
        ("refund-order", "fulfill-order"),
        ("fulfill-order", "refund-order"),
    }
    assert all(plan.schedule.concurrency == 2 for plan in plans)
    assert all(
        plan.schedule.options["required_executor_capability"] == "parallel-step-race"
        for plan in plans
    )


@pytest.mark.asyncio
async def test_business_logic_generator_includes_new_strategy_families() -> None:
    callback_workflow = load_model(CALLBACK_WORKFLOW, Workflow)
    callback_invariants = load_typed(CALLBACK_INVARIANTS, list[Invariant])
    refund_workflow = load_model(REFUND_WORKFLOW, Workflow)
    refund_invariants = load_typed(REFUND_INVARIANTS, list[Invariant])
    generator = BusinessLogicAttackGenerator()

    callback_plans = await generator.generate(callback_workflow, callback_invariants)
    refund_plans = await generator.generate(refund_workflow, refund_invariants)

    assert any(plan.id.startswith("callback-idempotency.") for plan in callback_plans)
    assert any(plan.id.startswith("refund-fulfill.") for plan in refund_plans)

@pytest.mark.asyncio
async def test_generic_fallback_covers_unknown_state_changing_action() -> None:
    workflow = Workflow.model_validate(
        {
            "name": "generic-demo",
            "base_url": "http://example.test",
            "sessions": {"alice": {"schema_version": "0.1"}},
            "steps": [
                {
                    "id": "create-run",
                    "role": "setup",
                    "session": "alice",
                    "request": {"method": "POST", "path": "/api/runs"},
                    "extract": [
                        {
                            "name": "run_id",
                            "kind": "jsonpath",
                            "expression": "$.run_id",
                        }
                    ],
                },
                {
                    "id": "state-before",
                    "role": "probe",
                    "session": "alice",
                    "request": {"method": "GET", "path": "/api/runs/${run_id}/state"},
                    "depends_on": ["create-run"],
                    "tags": ["state", "before"],
                },
                {
                    "id": "apply-adjustment",
                    "role": "action",
                    "session": "alice",
                    "request": {"method": "POST", "path": "/api/runs/${run_id}/adjust"},
                    "depends_on": ["state-before"],
                    "tags": ["attack-target"],
                },
                {
                    "id": "state-after",
                    "role": "probe",
                    "session": "alice",
                    "request": {"method": "GET", "path": "/api/runs/${run_id}/state"},
                    "depends_on": ["apply-adjustment"],
                    "tags": ["state", "after"],
                },
            ],
            "state_probe_steps": ["state-before", "state-after"],
        }
    )
    invariants = [
        Invariant(
            id="generic-counter-limit",
            kind="count-limit",
            selector="$.adjustment_count",
            before_probe="state-before",
            after_probe="state-after",
            parameters={"max_count": 1},
            description="unknown domain counter must not be over-applied",
        )
    ]

    direct_plans = await GenericInvariantReplayGenerator().generate(workflow, invariants)
    total_plans = await BusinessLogicAttackGenerator().generate(workflow, invariants)

    assert {plan.attack_type for plan in direct_plans} == {
        "concurrent-replay",
        "offset-sweep",
        "sequential-replay",
    }
    assert [plan.id for plan in total_plans] == sorted(plan.id for plan in direct_plans)
