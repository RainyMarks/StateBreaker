"""Discover and validate state probes: requests exposing business state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from statebreaker.execution.client import HttpSender
from statebreaker.intelligence.dependency_inference import replay_flow, send_template
from statebreaker.intelligence.jsondiff import diff_states
from statebreaker.intelligence.lineage import iter_json_leaves
from statebreaker.intelligence.value_types import classify_value
from statebreaker.models.state import StateProbe
from statebreaker.models.workflow import WorkflowGraph

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def discover_probe_candidates(graph: WorkflowGraph) -> list[StateProbe]:
    """Rank GET exchanges that likely expose resource state.

    Structural signals only: consumes a path variable, returns a JSON object
    with numeric or enum leaves.
    """
    probes: list[StateProbe] = []
    consumed_vars: dict[str, list[str]] = {}
    for binding in graph.variable_bindings:
        consumed_vars.setdefault(binding.consumer_exchange_id, []).append(binding.variable_id)

    from statebreaker.intelligence.templates import build_templates

    templates = {
        t.template_id: t
        for t in build_templates(graph.exchanges, graph.variable_bindings)
    }

    for exchange in graph.exchanges:
        if exchange.method != "GET":
            continue
        if exchange.response_body_encoding != "json" or not isinstance(
            exchange.response_body, dict
        ):
            continue
        score = 0.2
        leaves = list(iter_json_leaves(exchange.response_body))
        if len(leaves) >= 2:
            score += 0.2
        kinds = {classify_value(leaf) for _, leaf in leaves}
        if kinds & {"amount", "small_number"} or any(
            isinstance(leaf, (int, float)) and not isinstance(leaf, bool) for _, leaf in leaves
        ):
            score += 0.2
        if "enum" in kinds:
            score += 0.2
        variables = consumed_vars.get(exchange.exchange_id, [])
        if variables:
            score += 0.2
        template = templates.get(exchange.exchange_id)
        if template is None:
            continue
        probes.append(
            StateProbe(
                probe_id=f"probe-{exchange.exchange_id}",
                request_template=template,
                resource_ids=[
                    f"resource-{variable}" for variable in sorted(set(variables))
                ],
                confidence=min(score, 0.8),
            )
        )
    probes.sort(key=lambda probe: probe.confidence, reverse=True)
    return probes


def clone_probes_for_sessions(
    probes: list[StateProbe], sessions: list[str]
) -> list[StateProbe]:
    """Clone identity-bearing probes so every session's view is observed.

    A probe is identity-bearing when one path segment equals a session id
    (e.g. ``/members/alice`` recorded from alice's flow). A cross-user race
    is invisible unless the *other* identity's state is probed as well, so
    each such probe is cloned once per remaining session.
    """
    identities = sorted({session for session in sessions if session != "default"})
    if len(identities) < 2:
        return list(probes)
    cloned: list[StateProbe] = []
    for probe in probes:
        segments = probe.request_template.path_template.split("/")
        owners = [session for session in identities if session in segments]
        if not owners:
            continue
        owner = owners[0]
        for session in identities:
            if session == owner:
                continue
            new_path = "/".join(
                session if segment == owner else segment for segment in segments
            )
            template = probe.request_template.model_copy(
                update={
                    "template_id": f"{probe.request_template.template_id}@{session}",
                    "path_template": new_path,
                }
            )
            cloned.append(
                probe.model_copy(
                    update={
                        "probe_id": f"{probe.probe_id}@{session}",
                        "request_template": template,
                    }
                )
            )
    return list(probes) + cloned


async def validate_probe(
    probe: StateProbe,
    graph: WorkflowGraph,
    templates: list[Any],
    sender: HttpSender,
    *,
    mutating_template_id: str,
    session_id: str = "default",
    initial_variables: Mapping[str, Any] | None = None,
) -> StateProbe:
    """Probe → action → probe; a stable, explainable diff confirms the probe."""
    index = next(
        (i for i, t in enumerate(templates) if t.template_id == mutating_template_id),
        None,
    )
    if index is None:
        return probe.model_copy(update={"confidence": probe.confidence * 0.5})

    prefix = await replay_flow(
        templates[:index],
        graph.variable_bindings,
        sender,
        session_id=session_id,
        initial_variables=initial_variables,
    )
    variables: dict[str, Any] = dict(prefix.variables)

    before = await send_template(
        probe.request_template, variables, sender, session_id=session_id
    )
    mutation = await replay_flow(
        templates[index : index + 1],
        graph.variable_bindings,
        sender,
        session_id=session_id,
        initial_variables=variables,
    )
    variables.update(mutation.variables)
    after = await send_template(
        probe.request_template, variables, sender, session_id=session_id
    )

    changes = diff_states(before.response_body, after.response_body)
    if changes and before.response_status < 400 and after.response_status < 400:
        return probe.model_copy(
            update={
                "confidence": 0.9,
                "observed_paths": [change.path for change in changes],
            }
        )
    return probe.model_copy(update={"confidence": probe.confidence * 0.5})
