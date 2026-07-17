from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from statebreaker.models import (
    AttackPlan,
    Finding,
    FindingVerdict,
    RawAttackResult,
    ResponseRecord,
    RunBundle,
    Workflow,
)

from statebreaker_reporter_pdf.plugin import PdfReporterPlugin


def _bundle() -> RunBundle:
    now = datetime.now(UTC)
    workflow = Workflow.model_validate(
        {
            "name": "coupon-race-demo",
            "base_url": "http://127.0.0.1:8080",
            "sessions": {"alice": {}},
            "steps": [
                {
                    "id": "redeem-coupon",
                    "session": "alice",
                    "request": {"method": "POST", "path": "/api/runs/x/redeem"},
                }
            ],
        }
    )
    plan = AttackPlan(
        id="double-hand-coupon",
        workflow_name="coupon-race-demo",
        attack_type="concurrent-replay",
        target_steps=["redeem-coupon"],
    )
    result = RawAttackResult(
        run_id="run-abc",
        attack_plan_id=plan.id,
        started_at=now,
        finished_at=now,
        before_state={"discount_yuan": 0},
        after_state={"discount_yuan": 100},
        responses=[
            ResponseRecord(
                correlation_id="c0",
                step_id="redeem-coupon",
                request_ordinal=0,
                status_code=200,
                elapsed_ms=12.0,
            ),
            ResponseRecord(
                correlation_id="c1",
                step_id="redeem-coupon",
                request_ordinal=1,
                status_code=200,
                elapsed_ms=13.0,
            ),
        ],
        plugin_data={"vulnerability_observed": True},
    )
    findings = [
        Finding(
            id="finding.coupon-max-delta",
            verdict=FindingVerdict.CONFIRMED,
            title="Invariant coupon-max-delta violated by state evidence",
            invariant_id="coupon-max-delta",
            evidence_refs=["state:before", "state:after"],
            details={"observed_delta": 100},
        )
    ]
    return RunBundle(workflow=workflow, attack_plan=plan, result=result, findings=findings)


@pytest.mark.asyncio
async def test_render_writes_pdf_and_summary(tmp_path: Path) -> None:
    artifacts = await PdfReporterPlugin().render(_bundle(), tmp_path)

    pdf = tmp_path / "statebreaker-report.pdf"
    summary = tmp_path / "report-summary.json"
    assert pdf.is_file()
    assert pdf.stat().st_size > 200
    assert summary.is_file()
    assert pdf.resolve().as_posix() in {
        Path(path).resolve().as_posix() for path in artifacts.files
    }
    assert artifacts.metadata["format"] == "pdf"
    assert artifacts.metadata["findings_count"] == 1
    assert b"%PDF" in pdf.read_bytes()[:8]
