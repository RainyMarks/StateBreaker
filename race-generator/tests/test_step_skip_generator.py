from __future__ import annotations

from pathlib import Path

import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import AttackPlan, Invariant, Workflow

from statebreaker_race_generator.plugin import StepSkipAttackGenerator

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "examples" / "payment-step-skip" / "workflow.yaml"
INVARIANTS_PATH = ROOT / "examples" / "payment-step-skip" / "invariants.yaml"


@pytest.mark.asyncio
async def test_generates_payment_step_skip_plan() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])

    plans = await StepSkipAttackGenerator().generate(workflow, invariants)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.id == "step-skip.confirm-order.payment-required-before-confirm.skip-pay-order"
    assert plan.attack_type == "step-skip"
    assert plan.workflow_name == "payment-step-skip-demo"
    assert plan.target_steps == ["confirm-order"]
    assert plan.session_bindings == {"confirm-order": "alice"}
    assert plan.invariant_ids == ["payment-required-before-confirm"]
    assert plan.schedule.concurrency == 1
    assert plan.schedule.offsets_ms == [0.0]
    assert plan.schedule.options["skip_steps"] == ["pay-order"]
    assert plan.schedule.options["required_executor_capability"] == "step-skip"
    assert plan.metadata["skipped_step"] == "pay-order"
    assert plan.metadata["invariant"]["selector"] == "$.confirmed_without_payment"


@pytest.mark.asyncio
async def test_step_skip_generation_is_deterministic() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])
    generator = StepSkipAttackGenerator()

    first = await generator.generate(workflow, invariants)
    second = await generator.generate(workflow, invariants)

    assert [plan.model_dump(mode="json") for plan in first] == [
        plan.model_dump(mode="json") for plan in second
    ]


@pytest.mark.asyncio
async def test_step_skip_plan_round_trips_through_public_contract() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])

    plans = await StepSkipAttackGenerator().generate(workflow, invariants)

    restored = [AttackPlan.model_validate_json(plan.model_dump_json()) for plan in plans]
    assert restored == plans