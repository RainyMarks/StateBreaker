"""Candidate filtering: which actions are worth racing at all (spec §9.1)."""

from __future__ import annotations

from statebreaker.baseline.learner import MUTATING_METHODS
from statebreaker.models.capture import RequestTemplate
from statebreaker.models.state import OperationEffect

_LOW_PRIORITY_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_race_worthy(template: RequestTemplate, effect: OperationEffect | None) -> bool:
    """Keep state-changing actions that showed observable behavior."""
    if template.method in _LOW_PRIORITY_METHODS:
        return False
    if template.method not in MUTATING_METHODS:
        return False
    if effect is None:
        return False
    if not effect.state_changes:
        return False
    return effect.success_probability > 0


def filter_candidates(
    templates: list[RequestTemplate],
    effects: list[OperationEffect],
) -> list[tuple[RequestTemplate, OperationEffect]]:
    """Pair each worthy template with its learned effect."""
    effect_by_action = {effect.action_id: effect for effect in effects}
    worthy: list[tuple[RequestTemplate, OperationEffect]] = []
    for template in templates:
        effect = effect_by_action.get(template.template_id)
        if effect is None:
            continue
        if is_race_worthy(template, effect):
            worthy.append((template, effect))
    return worthy
