"""Every v0.2 model must round-trip through JSON and be fixture-constructible."""

from __future__ import annotations

from typing import Any

import pytest

from statebreaker.models.base import ContractModel
from statebreaker.models.capture import (
    BrowserAction,
    CapturedTrace,
    DomEvent,
    HttpExchange,
    RequestTemplate,
)
from statebreaker.models.discovery import ActionInstance, AttackPlan, RaceCandidate
from statebreaker.models.execution import (
    ExecutionTrial,
    HttpResponseRecord,
    PreparedRace,
    PreparedRequest,
    ScanBudget,
    TimelineEvent,
    TrialContext,
)
from statebreaker.models.findings import EvidenceBundle, Finding, ScanOutcome
from statebreaker.models.state import (
    BaselineProfile,
    FieldChange,
    LearnedInvariant,
    NormalizedState,
    OperationEffect,
    ResponseSignature,
    StateProbe,
    StateSnapshot,
)
from statebreaker.models.workflow import (
    ActionNode,
    DependencyEdge,
    ResourceNode,
    VariableBinding,
    WorkflowGraph,
)


def _template() -> RequestTemplate:
    return RequestTemplate(
        template_id="tpl-1",
        method="POST",
        path_template="/api/things/${thing_id}/act",
        query={"verbose": "1"},
        headers={"X-Requested-With": "test"},
        body={"amount": 10},
        body_encoding="json",
    )


def _exchange() -> HttpExchange:
    return HttpExchange(
        exchange_id="exchange-1",
        action_id="action-1",
        session_id="alice",
        method="POST",
        url="http://127.0.0.1:8080/api/things",
        request_headers={"Content-Type": "application/json"},
        request_body={"name": "x"},
        request_body_encoding="json",
        response_status=200,
        response_headers={"Content-Type": "application/json"},
        response_body={"thing": {"id": "abc-123"}},
        response_body_encoding="json",
        started_at_ns=1,
        completed_at_ns=2,
    )


def _candidate() -> RaceCandidate:
    return RaceCandidate(
        candidate_id="cand-1",
        kind="same_action",
        action_ids=["action-1"],
        resource_ids=["resource-1"],
        score=7.5,
        score_breakdown={"single_use_signal": 3.0, "state_change_score": 4.5},
        rationale=["sequential repeat shows one-shot semantics"],
    )


def _plan() -> AttackPlan:
    return AttackPlan(
        plan_id="plan-1",
        candidate_id="cand-1",
        action_instances=[
            ActionInstance(
                instance_id="inst-1",
                action_id="action-1",
                session_id="alice",
                exchange_templates=[_template()],
            )
        ],
        sessions=["alice"],
        scheduler="async-http",
        concurrency=2,
        offsets_ms=[0.0],
        state_probe_ids=["probe-1"],
    )


