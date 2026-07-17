from __future__ import annotations

from pathlib import Path

import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import AttackPlan, Invariant, Workflow

from statebreaker_race_generator.plugin import RaceAttackGenerator

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "examples" / "coupon-race" / "workflow.yaml"
INVARIANTS_PATH = ROOT / "examples" / "coupon-race" / "invariants.yaml"


@pytest.mark.asyncio
async def test_generates_bounded_coupon_race_plans() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])

    plans = await RaceAttackGenerator().generate(workflow, invariants)

    assert len(plans) == 10
    assert {plan.attack_type for plan in plans} == {
        "concurrent-replay",
        "burst-replay",
        "offset-sweep",
        "precondition-bypass-replay",
        "idempotency-key-reuse",
        "stale-state-assisted-replay",
        "run-eviction-pressure",
    }
    assert all(plan.workflow_name == workflow.name for plan in plans)
    assert all(plan.target_steps == ["redeem-coupon"] for plan in plans)
    assert all(plan.invariant_ids == ["coupon-max-delta"] for plan in plans)
    assert all(plan.session_bindings == {"redeem-coupon": "alice"} for plan in plans)
    assert max(plan.schedule.concurrency for plan in plans) == 4
    assert all(
        len(plan.schedule.offsets_ms) == plan.schedule.concurrency for plan in plans
    )
    assert all(isinstance(plan.metadata.get("invariant"), dict) for plan in plans)
    assert all(plan.metadata["invariant"]["id"] == "coupon-max-delta" for plan in plans)
    assert all(
        plan.metadata["invariant"]["parameters"].get("max_delta") == 50 for plan in plans
    )
    assert all("verdict_note" in plan.metadata for plan in plans)

    bypass = next(
        plan for plan in plans if plan.attack_type == "precondition-bypass-replay"
    )
    assert bypass.schedule.concurrency == 1
    assert bypass.schedule.options["repeat_count"] == 4
    assert bypass.schedule.options["skip_steps"] == ["state-before"]
    assert bypass.schedule.options["continue_on_rejection"] is True
    assert bypass.schedule.options["hard_request_limit"] == 4

    idempotency = next(
        plan for plan in plans if plan.attack_type == "idempotency-key-reuse"
    )
    assert idempotency.schedule.concurrency == 2
    assert idempotency.schedule.options["request_id_mode"] == "shared"
    assert idempotency.schedule.options["request_id_value"] == "coupon-redeem-duplicate"

    stale_state = next(
        plan for plan in plans if plan.attack_type == "stale-state-assisted-replay"
    )
    assert stale_state.schedule.options["strategy"] == "state-probe-assisted"
    assert stale_state.schedule.options["probe_after_ms"] == 50.0

    eviction = next(plan for plan in plans if plan.attack_type == "run-eviction-pressure")
    assert eviction.schedule.options["create_count"] == 101


@pytest.mark.asyncio
async def test_generation_is_deterministic() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])
    generator = RaceAttackGenerator()

    first = await generator.generate(workflow, invariants)
    second = await generator.generate(workflow, invariants)

    assert [plan.model_dump(mode="json") for plan in first] == [
        plan.model_dump(mode="json") for plan in second
    ]
    assert len({plan.id for plan in first}) == len(first)


@pytest.mark.asyncio
async def test_unsupported_or_unrelated_invariant_is_skipped() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    unrelated = Invariant(
        id="profile-email-format",
        kind="format",
        selector="$.email",
        description="Email should remain valid",
    )

    plans = await RaceAttackGenerator().generate(workflow, [unrelated])

    assert plans == []


@pytest.mark.asyncio
async def test_plan_output_round_trips_through_public_contract() -> None:
    workflow = load_model(WORKFLOW_PATH, Workflow)
    invariants = load_typed(INVARIANTS_PATH, list[Invariant])

    plans = await RaceAttackGenerator().generate(workflow, invariants)

    restored = [AttackPlan.model_validate_json(plan.model_dump_json()) for plan in plans]
    assert restored == plans
