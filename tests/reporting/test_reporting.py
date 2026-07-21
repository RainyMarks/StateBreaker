"""Reporting tests: PoC rendering, JSON bundle, HTML report, redaction (§15)."""

from __future__ import annotations

import json

import pytest

from statebreaker.artifacts.redaction import REDACTED, redact_mapping
from statebreaker.artifacts.store import ArtifactStore
from statebreaker.models.discovery import ActionInstance, AttackPlan, RaceCandidate
from statebreaker.models.execution import ExecutionTrial, PreparedRequest, TimelineEvent
from statebreaker.models.findings import Finding, RunStatistics
from statebreaker.reporting import (
    build_json_report,
    render_html_report,
    render_poc_script,
    write_finding_reports,
)


def _plan() -> AttackPlan:
    return AttackPlan(
        plan_id="plan-min",
        candidate_id="cand-1",
        action_instances=[
            ActionInstance(instance_id="i-0", action_id="act-a"),
            ActionInstance(instance_id="i-1", action_id="act-a"),
        ],
        scheduler="async-http",
        concurrency=2,
    )


def _trial() -> ExecutionTrial:
    return ExecutionTrial(
        trial_id="trial-attack-1",
        plan_id="plan-min",
        control_or_attack="attack",
        requests=[
            PreparedRequest(
                instance_id="i-0",
                method="POST",
                url="http://127.0.0.1:9000/do",
                headers={"content-type": "application/json"},
                body=b"{}",
            ),
            PreparedRequest(
                instance_id="i-1",
                method="POST",
                url="http://127.0.0.1:9000/do",
                headers={"content-type": "application/json"},
                body=b"{}",
            ),
        ],
        timeline=[
            TimelineEvent(instance_id="i-0", event="released", at_ns=1_000),
            TimelineEvent(instance_id="i-1", event="released", at_ns=1_500),
        ],
    )


def _finding() -> Finding:
    return Finding(
        finding_id="finding-plan-1",
        verdict="confirmed",
        confidence=0.9,
        candidate=RaceCandidate(candidate_id="cand-1", kind="same_action", action_ids=["act-a"]),
        minimized_plan_id="plan-min",
        evidence_refs=["trial-control-1", "trial-attack-1"],
        explanation=["concurrent effect exceeds sequential control"],
        success_rate=0.9,
        minimum_concurrency=2,
        best_scheduler="async-http",
        statistics=RunStatistics(rounds=10, successes=9, success_rate=0.9),
    )


# -- PoC ----------------------------------------------------------------------


def test_poc_script_compiles_and_carries_trial_requests() -> None:
    script = render_poc_script(_finding(), _plan(), _trial())
    compile(script, "poc.py", "exec")
    assert "http://127.0.0.1:9000/do" in script
    assert "trial-attack-1" in script
    assert "9/10" in script
    assert "Re-fires the exact requests" in script
    assert "真实记录的请求" in script


def test_poc_script_refuses_a_trial_without_requests() -> None:
    trial = _trial().model_copy(update={"requests": []})
    with pytest.raises(ValueError, match="no requests"):
        render_poc_script(_finding(), _plan(), trial)


# -- JSON bundle ----------------------------------------------------------------


def test_json_report_bundles_control_and_attack_groups() -> None:
    control = _trial().model_copy(
        update={"trial_id": "trial-control-1", "control_or_attack": "control"}
    )
    payload = build_json_report(_finding(), _plan(), control, [_trial()])
    assert payload["control_group"]["trial_id"] == "trial-control-1"
    assert [t["trial_id"] for t in payload["attack_group"]] == ["trial-attack-1"]
    json.dumps(payload)  # must stay JSON-serializable


def test_json_report_tolerates_missing_control() -> None:
    payload = build_json_report(_finding(), _plan(), None, [_trial()])
    assert payload["control_group"] is None


# -- HTML -----------------------------------------------------------------------


def test_html_report_renders_verdict_and_stats() -> None:
    page = render_html_report(_finding(), _plan())
    assert "CONFIRMED" in page
    assert "finding-plan-1" in page
    assert "9/10" in page
    assert "StateBreaker finding report" in page
    assert "StateBreaker finding 报告" in page


def test_redaction_masks_credentials_at_the_report_boundary() -> None:
    redacted = redact_mapping({"authorization": "Bearer abc", "note": "ok"})
    assert redacted["authorization"] == REDACTED
    assert redacted["note"] == "ok"


# -- writer ---------------------------------------------------------------------


def test_write_finding_reports_emits_all_artifacts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    store = ArtifactStore(tmp_path / "project")
    control = _trial().model_copy(
        update={"trial_id": "trial-control-1", "control_or_attack": "control"}
    )
    paths = write_finding_reports(
        store,
        _finding(),
        _plan(),
        control=control,
        attacks=[_trial()],
        poc_trial=_trial(),
    )
    assert set(paths) == {"json", "html", "poc"}
    for path in paths.values():
        assert Path(path).exists(), path
    compile(Path(paths["poc"]).read_text(encoding="utf-8"), paths["poc"], "exec")
    store.close()
