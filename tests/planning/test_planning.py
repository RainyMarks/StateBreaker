"""Plan synthesis unit tests: templates -> scheduled attack plans (spec §10).

Normal groups: same-action bursts, cross-user pairs, cross-action pairs.
Anomaly groups: missing templates yield no plan; budgets trim the tail.
"""

from __future__ import annotations

from statebreaker.models.capture import RequestTemplate
from statebreaker.models.discovery import RaceCandidate
from statebreaker.models.execution import ScanBudget
from statebreaker.planning.budget import affordable_plans, estimate_requests
from statebreaker.planning.synthesizer import synthesize_plans


def _template(template_id: str, path: str = "/do") -> RequestTemplate:
    return RequestTemplate(template_id=template_id, method="POST", path_template=path)


def _candidate(kind: str, action_ids: list[str]) -> RaceCandidate:
    return RaceCandidate(
        candidate_id=f"cand-{kind}-{'-'.join(action_ids)}",
        kind=kind,  # type: ignore[arg-type]
        action_ids=action_ids,
    )


def _synthesize(candidates: list[RaceCandidate], templates: list[RequestTemplate], **overrides):  # type: ignore[no-untyped-def]
    kwargs = {
        "probe_ids": ["probe-1"],
        "schedulers": ["async-http"],
        "concurrencies": [2, 4],
        "offsets_ms": [0.0],
        "reset_strategy": "fresh-resource",
        "sessions": ["alice", "bob"],
    }
    kwargs.update(overrides)
    return synthesize_plans(candidates, templates, **kwargs)


# -- same action ---------------------------------------------------------------


def test_same_action_candidate_yields_one_plan_per_concurrency() -> None:
    plans = _synthesize([_candidate("same_action", ["a"])], [_template("a")])
    assert [plan.concurrency for plan in plans] == [2, 4]
    burst = plans[1]
    assert len(burst.action_instances) == 4
    assert all(instance.session_id == "alice" for instance in burst.action_instances)
    assert burst.state_probe_ids == ["probe-1"]


def test_setup_chain_is_the_template_prefix() -> None:
    templates = [_template("create"), _template("act", path="/do/${rid}")]
    plans = _synthesize([_candidate("same_action", ["act"])], templates)
    assert plans[0].setup_action_ids == ["create"]


def test_speculative_fixed_request_does_not_replay_unneeded_prefix() -> None:
    templates = [_template("login"), _template("act")]
    candidate = _candidate("same_action", ["act"])
    candidate = candidate.model_copy(update={"candidate_id": "cand-speculative-act"})
    plans = _synthesize([candidate], templates)
    assert plans[0].setup_action_ids == []


def test_same_action_plan_uses_form_variant_hints() -> None:
    template = RequestTemplate(
        template_id="transfer",
        method="POST",
        path_template="/run",
        body={
            "payload": '{"from":"primary","to":"first","amount":"100"}',
        },
        body_encoding="form",
        variant_hints={"body.payload.to": ["first", "second"]},
    )

    plans = _synthesize(
        [_candidate("same_action", ["transfer"])],
        [template],
        concurrencies=[2],
    )

    bodies = [
        instance.exchange_templates[0].body
        for instance in plans[0].action_instances
    ]
    assert bodies[0] == {"payload": '{"from":"primary","to":"first","amount":"100"}'}
    assert bodies[1] == {"payload": '{"from":"primary","to":"second","amount":"100"}'}


# -- cross user ------------------------------------------------------------------


def test_cross_user_plan_pairs_two_sessions() -> None:
    plans = _synthesize([_candidate("cross_user", ["act"])], [_template("act")])
    assert len(plans) == 1
    sessions = [instance.session_id for instance in plans[0].action_instances]
    assert sessions == ["alice", "bob"]


def test_cross_user_plan_needs_two_sessions() -> None:
    plans = _synthesize(
        [_candidate("cross_user", ["act"])], [_template("act")], sessions=["alice"]
    )
    assert plans == []


# -- cross action -----------------------------------------------------------------


def test_cross_action_plan_combines_two_templates() -> None:
    templates = [
        _template("create"),
        _template("a", path="/a/${rid}"),
        _template("b", path="/b/${rid}"),
    ]
    plans = _synthesize([_candidate("cross_action", ["a", "b"])], templates)
    assert len(plans) == 1
    plan = plans[0]
    assert [instance.action_id for instance in plan.action_instances] == ["a", "b"]
    # setup union of both prefixes, deduplicated, in trace order
    assert plan.setup_action_ids == ["create", "a"]


def test_unknown_action_ids_produce_no_plan() -> None:
    plans = _synthesize([_candidate("cross_action", ["a", "ghost"])], [_template("a")])
    assert plans == []


# -- budget ------------------------------------------------------------------------


def test_estimate_requests_counts_setup_probes_and_fire() -> None:
    plans = _synthesize([_candidate("same_action", ["a"])], [_template("a")])
    plan = plans[0]  # concurrency 2, no setup, one probe
    assert estimate_requests(plan, repetitions=3) == (2 + 2) * 4


def test_affordable_plans_trims_to_the_request_budget() -> None:
    plans = _synthesize([_candidate("same_action", ["a"])], [_template("a")])
    assert len(plans) == 2
    kept = affordable_plans(plans, ScanBudget(maximum_requests=1), repetitions=3)
    # the first plan always fits (a scan with zero plans is never useful);
    # the more expensive burst does not
    assert kept == plans[:1]


def test_affordable_plans_keeps_everything_under_a_generous_budget() -> None:
    plans = _synthesize([_candidate("same_action", ["a"])], [_template("a")])
    kept = affordable_plans(plans, ScanBudget(maximum_requests=10_000), repetitions=3)
    assert kept == plans
