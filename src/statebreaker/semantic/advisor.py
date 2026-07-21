"""Semantic advisor boundary: weak labels for ranking, never verdicts (§16)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field

from statebreaker.models.base import ContractModel


class SemanticLabel(ContractModel):
    """A weak semantic hint about one action (e.g. "state-changing write")."""

    action_id: str
    label: str
    confidence: float = 0.0
    rationale: str = ""


class SemanticContext(ContractModel):
    """What an advisor may look at: action ids, templates, DOM-ish hints."""

    action_ids: list[str] = Field(default_factory=list)
    methods: dict[str, str] = Field(default_factory=dict)
    paths: dict[str, str] = Field(default_factory=dict)
    hints: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class SemanticAdvisor(Protocol):
    """Extension point: classify actions for candidate ranking only."""

    async def classify_actions(self, context: SemanticContext) -> list[SemanticLabel]:
        ...
