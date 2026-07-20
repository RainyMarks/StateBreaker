from __future__ import annotations

from pathlib import Path

import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import Invariant, Workflow

from statebreaker_race_generator.plugin import (
    BusinessLogicAttackGenerator,
    PaymentBindingAttackGenerator,
)

ROOT = Path(__file__).resolve().parents[2]
AUTH_WORKFLOW = ROOT / "examples" / "payment-binding-mismatch" / "authorization-workflow.yaml"
AUTH_INVARIANTS = ROOT / "examples" / "payment-binding-mismatch" / "authorization-invariants.yaml"
BINDING_WORKFLOW = ROOT / "examples" / "payment-binding-mismatch" / "binding-workflow.yaml"
BINDING_INVARIANTS = ROOT / "examples" / "payment-binding-mismatch" / "binding-invariants.yaml"


@pytest.mark.asyncio
async def test_generates_authorization_bypass_plan() -> None:
    workflow = load_model(AUTH_WORKFLOW, Workflow)
    invariants = load_typed(AUTH_INVARIANTS, list[Invariant])

    plans = await PaymentBindingAttackGenerator().generate(workflow, invariants)

    assert [plan.id for plan in plans] == [
        "payment-binding.alice-pay-bob-order.bob-order-not-paid-by-alice.single"
    ]
    plan = plans[0]
    assert plan.attack_type == "authorization-bypass"
    assert plan.target_steps == ["alice-pay-bob-order"]
    assert plan.schedule.options["required_executor_capability"] == "authorization-bypass"


@pytest.mark.asyncio
async def test_generates_binding_mismatch_plan() -> None:
    workflow = load_model(BINDING_WORKFLOW, Workflow)
    invariants = load_typed(BINDING_INVARIANTS, list[Invariant])

    plans = await PaymentBindingAttackGenerator().generate(workflow, invariants)

    assert [plan.id for plan in plans] == [
        "payment-binding.alice-token-pay-bob-order."
        "bob-order-not-paid-with-alice-token.single"
    ]
    plan = plans[0]
    assert plan.attack_type == "binding-mismatch"
    assert plan.target_steps == ["alice-token-pay-bob-order"]
    assert plan.schedule.options["required_executor_capability"] == "binding-mismatch"


@pytest.mark.asyncio
async def test_business_logic_generator_includes_payment_binding_family() -> None:
    workflow = load_model(BINDING_WORKFLOW, Workflow)
    invariants = load_typed(BINDING_INVARIANTS, list[Invariant])

    plans = await BusinessLogicAttackGenerator().generate(workflow, invariants)

    assert any(plan.attack_type == "binding-mismatch" for plan in plans)
