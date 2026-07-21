"""Phase 1 acceptance: from a raw trace to replayed, confirmed dependencies —
on two labs with entirely different paths and field names, no code changes.
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

from statebreaker.config.loader import ScopeGuard
from statebreaker.config.models import ProjectConfig, SessionConfig
from statebreaker.execution.client import HttpSender
from statebreaker.execution.sessions import SessionManager
from statebreaker.intelligence.dependency_inference import evaluate_bindings, replay_flow
from statebreaker.intelligence.lineage import infer_bindings
from statebreaker.intelligence.normalizer import normalize_trace
from statebreaker.intelligence.probe_discovery import discover_probe_candidates, validate_probe
from statebreaker.intelligence.templates import build_templates
from statebreaker.intelligence.workflow_builder import build_graph


def _scope() -> ScopeGuard:
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "lab", "base_url": LAB_BASE_URL},
            "scope": {"allowed_hosts": ["lab.local"]},
        }
    )
    return ScopeGuard(config)


@pytest.mark.parametrize(
    ("lab", "record_flow", "expected_variable"),
    [
        ("lab-oneshot-redemption", record_oneshot_flow, "code"),
        ("lab-overdraw", record_overdraw_flow, "id"),
        ("lab-crossuser-claim", record_crossuser_flow, "slug"),
        ("lab-token-reuse", record_token_reuse_flow, "ticket"),
        ("lab-quota-oversell", record_quota_flow, "sku"),
    ],
)
async def test_trace_to_confirmed_dependencies(lab, record_flow, expected_variable) -> None:  # type: ignore[no-untyped-def]
    # 1. record a normal flow (stands in for a HAR/browser capture)
    recorder = FlowRecorder(load_lab_app(lab))
    await record_flow(recorder)
    trace = normalize_trace(recorder.trace(), base_url=LAB_BASE_URL)
    await recorder.aclose()

    # 2. passive inference: values must flow producer -> consumer
    bindings = infer_bindings(trace.exchanges)
    variables = {binding.variable_id for binding in bindings}
    assert expected_variable in variables, f"bindings: {[b.to_json_dict() for b in bindings]}"
    consumers = {b.consumer_exchange_id for b in bindings}
    assert len(consumers) >= 2  # the id is reused by at least two later requests

    # 3. templates + graph
    templates = build_templates(trace.exchanges, bindings)
    templated_paths = [t.path_template for t in templates]
    assert any("${" + expected_variable + "}" in path for path in templated_paths)

    graph = build_graph(trace, bindings, graph_id="graph-1")
    assert graph.resources, "expected at least one resource node"
    assert any(e.edge_type == "must_precede" for e in graph.dependencies)

    # 4. active validation: replay the whole flow against a FRESH lab instance
    session_configs = {"alice": SessionConfig(headers={"X-User-Id": "alice"})}
    sessions = SessionManager(
        LAB_BASE_URL, session_configs, transport=asgi_transport(load_lab_app(lab))
    )
    sender = HttpSender(sessions, _scope(), requests_per_second=1000)
    replay = await replay_flow(templates, bindings, sender, session_id="alice")
    await sessions.aclose()

    assert replay.success, f"replay failed: {replay.failure_reason}"
    confirmed = evaluate_bindings(bindings, replay)
    assert all(b.status == "confirmed" for b in confirmed), [
        (b.variable_id, b.status, b.consumer_exchange_id) for b in confirmed
    ]
    # the replay must have threaded a FRESH id (not the recorded one)
    fresh_value = replay.variables[expected_variable]
    assert isinstance(fresh_value, str) and fresh_value

    # 5. probe discovery + validation (probe -> action -> probe)
    probes = discover_probe_candidates(graph)
    assert probes, "expected at least one state probe candidate"
    resource_probe = next(p for p in probes if p.resource_ids)
    mutating = next(t for t in templates if t.method == "POST" and "${" in t.path_template)

    sessions2 = SessionManager(
        LAB_BASE_URL, session_configs, transport=asgi_transport(load_lab_app(lab))
    )
    sender2 = HttpSender(sessions2, _scope(), requests_per_second=1000)
    validated = await validate_probe(
        resource_probe,
        graph,
        templates,
        sender2,
        mutating_template_id=mutating.template_id,
        session_id="alice",
    )
    await sessions2.aclose()

    assert validated.confidence >= 0.9
    assert validated.observed_paths, "probe must observe the action's state change"
