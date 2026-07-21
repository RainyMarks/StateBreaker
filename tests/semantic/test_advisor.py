"""Semantic advisor tests: the Noop default and the protocol boundary (§16)."""

from __future__ import annotations

from statebreaker.semantic import (
    NoopSemanticAdvisor,
    SemanticAdvisor,
    SemanticContext,
    SemanticLabel,
)


async def test_noop_advisor_returns_no_labels() -> None:
    advisor = NoopSemanticAdvisor()
    context = SemanticContext(action_ids=["a", "b"], methods={"a": "POST"})
    assert await advisor.classify_actions(context) == []


def test_noop_advisor_satisfies_the_protocol() -> None:
    assert isinstance(NoopSemanticAdvisor(), SemanticAdvisor)


def test_random_object_is_not_an_advisor() -> None:
    assert not isinstance(object(), SemanticAdvisor)


def test_semantic_label_round_trip() -> None:
    label = SemanticLabel(action_id="a", label="state-changing", confidence=0.7)
    assert SemanticLabel.from_json(label.to_json()) == label
