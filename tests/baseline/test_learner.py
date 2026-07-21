"""Phase 2 acceptance: baseline learning derives effects + invariants from
normal execution alone — no hand-written rules, on two different labs.
"""

from __future__ import annotations

import pytest
from support.flows import record_oneshot_flow, record_overdraw_flow
from support.recorder import LAB_BASE_URL, FlowRecorder, asgi_transport, load_lab_app

from statebreaker.baseline.learner import BaselineLearner, find_mutating_actions
from statebreaker.config.loader import ScopeGuard
from statebreaker.config.models import ProjectConfig, SessionConfig
from statebreaker.execution.client import BudgetTracker, HttpSender
from statebreaker.execution.reset import ApiResetStrategy
from statebreaker.execution.sessions import SessionManager
from statebreaker.intelligence.dependency_inference import evaluate_bindings, replay_flow
from statebreaker.intelligence.lineage import infer_bindings
from statebreaker.intelligence.normalizer import normalize_trace
from statebreaker.intelligence.probe_discovery import discover_probe_candidates
from statebreaker.intelligence.templates import build_templates
from statebreaker.intelligence.workflow_builder import build_graph
from statebreaker.models.capture import RequestTemplate
from statebreaker.models.execution import ScanBudget
from statebreaker.models.state import StateProbe


def _scope() -> ScopeGuard:
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "lab", "base_url": LAB_BASE_URL},
            "scope": {"allowed_hosts": ["lab.local"]},
        }
    )
    return ScopeGuard(config)


def test_fixed_path_mutation_is_learned_when_state_probe_follows() -> None:
    action = RequestTemplate(
        template_id="withdraw",
        method="POST",
        path_template="/accounts/alice/withdraw",
    )
    probe_template = RequestTemplate(
        template_id="account",
        method="GET",
        path_template="/accounts/alice",
    )
    probe = StateProbe(probe_id="probe-account", request_template=probe_template)

    actions = find_mutating_actions([action, probe_template], probes=[probe])

    assert [item.template.template_id for item in actions] == ["withdraw"]


def test_fixed_path_mutation_without_probe_is_not_learned() -> None:
    action = RequestTemplate(
        template_id="withdraw",
        method="POST",
        path_template="/accounts/alice/withdraw",
    )

    assert find_mutating_actions([action], probes=[]) == []


async def _prepare(lab: str, record_flow) -> tuple:  # type: ignore[no-untyped-def]
    recorder = FlowRecorder(load_lab_app(lab))
    await record_flow(recorder)
    trace = normalize_trace(recorder.trace(), base_url=LAB_BASE_URL)
    await recorder.aclose()

    bindings = infer_bindings(trace.exchanges)
    templates = build_templates(trace.exchanges, bindings)
    graph = build_graph(trace, bindings, graph_id="graph-baseline")

    sessions = SessionManager(
        LAB_BASE_URL,
        {"alice": SessionConfig(headers={"X-User-Id": "alice"})},
        transport=asgi_transport(load_lab_app(lab)),
    )
    sender = HttpSender(
        sessions,
        _scope(),
        budget=BudgetTracker(ScanBudget(maximum_requests=500, requests_per_second=1000)),
        requests_per_second=1000,
    )
    replay = await replay_flow(templates, bindings, sender, session_id="alice")
    confirmed = evaluate_bindings(bindings, replay)
    graph = build_graph(trace, confirmed, graph_id="graph-baseline")
    probes = discover_probe_candidates(graph)
    return graph, templates, probes, sessions, sender


@pytest.mark.parametrize(
    ("lab", "record_flow", "expect_transition"),
    [
        ("lab-oneshot-redemption", record_oneshot_flow, True),
        ("lab-overdraw", record_overdraw_flow, False),
    ],
)
async def test_baseline_learning(lab, record_flow, expect_transition) -> None:  # type: ignore[no-untyped-def]
    graph, templates, probes, sessions, sender = await _prepare(lab, record_flow)
    reset = ApiResetStrategy(sender, "/__test__/reset", session_id="alice")
    learner = BaselineLearner(sender, reset, session_id="alice")

    profile, trials = await learner.learn(
        graph=graph,
        templates=templates,
        probes=probes,
        capture_id="cap-baseline",
    )
    await sessions.aclose()

    # experiment battery ran: control + single + sequential per mutating action
    roles = [trial.control_or_attack for trial in trials]
    assert "control" in roles
    assert roles.count("baseline") >= 2

    # every action got an effect; first execution succeeded
    assert profile.effects, "expected learned operation effects"
    for effect in profile.effects:
        assert effect.success_probability == 1.0
        assert effect.state_changes, f"no state change learned for {effect.action_id}"
        assert effect.supporting_trial_ids

    # control trials show zero business change (sanity of the harness itself)
    for trial in trials:
        if trial.control_or_attack != "control":
            continue
        for before, after in zip(trial.before_state, trial.after_state, strict=False):
            if before.normalized and after.normalized:
                assert before.normalized.fields == after.normalized.fields

    invariant_types = {invariant.invariant_type for invariant in profile.invariants}
    assert "one_shot" in invariant_types  # sequential repeat has no further effect
    assert "numeric_bound" in invariant_types  # numeric deltas have a learned max
    if expect_transition:
        assert "state_transition" in invariant_types
    else:
        assert "lower_bound" in invariant_types  # balance floor learned from samples

    # every learned rule is backed by real trials (spec §27.10)
    for invariant in profile.invariants:
        assert invariant.supporting_trials, invariant.invariant_id
        known = {trial.trial_id for trial in trials}
        assert set(invariant.supporting_trials) <= known

    # one-shot semantics were learned from behavior, not from names
    one_shots = [e for e in profile.effects if e.repeat_behavior == "once"]
    assert one_shots, f"effects: {[(e.action_id, e.repeat_behavior) for e in profile.effects]}"
