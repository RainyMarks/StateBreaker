"""Candidate discovery unit tests: filter, score, pair generation (spec §9).

Normal groups: state-changing one-shot actions are kept, scored, paired.
Anomaly groups: reads, effect-less actions, and unshared pairs are dropped.
"""

from __future__ import annotations

from statebreaker.discovery.candidate_filter import filter_candidates, is_race_worthy
from statebreaker.discovery.candidate_ranker import score_action
from statebreaker.discovery.pair_generator import generate_candidates
from statebreaker.models.capture import RequestTemplate
from statebreaker.models.state import FieldChange, OperationEffect
from statebreaker.models.workflow import DependencyEdge, WorkflowGraph


def _template(template_id: str, method: str = "POST", path: str = "/do") -> RequestTemplate:
    return RequestTemplate(template_id=template_id, method=method, path_template=path)


def _effect(
    action_id: str,
    *,
    repeat: str = "once",
    numeric: bool = True,
    success: float = 1.0,
    changes: int = 1,
) -> OperationEffect:
    state_changes = [
        FieldChange(path=f"field.{index}", before=0, after=1, delta=1.0 if numeric else None)
        for index in range(changes)
    ]
    return OperationEffect(
        action_id=action_id,
        state_changes=state_changes,
        success_probability=success,
        repeat_behavior=repeat,  # type: ignore[arg-type]
    )


def _graph(consumes: list[tuple[str, str]]) -> WorkflowGraph:
    return WorkflowGraph(
        graph_id="g",
        capture_id="c",
        dependencies=[
            DependencyEdge(edge_type="consumes", source_id=source, target_id=target)
            for source, target in consumes
        ],
    )


# -- filter --------------------------------------------------------------------


def test_filter_keeps_mutating_action_with_effect() -> None:
    template = _template("a")
    worthy = filter_candidates([template], [_effect("a")])
    assert [t.template_id for t, _ in worthy] == ["a"]


def test_filter_drops_reads_and_effectless_actions() -> None:
    get_template = _template("read", method="GET")
    post_template = _template("write")
    assert not is_race_worthy(get_template, _effect("read"))
    assert not is_race_worthy(post_template, None)
    assert not is_race_worthy(post_template, _effect("write", changes=0))
    assert not is_race_worthy(post_template, _effect("write", success=0.0))


# -- scoring -------------------------------------------------------------------


def test_score_rewards_one_shot_numeric_actions() -> None:
    score, breakdown = score_action(
        _template("a", path="/do/${rid}"), _effect("a"), None, session_count=2
    )
    assert score > 5.0
    assert breakdown["single_use_signal"] == 2.0
    assert breakdown["numeric_boundary_signal"] == 1.5
    assert breakdown["shared_resource_score"] == 1.5


def test_score_penalizes_unstable_and_idempotent_actions() -> None:
    unstable, breakdown = score_action(
        _template("a"), _effect("a", repeat="unstable"), None, session_count=1
    )
    assert breakdown["instability_penalty"] == -2.0
    idempotent, _ = score_action(
        _template("a"), _effect("a", repeat="idempotent"), None, session_count=1
    )
    assert unstable < idempotent


# -- generation ------------------------------------------------------------------


def test_generate_candidates_emits_same_action_and_cross_user() -> None:
    templates = [_template("a", path="/do/${rid}")]
    graph = _graph([("a", "resource-rid")])
    candidates = generate_candidates(
        graph, templates, [_effect("a")], sessions=["alice", "bob"]
    )
    kinds = {candidate.kind for candidate in candidates}
    assert "same_action" in kinds or "quota" in kinds
    assert "cross_user" in kinds


def test_generate_candidates_pairs_actions_sharing_a_resource() -> None:
    templates = [_template("a", path="/do/${rid}"), _template("b", path="/undo/${rid}")]
    graph = _graph([("a", "resource-rid"), ("b", "resource-rid")])
    candidates = generate_candidates(
        graph, templates, [_effect("a"), _effect("b")], sessions=["alice"]
    )
    cross = [candidate for candidate in candidates if candidate.kind == "cross_action"]
    assert len(cross) == 1
    assert cross[0].action_ids == ["a", "b"]
    assert cross[0].resource_ids == ["resource-rid"]


def test_generate_candidates_never_pairs_unshared_actions() -> None:
    templates = [_template("a", path="/do/${x}"), _template("b", path="/undo/${y}")]
    graph = _graph([("a", "resource-x"), ("b", "resource-y")])
    candidates = generate_candidates(
        graph, templates, [_effect("a"), _effect("b")], sessions=["alice"]
    )
    assert not [candidate for candidate in candidates if candidate.kind == "cross_action"]


def test_generate_candidates_respects_max_candidates() -> None:
    templates = [_template(f"a{index}") for index in range(10)]
    graph = _graph([])
    candidates = generate_candidates(
        graph,
        templates,
        [_effect(f"a{index}") for index in range(10)],
        sessions=["alice"],
        max_candidates=3,
    )
    assert len(candidates) == 3
