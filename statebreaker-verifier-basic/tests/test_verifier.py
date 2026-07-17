from __future__ import annotations

from datetime import UTC, datetime

import pytest
from statebreaker.errors import PluginError
from statebreaker.models import (
    FindingVerdict,
    Invariant,
    RawAttackResult,
    ResponseRecord,
)

from statebreaker_verifier_basic.plugin import BasicVerifierPlugin


def _result(
    *,
    before: dict,
    after: dict,
    codes: list[int],
    vulnerability_observed: bool = False,
) -> RawAttackResult:
    now = datetime.now(UTC)
    return RawAttackResult(
        run_id="run-1",
        attack_plan_id="plan-1",
        started_at=now,
        finished_at=now,
        before_state=before,
        after_state=after,
        responses=[
            ResponseRecord(
                correlation_id=f"c{i}",
                step_id="redeem-coupon",
                request_ordinal=i,
                status_code=code,
                elapsed_ms=1.0,
            )
            for i, code in enumerate(codes)
        ],
        plugin_data={"vulnerability_observed": vulnerability_observed},
    )


@pytest.mark.asyncio
async def test_max_delta_confirmed_when_delta_exceeds_bound() -> None:
    inv = Invariant(
        id="coupon-max-delta",
        kind="max-delta",
        selector="$.discount_yuan",
        parameters={"max_delta": 50},
        description="max +50",
    )
    result = _result(
        before={"discount_yuan": 0},
        after={"discount_yuan": 100},
        codes=[200, 200],
    )

    findings = await BasicVerifierPlugin().verify(result, [inv])

    assert len(findings) == 1
    assert findings[0].verdict == FindingVerdict.CONFIRMED
    assert findings[0].details["observed_delta"] == 100
    assert findings[0].invariant_id == "coupon-max-delta"
    assert "state:before" in findings[0].evidence_refs


@pytest.mark.asyncio
async def test_max_delta_rejected_when_within_bound() -> None:
    inv = Invariant(
        id="coupon-max-delta",
        kind="max-delta",
        selector="$.discount_yuan",
        parameters={"max_delta": 50},
    )
    result = _result(
        before={"discount_yuan": 0},
        after={"discount_yuan": 50},
        codes=[200, 409],
    )

    findings = await BasicVerifierPlugin().verify(result, [inv])

    assert findings[0].verdict == FindingVerdict.REJECTED


@pytest.mark.asyncio
async def test_probable_when_state_missing_but_heuristic_flags() -> None:
    inv = Invariant(
        id="coupon-max-delta",
        kind="max-delta",
        selector="$.discount_yuan",
        parameters={"max_delta": 50},
    )
    result = _result(
        before={},
        after={},
        codes=[200, 200],
        vulnerability_observed=True,
    )

    findings = await BasicVerifierPlugin().verify(result, [inv])

    assert findings[0].verdict == FindingVerdict.PROBABLE


@pytest.mark.asyncio
async def test_requires_at_least_one_invariant() -> None:
    result = _result(before={}, after={}, codes=[200])
    with pytest.raises(PluginError, match="at least one invariant"):
        await BasicVerifierPlugin().verify(result, [])
