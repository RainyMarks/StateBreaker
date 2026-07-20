from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import FindingVerdict, Invariant, Workflow
from statebreaker.runtime import ExecutionRuntime
from statebreaker_race_generator.plugin import PaymentBindingAttackGenerator
from statebreaker_verifier_basic.plugin import BasicVerifierPlugin

from statebreaker_race_executor.plugin import RaceAttackExecutor

ROOT = Path(__file__).resolve().parents[2]
LAB_ROOT = ROOT / "labs" / "payment-binding-mismatch"
AUTH_WORKFLOW = ROOT / "examples" / "payment-binding-mismatch" / "authorization-workflow.yaml"
AUTH_INVARIANTS = ROOT / "examples" / "payment-binding-mismatch" / "authorization-invariants.yaml"
BINDING_WORKFLOW = ROOT / "examples" / "payment-binding-mismatch" / "binding-workflow.yaml"
BINDING_INVARIANTS = ROOT / "examples" / "payment-binding-mismatch" / "binding-invariants.yaml"
sys.path.insert(0, str(LAB_ROOT))


@pytest.mark.asyncio
async def test_authorization_bypass_is_confirmed() -> None:
    from binding_lab.main import app

    workflow = load_model(AUTH_WORKFLOW, Workflow)
    invariants = load_typed(AUTH_INVARIANTS, list[Invariant])
    plans = await PaymentBindingAttackGenerator().generate(workflow, invariants)
    transport = httpx.ASGITransport(app=app)

    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        result = await RaceAttackExecutor().execute(plans[0], runtime)

    assert result.plugin_data["target_status_codes"] == [200]
    assert result.plugin_data["bob_order_status"] == "PAID"
    assert result.plugin_data["bob_paid_by"] == "alice"
    assert result.plugin_data["unauthorized_payment_observed"] is True
    assert result.plugin_data["vulnerability_observed"] is True

    findings = await BasicVerifierPlugin().verify(result, invariants)
    assert findings[0].verdict == FindingVerdict.CONFIRMED


@pytest.mark.asyncio
async def test_binding_mismatch_is_confirmed() -> None:
    from binding_lab.main import app

    workflow = load_model(BINDING_WORKFLOW, Workflow)
    invariants = load_typed(BINDING_INVARIANTS, list[Invariant])
    plans = await PaymentBindingAttackGenerator().generate(workflow, invariants)
    transport = httpx.ASGITransport(app=app)

    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        result = await RaceAttackExecutor().execute(plans[0], runtime)

    assert result.plugin_data["target_status_codes"] == [200]
    assert result.plugin_data["bob_order_status"] == "PAID"
    assert result.plugin_data["bob_paid_by"] == "alice"
    assert result.plugin_data["bob_payment_token_owner"] == "alice"
    assert result.plugin_data["bob_paid_with_alice_token"] is True
    assert result.plugin_data["binding_mismatch_observed"] is True
    assert result.plugin_data["vulnerability_observed"] is True

    findings = await BasicVerifierPlugin().verify(result, invariants)
    assert findings[0].verdict == FindingVerdict.CONFIRMED
