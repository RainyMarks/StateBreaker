"""Default advisor: no labels, no external calls — core detection is unaffected."""

from __future__ import annotations

from statebreaker.semantic.advisor import SemanticContext, SemanticLabel


class NoopSemanticAdvisor:
    """The system runs fully automatically with this advisor (spec §16)."""

    async def classify_actions(self, context: SemanticContext) -> list[SemanticLabel]:
        return []
