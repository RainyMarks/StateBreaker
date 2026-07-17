from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from statebreaker.errors import PluginError
from statebreaker.models import Workflow
from statebreaker.runtime import ExecutionRuntime

from statebreaker_learner_delta.plugin import DeltaLearnerPlugin

COUPON_WORKFLOW: dict[str, Any] = {
    "name": "coupon-race-demo",
    "description": "BUG50 single-redemption baseline",
    "base_url": "http://127.0.0.1:8080",
    "sessions": {"alice": {}},
    "variables": {"coupon_code": "BUG50"},
    "steps": [
        {
            "id": "create-run",
            "role": "setup",
            "session": "alice",
            "request": {"method": "POST", "path": "/api/runs"},
            "extract": [{"name": "run_id", "kind": "jsonpath", "expression": "$.run_id"}],
        },
        {
            "id": "state-before",
            "role": "probe",
            "session": "alice",
            "request": {"method": "GET", "path": "/api/runs/${run_id}/state"},
            "depends_on": ["create-run"],
        },
        {
            "id": "redeem-coupon",
            "role": "action",
            "session": "alice",
            "request": {
                "method": "POST",
                "path": "/api/runs/${run_id}/redeem",
                "json_body": {"coupon_code": "${coupon_code}"},
            },
            "depends_on": ["state-before"],
        },
        {
            "id": "state-after",
            "role": "probe",
            "session": "alice",
            "request": {"method": "GET", "path": "/api/runs/${run_id}/state"},
            "depends_on": ["redeem-coupon"],
        },
    ],
    "state_probe_steps": ["state-before", "state-after"],
}


class FakeCouponLab:
    """Minimal stand-in for labs/coupon-race, wired through httpx.MockTransport."""

    def __init__(self, *, fail_redeem_for_runs: set[str] | None = None) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._fail_redeem_for_runs = fail_redeem_for_runs or set()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/runs" and request.method == "POST":
            self._counter += 1
            run_id = f"run-{self._counter}"
            self.runs[run_id] = {
                "run_id": run_id,
                "coupon_code": "BUG50",
                "coupon_value": 50,
                "discount_yuan": 0,
                "coupon_used": False,
                "successful_redemptions": 0,
                "created_at": f"2024-01-01T00:00:{self._counter:02d}+00:00",
            }
            return httpx.Response(201, json=self.runs[run_id])

        parts = path.split("/")
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "runs":
            run_id, action = parts[3], parts[4]
            run = self.runs.get(run_id)
            if run is None:
                return httpx.Response(404)
            if action == "state" and request.method == "GET":
                return httpx.Response(200, json=run)
            if action == "redeem" and request.method == "POST":
                if run_id in self._fail_redeem_for_runs:
                    # A transport-level failure (timeout/connection error), not just
                    # a non-2xx status: execute_step only raises on httpx.HTTPError,
                    # so this is what actually exercises the discard-a-round path.
                    raise httpx.ReadTimeout("simulated timeout", request=request)
                if not run["coupon_used"]:
                    run["discount_yuan"] += 50
                    run["coupon_used"] = True
                    run["successful_redemptions"] += 1
                return httpx.Response(200, json=run)
        return httpx.Response(404)


@pytest.mark.asyncio
async def test_learn_end_to_end_proposes_expected_invariants(tmp_path: Path) -> None:
    workflow = Workflow.model_validate(COUPON_WORKFLOW)
    lab = FakeCouponLab()
    plugin = DeltaLearnerPlugin(sample_count=6)

    async with ExecutionRuntime(
        workflow, output_root=tmp_path, transport=httpx.MockTransport(lab.handler)
    ) as runtime:
        result = await plugin.learn(workflow, runtime)

    assert "$.discount_yuan" in result.profile.stable_fields
    assert "$.coupon_used" in result.profile.stable_fields
    assert "$.run_id" in result.profile.ignored_fields
    assert "$.created_at" in result.profile.ignored_fields

    invariant_ids = {invariant.id for invariant in result.invariants}
    assert "learned-max-delta-discount-yuan" in invariant_ids
    assert "learned-min-value-discount-yuan" in invariant_ids
    assert "learned-state-transition-coupon-used" in invariant_ids
    assert "learned-max-delta-successful-redemptions" in invariant_ids

    # coupon_code and coupon_value never change, run_id/created_at are ignored:
    # none of them should produce a candidate rule.
    assert not any("coupon-code" in invariant_id for invariant_id in invariant_ids)
    assert not any("coupon-value" in invariant_id for invariant_id in invariant_ids)
    assert not any("run-id" in invariant_id for invariant_id in invariant_ids)

    max_delta = next(
        inv for inv in result.invariants if inv.id == "learned-max-delta-discount-yuan"
    )
    assert max_delta.parameters["max_delta"] == 50
    assert max_delta.parameters["confidence"] == 1.0
    assert max_delta.before_probe == "state-before"
    assert max_delta.after_probe == "state-after"


@pytest.mark.asyncio
async def test_learn_discards_failed_rounds_without_crashing(tmp_path: Path) -> None:
    workflow = Workflow.model_validate(COUPON_WORKFLOW)
    # The 2nd and 4th created runs (run-2, run-4) will fail their redeem call.
    lab = FakeCouponLab(fail_redeem_for_runs={"run-2", "run-4"})
    plugin = DeltaLearnerPlugin(sample_count=5)

    async with ExecutionRuntime(
        workflow, output_root=tmp_path, transport=httpx.MockTransport(lab.handler)
    ) as runtime:
        result = await plugin.learn(workflow, runtime)

    discarded = [event for event in runtime.events if event.kind == "learner.sample-discarded"]
    assert len(discarded) == 2
    assert len(result.profile.samples) == 3
    assert any(inv.id == "learned-max-delta-discount-yuan" for inv in result.invariants)


@pytest.mark.asyncio
async def test_learn_returns_empty_invariants_when_too_few_rounds_succeed(
    tmp_path: Path,
) -> None:
    workflow = Workflow.model_validate(COUPON_WORKFLOW)
    lab = FakeCouponLab(fail_redeem_for_runs={"run-1", "run-2", "run-3"})
    plugin = DeltaLearnerPlugin(sample_count=4)

    async with ExecutionRuntime(
        workflow, output_root=tmp_path, transport=httpx.MockTransport(lab.handler)
    ) as runtime:
        result = await plugin.learn(workflow, runtime)

    assert result.invariants == []
    assert any(event.kind == "learner.insufficient-samples" for event in runtime.events)


@pytest.mark.asyncio
async def test_learn_requires_at_least_two_state_probe_steps(tmp_path: Path) -> None:
    single_probe_workflow = {
        **COUPON_WORKFLOW,
        "steps": [
            step for step in COUPON_WORKFLOW["steps"] if step["id"] != "state-after"
        ],
        "state_probe_steps": ["state-before"],
    }
    workflow = Workflow.model_validate(single_probe_workflow)
    plugin = DeltaLearnerPlugin(sample_count=3)

    async with ExecutionRuntime(
        workflow, output_root=tmp_path, transport=httpx.MockTransport(FakeCouponLab().handler)
    ) as runtime:
        with pytest.raises(PluginError, match="at least two state_probe_steps"):
            await plugin.learn(workflow, runtime)
