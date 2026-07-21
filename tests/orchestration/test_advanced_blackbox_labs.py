"""StateBreaker black-box coverage for advanced local race labs."""

from __future__ import annotations

import os

import pytest
from support.advanced_flows import (
    ADVANCED_FLOW_SPECS,
    BlackBoxFlowSpec,
    record_advanced_blackbox_flow,
)
from support.recorder import LAB_BASE_URL, FlowRecorder, asgi_transport, load_lab_app

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.config.models import ProjectConfig
from statebreaker.models.capture import CapturedTrace
from statebreaker.models.execution import ScanBudget
from statebreaker.models.findings import Finding
from statebreaker.orchestration.scanner import AutoRaceScanner

FULL_ADVANCED_SCAN = os.environ.get("STATEBREAKER_ADVANCED_LAB_FULL_SCAN") == "1"
# The default scan set keeps CI quick while covering the public API shapes most likely to
# confuse dependency learning: path/body state, numeric effects, header/query bindings,
# and conditional version updates. Set STATEBREAKER_ADVANCED_LAB_FULL_SCAN=1 for all 20.
DEFAULT_SCAN_LABS = {
    "lab-advanced-cart-bundle",
    "lab-advanced-ledger-transfer",
    "lab-advanced-header-body-quota",
    "lab-advanced-cas-profile",
}


def _project() -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project": {"name": "advanced-blackbox-lab-scan", "base_url": LAB_BASE_URL},
            "scope": {"allowed_hosts": ["lab.local"], "requests_per_second": 1000},
            "reset": {"strategy": "api", "endpoint": "/__test__/reset"},
            "discovery": {"max_candidates": 8, "max_action_pairs": 4},
            "execution": {
                "schedulers": ["async-http"],
                "concurrency": [2, 4],
                "repetitions": 3,
            },
            "budget": {
                "maximum_requests": 1400,
                "maximum_trial_rounds": 180,
                "maximum_minutes": 5.0,
            },
        }
    )


def _budget() -> ScanBudget:
    return ScanBudget(
        maximum_requests=1400,
        maximum_trial_rounds=180,
        maximum_minutes=5.0,
        requests_per_second=1000,
        max_candidates=8,
        max_action_pairs=4,
    )


async def _capture_trace(spec: BlackBoxFlowSpec) -> tuple[FlowRecorder, CapturedTrace]:
    recorder = FlowRecorder(load_lab_app(spec.lab), capture_id=f"{spec.lab}-capture")
    await record_advanced_blackbox_flow(recorder, spec)
    return recorder, recorder.trace(project="advanced-blackbox-lab-scan")


async def _scan_spec(tmp_path, spec: BlackBoxFlowSpec) -> list[Finding]:  # type: ignore[no-untyped-def]
    recorder, trace = await _capture_trace(spec)
    await recorder.aclose()

    store = ArtifactStore(tmp_path / spec.lab)
    try:
        store.save("captures", trace.capture_id, trace)
        # ASGI transport is only a faster local HTTP boundary: the scanner still sees
        # requests, responses, probes, and reset behavior, not the lab's private state.
        scanner = AutoRaceScanner(store, transport=asgi_transport(load_lab_app(spec.lab)))
        outcome = await scanner.scan(_project(), capture_id=trace.capture_id, budget=_budget())

        assert outcome.status == "completed"
        assert outcome.graph_id
        assert outcome.baseline_id
        assert outcome.candidate_ids
        assert outcome.plan_ids
        return [store.load("findings", finding_id, Finding) for finding_id in outcome.finding_ids]
    finally:
        store.close()


@pytest.mark.parametrize(
    "spec",
    ADVANCED_FLOW_SPECS,
    ids=[spec.lab for spec in ADVANCED_FLOW_SPECS],
)
async def test_records_advanced_lab_blackbox_capture(spec: BlackBoxFlowSpec) -> None:
    recorder, trace = await _capture_trace(spec)
    await recorder.aclose()

    assert len(trace.exchanges) == 4
    assert all(exchange.response_status is not None for exchange in trace.exchanges)
    assert all(200 <= exchange.response_status < 300 for exchange in trace.exchanges)
    assert any(exchange.method == "POST" for exchange in trace.exchanges)
    assert any(exchange.method == "GET" for exchange in trace.exchanges)


@pytest.mark.parametrize(
    "spec",
    [spec for spec in ADVANCED_FLOW_SPECS if spec.lab in DEFAULT_SCAN_LABS],
    ids=[spec.lab for spec in ADVANCED_FLOW_SPECS if spec.lab in DEFAULT_SCAN_LABS],
)
async def test_scans_representative_advanced_labs_with_statebreaker(
    tmp_path, spec: BlackBoxFlowSpec
) -> None:  # type: ignore[no-untyped-def]
    findings = await _scan_spec(tmp_path, spec)

    confirmed = [finding for finding in findings if finding.verdict == "confirmed"]
    assert confirmed, f"verdicts: {[(finding.finding_id, finding.verdict) for finding in findings]}"
    assert any(finding.evidence_refs for finding in confirmed)


@pytest.mark.skipif(
    not FULL_ADVANCED_SCAN,
    reason="set STATEBREAKER_ADVANCED_LAB_FULL_SCAN=1 to run every advanced lab scan",
)
@pytest.mark.parametrize(
    "spec",
    ADVANCED_FLOW_SPECS,
    ids=[spec.lab for spec in ADVANCED_FLOW_SPECS],
)
async def test_scan_each_advanced_lab_with_statebreaker(
    tmp_path, spec: BlackBoxFlowSpec
) -> None:  # type: ignore[no-untyped-def]
    findings = await _scan_spec(tmp_path, spec)

    confirmed = [finding for finding in findings if finding.verdict == "confirmed"]
    assert confirmed, f"verdicts: {[(finding.finding_id, finding.verdict) for finding in findings]}"
