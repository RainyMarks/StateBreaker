from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from statebreaker.documents import load_model, load_typed
from statebreaker.models import FindingVerdict, Invariant, Workflow
from statebreaker.runtime import ExecutionRuntime
from statebreaker_race_generator.plugin import BankRaceAttackGenerator, BusinessLogicAttackGenerator
from statebreaker_verifier_basic.plugin import BasicVerifierPlugin

from statebreaker_race_executor.plugin import RaceAttackExecutor

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "examples" / "bank-double-withdraw" / "workflow.yaml"
INVARIANTS_PATH = ROOT / "examples" / "bank-double-withdraw" / "invariants.yaml"
LAB_ROOT = ROOT / "labs" / "bank-double-withdraw"
sys.path.insert(0, str(LAB_ROOT))


@pytest.fixture
def workflow() -> Workflow:
    return load_model(WORKFLOW_PATH, Workflow)


@pytest.fixture
def invariants() -> list[Invariant]:
    return load_typed(INVARIANTS_PATH, list[Invariant])


async def _execute_bank_race(workflow: Workflow, invariants: list[Invariant]) -> object:
    from bank_lab.main import app

    plans = await BankRaceAttackGenerator().generate(workflow, invariants)
    plan = next(
        item
        for item in plans
        if item.attack_type == "concurrent-replay"
        and item.invariant_ids == ["account-balance-never-negative"]
    )
    transport = httpx.ASGITransport(app=app)
    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        return await RaceAttackExecutor().execute(plan, runtime)


@pytest.mark.asyncio
async def test_concurrent_withdraw_confirms_balance_race(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    result = await _execute_bank_race(workflow, invariants)

    assert result.plugin_data["target_status_codes"] == [200, 200]
    assert result.before_state["balance_cents"] == 10_000
    assert result.after_state["balance_cents"] == -10_000
    assert result.after_state["overdraft"] is True
    assert result.plugin_data["balance_before"] == 10_000
    assert result.plugin_data["balance_after"] == -10_000
    assert result.plugin_data["balance_delta"] == -20_000
    assert result.plugin_data["successful_withdrawals"] == 2
    assert result.plugin_data["overdraft_observed"] is True
    assert result.plugin_data["vulnerability_observed"] is True
    assert result.plugin_data["withdraw_checked_events"] == 2
    assert result.plugin_data["withdraw_committed_events"] == 2


@pytest.mark.asyncio
async def test_basic_verifier_confirms_bank_race_finding(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    result = await _execute_bank_race(workflow, invariants)

    findings = await BasicVerifierPlugin().verify(result, invariants)
    verdicts = {finding.invariant_id: finding.verdict for finding in findings}

    assert verdicts["account-balance-never-negative"] == FindingVerdict.CONFIRMED
    assert verdicts["one-successful-withdrawal-per-full-balance"] == FindingVerdict.CONFIRMED


@pytest.mark.asyncio
async def test_total_strategy_generator_can_drive_bank_race(
    workflow: Workflow,
    invariants: list[Invariant],
) -> None:
    from bank_lab.main import app

    plans = await BusinessLogicAttackGenerator().generate(workflow, invariants)
    plan = next(item for item in plans if item.id.endswith("simultaneous-c2"))
    transport = httpx.ASGITransport(app=app)
    async with ExecutionRuntime(workflow, transport=transport) as runtime:
        result = await RaceAttackExecutor().execute(plan, runtime)

    assert result.plugin_data["overdraft_observed"] is True