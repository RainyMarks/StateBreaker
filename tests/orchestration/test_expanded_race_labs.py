"""Regression coverage for fixed-path and header-key race labs."""

from __future__ import annotations

import pytest
from support.recorder import LAB_BASE_URL, FlowRecorder, asgi_transport, load_lab_app

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.config.models import ProjectConfig
from statebreaker.models.capture import CapturedTrace, HttpExchange
from statebreaker.models.execution import ScanBudget
from statebreaker.models.findings import Finding
from statebreaker.orchestration.scanner import AutoRaceScanner
from statebreaker.orchestration.stages import session_configs


def _project() -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project": {"name": "expanded-lab-scan", "base_url": LAB_BASE_URL},
            "scope": {"allowed_hosts": ["lab.local"], "requests_per_second": 1000},
            "reset": {"strategy": "api", "endpoint": "/__test__/reset"},
            "discovery": {"max_candidates": 8, "max_action_pairs": 4},
            "execution": {
                "schedulers": ["async-http"],
                "concurrency": [2, 4],
                "repetitions": 3,
            },
            "budget": {
                "maximum_requests": 1800,
                "maximum_trial_rounds": 250,
                "maximum_minutes": 5.0,
            },
        }
    )


def _budget() -> ScanBudget:
    return ScanBudget(
        maximum_requests=1800,
        maximum_trial_rounds=250,
        maximum_minutes=5.0,
        requests_per_second=1000,
        max_candidates=8,
        max_action_pairs=4,
    )


def test_default_capture_identity_headers_seed_primary_session() -> None:
    project = ProjectConfig.model_validate(
        {
            "project": {"name": "identity", "base_url": LAB_BASE_URL},
            "sessions": {"alice": {}, "bob": {}},
        }
    )
    trace = CapturedTrace(
        capture_id="identity-cap",
        source="proxy",
        exchanges=[
            HttpExchange(
                exchange_id="http-1",
                session_id="default",
                method="POST",
                url=f"{LAB_BASE_URL}/action",
                request_headers={"x-user-id": "alice"},
            )
        ],
    )

    configs = session_configs(project, trace)

    assert configs["alice"].headers == {"x-user-id": "alice"}
    assert configs["bob"].headers == {}


def test_default_capture_cookies_seed_primary_session() -> None:
    project = ProjectConfig.model_validate(
        {
            "project": {"name": "identity", "base_url": LAB_BASE_URL},
            "sessions": {"alice": {}, "bob": {}},
        }
    )
    trace = CapturedTrace(
        capture_id="cookie-cap",
        source="browser",
        exchanges=[
            HttpExchange(
                exchange_id="browser-1",
                session_id="default",
                method="POST",
                url=f"{LAB_BASE_URL}/action",
                request_headers={"cookie": "PHPSESSID=abc123; lab=securify"},
            )
        ],
    )

    configs = session_configs(project, trace)

    assert configs["alice"].cookies == {"PHPSESSID": "abc123", "lab": "securify"}
    assert configs["bob"].cookies == {}


async def _record_wallet_flow(recorder: FlowRecorder) -> None:
    await recorder.record("POST", "/accounts/alice/deposit", json_body={"amount": 100})
    await recorder.record("GET", "/accounts/alice")
    await recorder.record("POST", "/accounts/alice/withdraw", json_body={"amount": 60})
    await recorder.record("GET", "/accounts/alice")


async def _record_idempotency_flow(recorder: FlowRecorder) -> None:
    headers = {"Idempotency-Key": "idem-race-regression-1001"}
    await recorder.record(
        "POST",
        "/orders",
        headers=headers,
        json_body={"sku": "widget", "quantity": 1},
    )
    await recorder.record("GET", "/orders")
    await recorder.record("GET", "/idempotency/idem-race-regression-1001")


@pytest.mark.parametrize(
    ("lab", "record_flow"),
    [
        ("lab-race-wallet-double-spend", _record_wallet_flow),
        ("lab-race-idempotency-reuse", _record_idempotency_flow),
    ],
)
async def test_scan_confirms_fixed_path_and_header_key_races(
    tmp_path,
    lab: str,
    record_flow,
) -> None:  # type: ignore[no-untyped-def]
    recorder = FlowRecorder(load_lab_app(lab), capture_id=f"{lab}-capture")
    await record_flow(recorder)
    trace = recorder.trace(project="expanded-lab-scan")
    await recorder.aclose()

    store = ArtifactStore(tmp_path / "project")
    store.save("captures", trace.capture_id, trace)

    scanner = AutoRaceScanner(store, transport=asgi_transport(load_lab_app(lab)))
    outcome = await scanner.scan(_project(), capture_id=trace.capture_id, budget=_budget())

    assert outcome.status == "completed"
    assert outcome.candidate_ids, "expected fixed-path/header-key race candidates"
    assert outcome.plan_ids, "expected attack plans"
    findings = [store.load("findings", fid, Finding) for fid in outcome.finding_ids]
    confirmed = [finding for finding in findings if finding.verdict == "confirmed"]
    assert confirmed, f"verdicts: {[(f.finding_id, f.verdict) for f in findings]}"
    assert any(finding.success_rate for finding in confirmed)
