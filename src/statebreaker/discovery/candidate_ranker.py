"""Candidate scoring: behavioral signals only, never URL vocabulary (§9.2)."""

from __future__ import annotations

from statebreaker.models.capture import HttpExchange, RequestTemplate
from statebreaker.models.state import OperationEffect


def score_action(
    template: RequestTemplate,
    effect: OperationEffect,
    exchange: HttpExchange | None,
    *,
    session_count: int,
) -> tuple[float, dict[str, float]]:
    """Compute the race-risk score for one action, with a full breakdown."""
    breakdown: dict[str, float] = {}

    changed_paths = len(effect.state_changes)
    numeric = any(change.delta is not None for change in effect.state_changes)
    breakdown["state_change_score"] = min(3.0, float(changed_paths)) + (1.0 if numeric else 0.0)

    breakdown["sequential_asymmetry_score"] = {
        "once": 3.0,
        "limited": 2.0,
        "unstable": 1.0,
        "idempotent": 0.0,
        "unknown": 0.0,
    }.get(effect.repeat_behavior, 0.0)

    consumes_resource = "${" in template.path_template
    breakdown["shared_resource_score"] = 1.5 if consumes_resource else 0.0

    breakdown["single_use_signal"] = 2.0 if effect.repeat_behavior == "once" else 0.0
    breakdown["numeric_boundary_signal"] = 1.5 if numeric else 0.0
    breakdown["cross_user_signal"] = 0.5 if session_count >= 2 else 0.0

    semantic_known = (
        effect.response_signature is not None and effect.repeat_behavior != "unknown"
    )
    breakdown["response_semantic_score"] = 1.0 if semantic_known else 0.0

    window_ms = 0.0
    if exchange is not None and exchange.completed_at_ns > exchange.started_at_ns:
        window_ms = (exchange.completed_at_ns - exchange.started_at_ns) / 1_000_000
    breakdown["latency_window_score"] = 1.0 if window_ms > 30 else (0.5 if window_ms > 10 else 0.0)

    breakdown["instability_penalty"] = -2.0 if effect.repeat_behavior == "unstable" else 0.0
    breakdown["destructive_risk_penalty"] = -2.0 if template.method == "DELETE" else 0.0

    return sum(breakdown.values()), breakdown
