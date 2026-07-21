"""Phase 3/6 acceptance: the AutoRaceScanner discovers and confirms races with
zero hand-written rules — on five labs with different paths and field names.

This is the spec §28 demo: trace in, CONFIRMED finding out, with control and
attack groups, state evidence, and a success rate.
"""

from __future__ import annotations

import pytest
from support.flows import (
    record_crossuser_flow,
    record_oneshot_flow,
    record_overdraw_flow,
    record_quota_flow,
    record_token_reuse_flow,
)
from support.recorder import LAB_BASE_URL, FlowRecorder, asgi_transport, load_lab_app

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.config.models import ProjectConfig
from statebreaker.models.execution import ScanBudget
from statebreaker.models.findings import Finding
from statebreaker.orchestration.scanner import AutoRaceScanner


def _project() -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project": {"name": "lab-scan", "base_url": LAB_BASE_URL},
            "scope": {"allowed_hosts": ["lab.local"], "requests_per_second": 1000},
            "sessions": {"alice": {}, "bob": {"headers": {"X-User-Id": "bob"}}},
            "reset": {"strategy": "api", "endpoint": "/__test__/reset"},
            "discovery": {"max_candidates": 6, "max_action_pairs": 4},
            "execution": {
                "schedulers": ["async-http"],
                "concurrency": [2, 4],
                "repetitions": 3,
            },
            "budget": {
                "maximum_requests": 1500,
                "maximum_trial_rounds": 200,
                "maximum_minutes": 5.0,
            },
        }
    )


def _budget() -> ScanBudget:
    return ScanBudget(
        maximum_requests=1500,
        maximum_trial_rounds=200,
        maximum_minutes=5.0,
        requests_per_second=1000,
        max_candidates=6,
        max_action_pairs=4,
    )


@pytest.mark.parametrize(
    ("lab", "record_flow", "violated_types"),
    [
        ("lab-oneshot-redemption", record_oneshot_flow, {"one_shot", "numeric_bound"}),
        ("lab-overdraw", record_overdraw_flow, {"lower_bound", "numeric_bound"}),
        ("lab-crossuser-claim", record_crossuser_flow, {"one_shot", "numeric_bound"}),
        ("lab-token-reuse", record_token_reuse_flow, {"one_shot", "numeric_bound"}),
        ("lab-quota-oversell", record_quota_flow, {"one_shot", "numeric_bound"}),
    ],
)
async def test_scan_confirms_race(tmp_path, lab, record_flow, violated_types) -> None:  # type: ignore[no-untyped-def]
    # capture: only a normal flow, recorded once — nothing else is provided
    recorder = FlowRecorder(load_lab_app(lab))
    await record_flow(recorder)
    trace = recorder.trace(project="lab-scan")
    await recorder.aclose()

    store = ArtifactStore(tmp_path / "project")
    store.save("captures", trace.capture_id, trace)

    scanner = AutoRaceScanner(store, transport=asgi_transport(load_lab_app(lab)))
    outcome = await scanner.scan(_project(), capture_id=trace.capture_id, budget=_budget())

    assert outcome.status == "completed"
    assert outcome.graph_id
    assert outcome.baseline_id
    assert outcome.candidate_ids, "expected race candidates"
    assert outcome.plan_ids, "expected attack plans"

    findings = [store.load("findings", fid, Finding) for fid in outcome.finding_ids]
    confirmed = [finding for finding in findings if finding.verdict == "confirmed"]
    assert confirmed, f"verdicts: {[(f.finding_id, f.verdict) for f in findings]}"

    best = max(confirmed, key=lambda finding: finding.confidence)
    assert best.success_rate is not None and best.success_rate >= 2 / 3
    assert best.confidence >= 0.8
    assert best.explanation, "a confirmed finding must explain itself"

    # evidence traceability: control + attack trials all exist on disk (§27.9)
    assert len(best.evidence_refs) >= 2
    for trial_id in best.evidence_refs:
        assert store.exists("trials", trial_id), trial_id

    # Phase 5: confirmed findings are minimized, measured, and reported (§14/§15)
    assert best.minimized_plan_id is not None
    assert store.exists("plans", best.minimized_plan_id)
    assert best.minimum_concurrency is not None and best.minimum_concurrency >= 2
    assert best.best_scheduler == "async-http"
    assert best.statistics is not None and best.statistics.rounds >= 1
    poc_files = list((tmp_path / "project" / "reports").glob("*-poc.py"))
    assert poc_files, "confirmed findings must emit an executable PoC"
    compile(poc_files[0].read_text(encoding="utf-8"), str(poc_files[0]), "exec")

    # the verdict is backed by violated learned rules of the expected kind
    from statebreaker.models.state import BaselineProfile

    baseline = store.load("baselines", outcome.baseline_id, BaselineProfile)
    invariant_by_id = {inv.invariant_id: inv for inv in baseline.invariants}
    violated: set[str] = set()
    for finding in confirmed:
        for invariant_id in finding.violated_invariant_ids:
            if invariant_id in invariant_by_id:
                violated.add(invariant_by_id[invariant_id].invariant_type)
    assert violated & violated_types, f"violated: {violated}"

    store.close()
