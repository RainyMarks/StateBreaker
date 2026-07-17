from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import AttackPlan, Invariant, Workflow
from statebreaker.runtime import ExecutionRuntime
from statebreaker_race_generator.plugin import RaceAttackGenerator

from statebreaker_race_executor.plugin import RaceAttackExecutor

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "examples" / "coupon-race" / "workflow.yaml"
INVARIANTS_PATH = ROOT / "examples" / "coupon-race" / "invariants.yaml"
LAB_ROOT = ROOT / "labs" / "coupon-race"
sys.path.insert(0, str(LAB_ROOT))


@pytest.fixture
def workflow() -> Workflow:
    return load_model(WORKFLOW_PATH, Workflow)


@pytest.fixture
def invariants() -> list[Invariant]:
    return load_typed(INVARIANTS_PATH, list[Invariant])


async def _execute(plan: AttackPlan, workflow: Workflow) -> object:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        return await RaceAttackExecutor().execute(plan, runtime)


@pytest.mark.asyncio
async def test_concurrent_replay_confirms_coupon_race(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    plans = await RaceAttackGenerator().generate(workflow, invariants)
    plan = next(item for item in plans if item.attack_type == "concurrent-replay")

    result = await _execute(plan, workflow)

    assert result.plugin_data["target_status_codes"] == [200, 200]
    assert result.after_state["discount_yuan"] == 100
    assert result.plugin_data["discount_delta"] == 100
    assert result.plugin_data["successful_redemptions"] == 2
    assert result.plugin_data["vulnerability_observed"] is True
    assert result.plugin_data["evaluation_mode"] == "invariant"
    assert result.plugin_data["is_formal_finding"] is False
    assert result.plugin_data["invariant_evidence"]["observed_delta"] == 100
    assert result.plugin_data["checked_events"] == 2
    assert result.plugin_data["committed_events"] == 2


@pytest.mark.asyncio
async def test_precondition_bypass_is_rejected_by_server_state(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    plans = await RaceAttackGenerator().generate(workflow, invariants)
    plan = next(item for item in plans if item.attack_type == "precondition-bypass-replay")

    result = await _execute(plan, workflow)

    assert result.plugin_data["target_status_codes"] == [200, 409, 409, 409]
    assert result.after_state["discount_yuan"] == 50
    assert result.plugin_data["successful_redemptions"] == 1
    assert result.plugin_data["vulnerability_observed"] is False
    assert result.plugin_data["rejected_events"] == 3


@pytest.mark.asyncio
async def test_shared_request_id_does_not_deduplicate_current_lab(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    plans = await RaceAttackGenerator().generate(workflow, invariants)
    plan = next(item for item in plans if item.attack_type == "idempotency-key-reuse")

    result = await _execute(plan, workflow)

    assert result.plugin_data["target_status_codes"] == [200, 200]
    assert result.after_state["successful_redemptions"] == 2
    assert result.plugin_data["vulnerability_observed"] is True


@pytest.mark.asyncio
async def test_stale_state_probe_can_drive_followup_redeem(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    plans = await RaceAttackGenerator().generate(workflow, invariants)
    plan = next(item for item in plans if item.attack_type == "stale-state-assisted-replay")

    result = await _execute(plan, workflow)

    assert result.plugin_data["target_status_codes"] == [200, 200]
    assert result.plugin_data["stale_state_observed"] is True
    assert result.plugin_data["intermediate_states"][0]["coupon_used"] is False
    assert result.after_state["successful_redemptions"] == 2
    assert result.plugin_data["vulnerability_observed"] is True


@pytest.mark.asyncio
async def test_run_eviction_pressure_expires_original_run(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    plans = await RaceAttackGenerator().generate(workflow, invariants)
    plan = next(item for item in plans if item.attack_type == "run-eviction-pressure")

    result = await _execute(plan, workflow)

    assert result.plugin_data["target_status_codes"] == [404]
    assert result.plugin_data["run_evicted"] is True
    assert result.plugin_data["availability_issue_observed"] is True
    assert result.plugin_data["vulnerability_observed"] is False