FIXTURES: list[tuple[type[ContractModel], dict[str, Any]]] = [
    (DomEvent, {"type": "click", "selector": "#go", "visible_text": "Go"}),
    (
        BrowserAction,
        {
            "action_id": "action-1",
            "session_id": "alice",
            "dom_event": {"type": "click", "selector": "#go"},
            "page_url": "http://127.0.0.1:8080/shop",
            "triggered_exchange_ids": ["exchange-1"],
        },
    ),
    (HttpExchange, _exchange().to_json_dict()),
    (
        CapturedTrace,
        {
            "capture_id": "cap-1",
            "source": "har",
            "project": "demo",
            "sessions": ["alice"],
            "exchanges": [_exchange().to_json_dict()],
        },
    ),
    (RequestTemplate, _template().to_json_dict()),
    (
        VariableBinding,
        {
            "variable_id": "thing_id",
            "producer_exchange_id": "exchange-1",
            "producer_selector": "json:$.thing.id",
            "consumer_exchange_id": "exchange-2",
            "consumer_location": "path",
            "value_type": "uuid",
            "confidence": 0.9,
            "status": "confirmed",
        },
    ),
    (
        ResourceNode,
        {
            "resource_id": "resource-1",
            "variable_id": "thing_id",
            "producer_exchange_id": "exchange-1",
            "consumer_exchange_ids": ["exchange-2"],
        },
    ),
    (
        DependencyEdge,
        {
            "edge_type": "consumes",
            "source_id": "exchange-2",
            "target_id": "resource-1",
            "confidence": 0.8,
            "evidence": ["exchange-1"],
        },
    ),
    (
        ActionNode,
        {"action_id": "action-1", "session_id": "alice", "exchange_ids": ["exchange-1"]},
    ),
    (
        StateProbe,
        {
            "probe_id": "probe-1",
            "request_template": _template().to_json_dict(),
            "resource_ids": ["resource-1"],
            "observed_paths": ["$.thing.state"],
            "confidence": 0.7,
        },
    ),
    (
        WorkflowGraph,
        {
            "graph_id": "graph-1",
            "capture_id": "cap-1",
            "actions": [
                {"action_id": "action-1", "session_id": "alice", "exchange_ids": ["exchange-1"]}
            ],
            "exchanges": [_exchange().to_json_dict()],
        },
    ),
    (NormalizedState, {"fields": {"$.thing.state": "active"}, "ignored_paths": ["$.ts"]}),
    (
        StateSnapshot,
        {
            "snapshot_id": "snap-1",
            "probe_id": "probe-1",
            "taken_at_ns": 5,
            "raw": {"thing": {"state": "active"}},
            "normalized": {"fields": {"$.thing.state": "active"}},
        },
    ),
    (FieldChange, {"path": "$.thing.state", "before": "new", "after": "active"}),
    (ResponseSignature, {"status": 200, "body_shape": "abc", "semantic_class": "success"}),
    (
        OperationEffect,
        {
            "action_id": "action-1",
            "state_changes": [{"path": "$.thing.state", "before": "new", "after": "active"}],
            "response_signature": {"status": 200},
            "success_probability": 1.0,
            "repeat_behavior": "once",
        },
    ),
    (
        LearnedInvariant,
        {
            "invariant_id": "inv-1",
            "invariant_type": "one_shot",
            "target_paths": ["$.thing.state"],
            "parameters": {"terminal": "active"},
            "confidence": 0.9,
            "supporting_trials": ["trial-1"],
        },
    ),
    (
        BaselineProfile,
        {"profile_id": "base-1", "capture_id": "cap-1", "probe_ids": ["probe-1"]},
    ),
    (RaceCandidate, _candidate().to_json_dict()),
    (
        ActionInstance,
        {
            "instance_id": "inst-1",
            "action_id": "action-1",
            "session_id": "alice",
            "exchange_templates": [_template().to_json_dict()],
        },
    ),
    (AttackPlan, _plan().to_json_dict()),
    (ScanBudget, {"maximum_requests": 100, "maximum_minutes": 5.0}),
    (TimelineEvent, {"instance_id": "inst-1", "event": "released", "at_ns": 42}),
    (
        HttpResponseRecord,
        {"instance_id": "inst-1", "status": 200, "body": {"ok": True}},
    ),
    (
        ExecutionTrial,
        {
            "trial_id": "trial-1",
            "candidate_id": "cand-1",
            "plan_id": "plan-1",
            "control_or_attack": "attack",
        },
    ),
    (TrialContext, {"context_id": "ctx-1", "variables": {"thing_id": "abc"}}),
    (
        PreparedRequest,
        {"instance_id": "inst-1", "method": "POST", "url": "http://127.0.0.1/x"},
    ),
    (
        PreparedRace,
        {
            "race_id": "race-1",
            "scheduler": "async-http",
            "requests": [
                {"instance_id": "inst-1", "method": "POST", "url": "http://127.0.0.1/x"}
            ],
        },
    ),
    (
        EvidenceBundle,
        {"bundle_id": "bundle-1", "trial_ids": ["trial-1"], "summary": {"delta": 2}},
    ),
    (
        Finding,
        {
            "finding_id": "finding-1",
            "verdict": "confirmed",
            "confidence": 0.95,
            "candidate": _candidate().to_json_dict(),
            "minimized_plan": _plan().to_json_dict(),
            "evidence_refs": ["bundle-1"],
            "explanation": ["concurrent effect exceeds sequential baseline"],
            "success_rate": 0.9,
            "minimum_concurrency": 2,
            "best_scheduler": "async-http",
        },
    ),
    (
        ScanOutcome,
        {"scan_id": "scan-1", "project": "demo", "capture_id": "cap-1", "status": "completed"},
    ),
]


MODEL_IDS = [model_type.__name__ for model_type, _ in FIXTURES]


@pytest.mark.parametrize(("model_type", "payload"), FIXTURES, ids=MODEL_IDS)
def test_model_json_roundtrip(model_type: type[ContractModel], payload: dict[str, Any]) -> None:
    instance = model_type.from_json_dict(payload)
    assert instance.schema_version == "0.2"
    restored = model_type.from_json(instance.to_json())
    assert restored == instance


@pytest.mark.parametrize(("model_type", "payload"), FIXTURES, ids=MODEL_IDS)
def test_model_forbids_unknown_fields(
    model_type: type[ContractModel], payload: dict[str, Any]
) -> None:
    with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
        model_type.from_json_dict({**payload, "surprise_field": 1})
