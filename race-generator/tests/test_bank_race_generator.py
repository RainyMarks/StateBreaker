from __future__ import annotations

from pathlib import Path

import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import AttackPlan, Invariant, Workflow

from statebreaker_race_generator.plugin import BankRaceAttackGenerator, BusinessLogicAttackGenerator

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "examples" / "bank-double-withdraw" / "workflow.yaml"
INVARIANTS_PATH = ROOT / "examples" / "bank-double-withdraw" / "invariants.yaml"


@pytest.mark.asyncio
async def test_generates_bank_double_withdraw_race_plans() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])

    plans = await BankRaceAttackGenerator().generate(workflow, invariants)

    assert len(plans) == 12
    assert {plan.attack_type for plan in plans} == {
        "concurrent-replay",
        "burst-replay",
        "offset-sweep",
    }
    assert all(plan.workflow_name == workflow.name for plan in plans)
    assert all(plan.target_steps == ["withdraw-cash"] for plan in plans)
    assert all(plan.session_bindings == {"withdraw-cash": "alice"} for plan in plans)
    assert {tuple(plan.invariant_ids) for plan in plans} == {
        ("account-balance-never-negative",),
        ("one-successful-withdrawal-per-full-balance",),
    }
    assert max(plan.schedule.concurrency for plan in plans) == 4
    assert all(isinstance(plan.metadata.get("invariant"), dict) for plan in plans)

    simultaneous = next(
        plan
        for plan in plans
        if plan.attack_type == "concurrent-replay"
        and plan.invariant_ids == ["account-balance-never-negative"]
    )
    assert simultaneous.id == (
        "bank-race.withdraw-cash.account-balance-never-negative.simultaneous-c2"
    )
    assert simultaneous.schedule.offsets_ms == [0.0, 0.0]


@pytest.mark.asyncio
async def test_business_logic_generator_includes_bank_race_strategy() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])

    plans = await BusinessLogicAttackGenerator().generate(workflow, invariants)

    assert len(plans) == 12
    assert any(plan.id.startswith("bank-race.") for plan in plans)
    assert {plan.attack_type for plan in plans} == {
        "concurrent-replay",
        "burst-replay",
        "offset-sweep",
    }


@pytest.mark.asyncio
async def test_bank_plan_output_round_trips_through_public_contract() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])

    plans = await BankRaceAttackGenerator().generate(workflow, invariants)

    restored = [AttackPlan.model_validate_json(plan.model_dump_json()) for plan in plans]
    assert restored == plans