"""Attack plan synthesis: candidates -> concrete schedules (spec §10)."""

from __future__ import annotations

from statebreaker.models.capture import RequestTemplate
from statebreaker.models.discovery import ActionInstance, AttackPlan, RaceCandidate


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
        plans.append(
            AttackPlan(
                plan_id=f"plan-x{concurrency}-{candidate.candidate_id}",
                candidate_id=candidate.candidate_id,
                action_instances=[
                    _instance(f"inst-{index + 1}", template, session)
                    for index in range(concurrency)
                ],
                sessions=[session],
                scheduler=scheduler,
                concurrency=concurrency,
                offsets_ms=offsets_ms or [0.0],
                reset_strategy=reset_strategy,
                state_probe_ids=list(probe_ids),
                setup_action_ids=prefix_by_id.get(template.template_id, []),
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
