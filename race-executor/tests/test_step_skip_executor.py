from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import FindingVerdict, Invariant, Workflow
from statebreaker.runtime import ExecutionRuntime
from statebreaker_race_generator.plugin import StepSkipAttackGenerator
from statebreaker_verifier_basic.plugin import BasicVerifierPlugin

from statebreaker_race_executor.plugin import RaceAttackExecutor

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "examples" / "payment-step-skip" / "workflow.yaml"
INVARIANTS_PATH = ROOT / "examples" / "payment-step-skip" / "invariants.yaml"
LAB_ROOT = ROOT / "labs" / "payment-step-skip"
sys.path.insert(0, str(LAB_ROOT))


@pytest.fixture
def workflow() -> Workflow:
    return load_model(WORKFLOW_PATH, Workflow)


@pytest.fixture
def invariants() -> list[Invariant]:
    return load_typed(INVARIANTS_PATH, list[Invariant])


async def _execute_step_skip(workflow: Workflow, invariants: list[Invariant]) -> object:
    from payment_lab.main import app, set_payment_guard

    set_payment_guard(False)
    plans = await StepSkipAttackGenerator().generate(workflow, invariants)
    transport = httpx.ASGITransport(app=app)
    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        return await RaceAttackExecutor().execute(plans[0], runtime)


@pytest.mark.asyncio
async def test_step_skip_confirms_order_without_payment(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    result = await _execute_step_skip(workflow, invariants)

    assert result.plugin_data["target_status_codes"] == [200]
    assert result.plugin_data["skipped_steps"] == ["pay-order"]
    assert result.plugin_data["step_skip_succeeded"] is True
    assert result.plugin_data["confirmed_without_payment"] is True
    assert result.plugin_data["vulnerability_observed"] is True
    assert result.before_state["confirmed_without_payment"] is False
    assert result.after_state["confirmed_without_payment"] is True
    assert "pay-order" not in {record.step_id for record in result.responses}
    assert any(event.kind == "attack.step.skipped" for event in result.events)


@pytest.mark.asyncio
async def test_basic_verifier_confirms_step_skip_finding(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    result = await _execute_step_skip(workflow, invariants)

    findings = await BasicVerifierPlugin().verify(result, invariants)

    assert findings[0].verdict == FindingVerdict.CONFIRMED
    assert findings[0].invariant_id == "payment-required-before-confirm"
    assert findings[0].details["unexpected_after"] is True