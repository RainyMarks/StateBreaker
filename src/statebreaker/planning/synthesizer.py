"""Attack plan synthesis: candidates -> concrete schedules (spec §10)."""

from __future__ import annotations

import copy
import json
from typing import Any

from statebreaker.models.capture import RequestTemplate
from statebreaker.models.discovery import ActionInstance, AttackPlan, RaceCandidate

_VARIANT_FIELD_HINTS = (
    "to",
    "target",
    "destination",
    "dest",
    "recipient",
    "receiver",
)


def synthesize_plans(
    candidates: list[RaceCandidate],
    templates: list[RequestTemplate],
    *,
    probe_ids: list[str],
    schedulers: list[str],
    concurrencies: list[int],
    offsets_ms: list[float],
    reset_strategy: str,
    sessions: list[str],
) -> list[AttackPlan]:
    """Generate plans in safe priority order: 2-request same-action first,
    cross-user second, cross-action third, bursts last (spec §10.2)."""
    template_by_id = {template.template_id: template for template in templates}
    prefix_by_id = _prefixes(templates)
    plans: list[AttackPlan] = []

    for candidate in candidates:
        primary = template_by_id.get(candidate.action_ids[0]) if candidate.action_ids else None
        if primary is None:
            continue
        scheduler = schedulers[0] if schedulers else "async-http"
        session = sessions[0] if sessions else "default"

        if candidate.kind in {"same_action", "quota"}:
            plans.extend(
                _same_action_plans(
                    candidate, primary, prefix_by_id,
                    scheduler=scheduler,
                    concurrencies=sorted(concurrencies) or [2],
                    offsets_ms=offsets_ms,
                    probe_ids=probe_ids,
                    reset_strategy=reset_strategy,
                    session=session,
                )
            )
        elif candidate.kind == "cross_user" and len(sessions) >= 2:
            plans.append(
                AttackPlan(
                    plan_id=f"plan-cross-user-{candidate.candidate_id}",
                    candidate_id=candidate.candidate_id,
                    action_instances=[
                        _instance("inst-1", primary, sessions[0]),
                        _instance("inst-2", primary, sessions[1]),
                    ],
                    sessions=sessions[:2],
                    scheduler=scheduler,
                    concurrency=2,
                    offsets_ms=offsets_ms or [0.0],
                    reset_strategy=reset_strategy,
                    state_probe_ids=list(probe_ids),
                    setup_action_ids=prefix_by_id.get(primary.template_id, []),
                )
            )
        elif candidate.kind in {"cross_action", "lifecycle"} and len(candidate.action_ids) >= 2:
            second = template_by_id.get(candidate.action_ids[1])
            if second is None:
                continue
            setup = list(
                dict.fromkeys(
                    prefix_by_id.get(primary.template_id, [])
                    + prefix_by_id.get(second.template_id, [])
                )
            )
            plans.append(
                AttackPlan(
                    plan_id=f"plan-cross-{candidate.candidate_id}",
                    candidate_id=candidate.candidate_id,
                    action_instances=[
                        _instance("inst-1", primary, session),
                        _instance("inst-2", second, session),
                    ],
                    sessions=[session],
                    scheduler=scheduler,
                    concurrency=2,
                    offsets_ms=offsets_ms or [0.0],
                    reset_strategy=reset_strategy,
                    state_probe_ids=list(probe_ids),
                    setup_action_ids=setup,
                )
            )
    return plans


def _same_action_plans(
    candidate: RaceCandidate,
    template: RequestTemplate,
    prefix_by_id: dict[str, list[str]],
    *,
    scheduler: str,
    concurrencies: list[int],
    offsets_ms: list[float],
    probe_ids: list[str],
    reset_strategy: str,
    session: str,
) -> list[AttackPlan]:
    plans: list[AttackPlan] = []
    for concurrency in concurrencies:
        if concurrency < 2:
            continue
        templates = _variant_templates(template, concurrency)
        plans.append(
            AttackPlan(
                plan_id=f"plan-x{concurrency}-{candidate.candidate_id}",
                candidate_id=candidate.candidate_id,
                action_instances=[
                    _instance(f"inst-{index + 1}", templates[index], session)
                    for index in range(concurrency)
                ],
                sessions=[session],
                scheduler=scheduler,
                concurrency=concurrency,
                offsets_ms=offsets_ms or [0.0],
                reset_strategy=reset_strategy,
                state_probe_ids=list(probe_ids),
                setup_action_ids=[] if _is_speculative(candidate) else prefix_by_id.get(
                    template.template_id, []
                ),
            )
        )
    return plans


def _instance(instance_id: str, template: RequestTemplate, session: str) -> ActionInstance:
    return ActionInstance(
        instance_id=instance_id,
        action_id=template.template_id,
        session_id=session,
        exchange_templates=[template],
    )


def _prefixes(templates: list[RequestTemplate]) -> dict[str, list[str]]:
    ordered = [template.template_id for template in templates]
    return {
        template.template_id: ordered[:index] for index, template in enumerate(templates)
    }


def _is_speculative(candidate: RaceCandidate) -> bool:
    return candidate.candidate_id.startswith("cand-speculative-")


def _variant_templates(template: RequestTemplate, count: int) -> list[RequestTemplate]:
    field_path = _preferred_variant_field(template)
    if field_path is None:
        return [template for _ in range(count)]
    values = template.variant_hints.get(field_path) or []
    variants = [template]
    current = _value_at_path(template.body, field_path)
    alternate_values = [value for value in values if str(value) != str(current)]
    for index in range(1, count):
        if not alternate_values:
            variants.append(template)
            continue
        value = alternate_values[(index - 1) % len(alternate_values)]
        variants.append(_template_with_variant(template, field_path, value))
    return variants


def _preferred_variant_field(template: RequestTemplate) -> str | None:
    if not template.variant_hints:
        return None
    ranked = sorted(template.variant_hints)
    for hint in _VARIANT_FIELD_HINTS:
        for path in ranked:
            if hint in path.lower().split(".")[-1]:
                return path
    return ranked[0]


def _template_with_variant(
    template: RequestTemplate,
    field_path: str,
    value: str,
) -> RequestTemplate:
    body = copy.deepcopy(template.body)
    body = _set_value_at_path(body, field_path, value)
    return template.model_copy(update={"body": body})


def _value_at_path(body: Any, field_path: str) -> Any:
    parts = field_path.split(".")
    if len(parts) < 2 or parts[0] != "body":
        return None
    current = body
    for index, part in enumerate(parts[1:], start=1):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
        if isinstance(current, str) and index < len(parts) - 1:
            try:
                current = json.loads(current)
            except json.JSONDecodeError:
                return None
    return current


def _set_value_at_path(body: Any, field_path: str, value: str) -> Any:
    parts = field_path.split(".")
    if len(parts) < 2 or parts[0] != "body":
        return body
    if len(parts) == 2 and isinstance(body, dict):
        body[parts[1]] = value
        return body
    if len(parts) >= 3 and isinstance(body, dict):
        container_key = parts[1]
        encoded = body.get(container_key)
        if isinstance(encoded, str):
            try:
                decoded = json.loads(encoded)
            except json.JSONDecodeError:
                return body
            current: Any = decoded
            for part in parts[2:-1]:
                if not isinstance(current, dict):
                    return body
                current = current.get(part)
            if isinstance(current, dict):
                current[parts[-1]] = value
                body[container_key] = json.dumps(decoded, separators=(",", ":"))
    return body
