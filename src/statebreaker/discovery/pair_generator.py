"""Generate race candidates from scored actions — with explosion guards.

A || B pairs are only produced when A and B share a resource or sit within
graph distance 2 (spec §9.5); never a cartesian product.
"""

from __future__ import annotations

from statebreaker.baseline.learner import MUTATING_METHODS
from statebreaker.discovery.candidate_filter import filter_candidates
from statebreaker.discovery.candidate_ranker import score_action
from statebreaker.models.capture import RequestTemplate
from statebreaker.models.discovery import CandidateKind, RaceCandidate
from statebreaker.models.state import OperationEffect
from statebreaker.models.workflow import WorkflowGraph


def _classify(effect: OperationEffect) -> CandidateKind:
    has_numeric = any(change.delta is not None for change in effect.state_changes)
    if has_numeric and effect.repeat_behavior != "once":
        return "quota"
    return "same_action"


def generate_candidates(
    graph: WorkflowGraph,
    templates: list[RequestTemplate],
    effects: list[OperationEffect],
    *,
    sessions: list[str],
    max_candidates: int = 20,
    max_action_pairs: int = 30,
) -> list[RaceCandidate]:
    worthy = filter_candidates(templates, effects)
    exchange_by_id = {exchange.exchange_id: exchange for exchange in graph.exchanges}
    candidates: list[RaceCandidate] = []

    for template, effect in worthy:
        score, breakdown = score_action(
            template, effect, exchange_by_id.get(template.template_id),
            session_count=len(sessions),
        )
        kind = _classify(effect)
        candidates.append(
            RaceCandidate(
                candidate_id=f"cand-{kind}-{template.template_id}",
                kind=kind,
                action_ids=[template.template_id],
                resource_ids=_resources_of(graph, template.template_id),
                score=score,
                score_breakdown=breakdown,
                rationale=_rationale(breakdown),
            )
        )
        if len(sessions) >= 2:
            cross_score = score + 0.5
            candidates.append(
                RaceCandidate(
                    candidate_id=f"cand-cross-user-{template.template_id}",
                    kind="cross_user",
                    action_ids=[template.template_id],
                    resource_ids=_resources_of(graph, template.template_id),
                    score=cross_score,
                    score_breakdown={**breakdown, "cross_user_signal": 1.0},
                    rationale=["same resource reachable by two identities"],
                )
            )

    candidates.extend(
        _speculative_candidates(
            templates,
            seen_action_ids={
                action_id
                for candidate in candidates
                for action_id in candidate.action_ids
            },
            sessions=sessions,
        )
    )

    candidates.extend(
        _pair_candidates(graph, worthy, sessions, max_action_pairs)
    )

    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:max_candidates]


def _speculative_candidates(
    templates: list[RequestTemplate],
    *,
    seen_action_ids: set[str],
    sessions: list[str],
) -> list[RaceCandidate]:
    candidates: list[RaceCandidate] = []
    for template in templates:
        if template.template_id in seen_action_ids:
            continue
        if template.method not in MUTATING_METHODS:
            continue
        score = 1.0
        breakdown = {"mutating_method_signal": 1.0}
        rationale = ["mutating action has no state probe; testing response-only race"]
        if template.variant_hints:
            score += 1.0
            breakdown["form_variant_signal"] = 1.0
            rationale.append("captured form exposes alternate values for concurrent variants")
        candidates.append(
            RaceCandidate(
                candidate_id=f"cand-speculative-{template.template_id}",
                kind="same_action",
                action_ids=[template.template_id],
                score=score,
                score_breakdown=breakdown,
                rationale=rationale,
            )
        )
        if len(sessions) >= 2:
            candidates.append(
                RaceCandidate(
                    candidate_id=f"cand-speculative-cross-user-{template.template_id}",
                    kind="cross_user",
                    action_ids=[template.template_id],
                    score=score + 0.5,
                    score_breakdown={**breakdown, "cross_user_signal": 1.0},
                    rationale=[*rationale, "multiple configured identities are available"],
                )
            )
    return candidates


def _resources_of(graph: WorkflowGraph, template_id: str) -> list[str]:
    resources: list[str] = []
    for edge in graph.dependencies:
        if edge.edge_type == "consumes" and edge.source_id == template_id:
            resources.append(edge.target_id)
    return resources


def _rationale(breakdown: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    if breakdown.get("single_use_signal"):
        reasons.append("sequential repeat shows one-shot semantics")
    if breakdown.get("numeric_boundary_signal"):
        reasons.append("action moves a numeric field with a learned bound")
    if breakdown.get("shared_resource_score"):
        reasons.append("action operates on a produced resource identifier")
    if breakdown.get("latency_window_score"):
        reasons.append("action has a measurable server-side time window")
    return reasons or ["action changes observable state"]


def _pair_candidates(
    graph: WorkflowGraph,
    worthy: list[tuple[RequestTemplate, OperationEffect]],
    sessions: list[str],
    max_pairs: int,
) -> list[RaceCandidate]:
    """A || B only for actions tied by a shared resource (graph distance 1)."""
    pairs: list[RaceCandidate] = []
    resource_consumers: dict[str, set[str]] = {}
    for edge in graph.dependencies:
        if edge.edge_type == "consumes":
            resource_consumers.setdefault(edge.target_id, set()).add(edge.source_id)

    effect_by_action = {effect.action_id: effect for _, effect in worthy}
    seen: set[frozenset[str]] = set()
    for resource_id, consumers in resource_consumers.items():
        consumer_list = sorted(consumers & set(effect_by_action))
        for left in consumer_list:
            for right in consumer_list:
                key = frozenset({left, right})
                if left == right or key in seen:
                    continue
                seen.add(key)
                left_effect = effect_by_action[left]
                right_effect = effect_by_action[right]
                score = 4.0 + 0.5 * (
                    len(left_effect.state_changes) + len(right_effect.state_changes)
                )
                pairs.append(
                    RaceCandidate(
                        candidate_id=f"cand-cross-action-{left}-{right}",
                        kind="cross_action",
                        action_ids=[left, right],
                        resource_ids=[resource_id],
                        score=score,
                        score_breakdown={"shared_resource_score": 4.0},
                        rationale=[f"both actions consume {resource_id}"],
                    )
                )
                if len(pairs) >= max_pairs:
                    return pairs
    return pairs
