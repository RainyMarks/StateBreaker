from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import FindingVerdict, Invariant, Workflow
from statebreaker.runtime import ExecutionRuntime
from statebreaker_race_generator.plugin import (
    BusinessLogicAttackGenerator,
    PaymentCallbackIdempotencyGenerator,
    RefundFulfillRaceGenerator,
)
from statebreaker_verifier_basic.plugin import BasicVerifierPlugin

from statebreaker_race_executor.plugin import RaceAttackExecutor

ROOT = Path(__file__).resolve().parents[2]
CALLBACK_WORKFLOW = ROOT / "examples" / "payment-callback-idempotency" / "workflow.yaml"
CALLBACK_INVARIANTS = ROOT / "examples" / "payment-callback-idempotency" / "invariants.yaml"
CALLBACK_LAB_ROOT = ROOT / "labs" / "payment-callback-idempotency"
REFUND_WORKFLOW = ROOT / "examples" / "refund-vs-fulfill-race" / "workflow.yaml"
REFUND_INVARIANTS = ROOT / "examples" / "refund-vs-fulfill-race" / "invariants.yaml"
REFUND_LAB_ROOT = ROOT / "labs" / "refund-vs-fulfill-race"
sys.path.insert(0, str(CALLBACK_LAB_ROOT))
sys.path.insert(0, str(REFUND_LAB_ROOT))


@pytest.mark.asyncio
async def test_payment_callback_idempotency_is_confirmed() -> None:
    from callback_lab.main import app

    workflow = load_model(CALLBACK_WORKFLOW, Workflow)
    invariants = load_typed(CALLBACK_INVARIANTS, list[Invariant])
    plans = await PaymentCallbackIdempotencyGenerator().generate(workflow, invariants)
    plan = next(
        item
        for item in plans
        if item.id.endswith("merchant-credit-single-callback-delta.simultaneous-c2")
    )
    transport = httpx.ASGITransport(app=app)
    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        result = await RaceAttackExecutor().execute(plan, runtime)

    assert result.plugin_data["target_status_codes"] == [200, 200]
    assert result.plugin_data["merchant_credit_before"] == 0
    assert result.plugin_data["merchant_credit_after"] == 20_000
    assert result.plugin_data["merchant_credit_delta"] == 20_000
    assert result.plugin_data["payment_apply_count"] == 2
    assert result.plugin_data["duplicate_callback_observed"] is True
    assert result.plugin_data["vulnerability_observed"] is True

    findings = await BasicVerifierPlugin().verify(result, invariants)
    verdicts = {finding.invariant_id: finding.verdict for finding in findings}
    assert verdicts["merchant-credit-single-callback-delta"] == FindingVerdict.CONFIRMED
    assert verdicts["payment-event-applied-once"] == FindingVerdict.CONFIRMED


@pytest.mark.asyncio
async def test_refund_vs_fulfill_race_is_confirmed() -> None:
    from refund_lab.main import app

    workflow = load_model(REFUND_WORKFLOW, Workflow)
    invariants = load_typed(REFUND_INVARIANTS, list[Invariant])
    plans = await RefundFulfillRaceGenerator().generate(workflow, invariants)
    transport = httpx.ASGITransport(app=app)
    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        result = await RaceAttackExecutor().execute(plans[0], runtime)

    assert result.plugin_data["target_status_codes"] == [200, 200]
    assert result.plugin_data["refunded_and_fulfilled"] is True
    assert result.plugin_data["refund_count"] == 1
    assert result.plugin_data["fulfill_count"] == 1
    assert result.plugin_data["vulnerability_observed"] is True

    findings = await BasicVerifierPlugin().verify(result, invariants)
    assert findings[0].verdict == FindingVerdict.CONFIRMED


@pytest.mark.asyncio
async def test_business_logic_generator_can_drive_refund_fulfill_race() -> None:
    from refund_lab.main import app

    workflow = load_model(REFUND_WORKFLOW, Workflow)
    invariants = load_typed(REFUND_INVARIANTS, list[Invariant])
    plans = await BusinessLogicAttackGenerator().generate(workflow, invariants)
    plan = next(item for item in plans if item.attack_type == "parallel-step-race")
    transport = httpx.ASGITransport(app=app)
    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        result = await RaceAttackExecutor().execute(plan, runtime)

    assert result.plugin_data["refunded_and_fulfilled"] is True