"""Assemble the WorkflowGraph from a trace and its inferred bindings."""

from __future__ import annotations

from statebreaker.models.capture import CapturedTrace
from statebreaker.models.state import StateProbe
from statebreaker.models.workflow import (
    ActionNode,
    DependencyEdge,
    DependencyKind,
    ResourceNode,
    VariableBinding,
    WorkflowGraph,
)


def build_actions(trace: CapturedTrace) -> list[ActionNode]:
    """Group exchanges into actions (one per recorded user action, or one
    per exchange when the capture has no UI correlation)."""
    grouped: dict[str, ActionNode] = {}
    for exchange in trace.exchanges:
        action_id = exchange.action_id or f"action-{exchange.exchange_id}"
        action_node = grouped.get(action_id)
        if action_node is None:
            action_node = ActionNode(
                action_id=action_id,
                session_id=exchange.session_id,
                exchange_ids=[],
            )
            grouped[action_id] = action_node
        action_node.exchange_ids.append(exchange.exchange_id)
    return list(grouped.values())


def build_graph(
    trace: CapturedTrace,
    bindings: list[VariableBinding],
    *,
    graph_id: str,
    state_probes: list[StateProbe] | None = None,
) -> WorkflowGraph:
    """Construct the full workflow graph: actions, resources, edges."""
    actions = build_actions(trace)

    # One resource per distinct produced variable.
    resources: dict[str, ResourceNode] = {}
    for binding in bindings:
        resource = resources.get(binding.variable_id)
        if resource is None:
            resource = ResourceNode(
                resource_id=f"resource-{binding.variable_id}",
                variable_id=binding.variable_id,
                producer_exchange_id=binding.producer_exchange_id,
            )
            resources[binding.variable_id] = resource
        if binding.consumer_exchange_id not in resource.consumer_exchange_ids:
            resource.consumer_exchange_ids.append(binding.consumer_exchange_id)

    edges: list[DependencyEdge] = []
    seen: set[tuple[str, str, str]] = set()

    def add(edge_type: DependencyKind, source: str, target: str, confidence: float) -> None:
        key = (edge_type, source, target)
        if key in seen or source == target:
            return
        seen.add(key)
        edges.append(
            DependencyEdge(
                edge_type=edge_type,
                source_id=source,
                target_id=target,
                confidence=confidence,
            )
        )

    exchange_to_action: dict[str, str] = {}
    for node in actions:
        for exchange_id in node.exchange_ids:
            exchange_to_action[exchange_id] = node.action_id

    for binding in bindings:
        resource_id = resources[binding.variable_id].resource_id
        add("produces", binding.producer_exchange_id, resource_id, binding.confidence)
        add("consumes", binding.consumer_exchange_id, resource_id, binding.confidence)
        add(
            "must_precede",
            binding.producer_exchange_id,
            binding.consumer_exchange_id,
            binding.confidence,
        )
        producer_action = exchange_to_action.get(binding.producer_exchange_id)
        consumer_action = exchange_to_action.get(binding.consumer_exchange_id)
        if producer_action and consumer_action:
            add("must_precede", producer_action, consumer_action, binding.confidence)

    resource_list = list(resources.values())
    for resource in resource_list:
        consumers = resource.consumer_exchange_ids
        for left in consumers:
            for right in consumers:
                add("same_resource", left, right, 0.8)

    by_session: dict[str, list[str]] = {}
    for exchange in trace.exchanges:
        by_session.setdefault(exchange.session_id, []).append(exchange.exchange_id)
    for exchange_ids in by_session.values():
        for left in exchange_ids:
            for right in exchange_ids:
                add("same_session", left, right, 1.0)

    return WorkflowGraph(
        graph_id=graph_id,
        capture_id=trace.capture_id,
        actions=actions,
        exchanges=list(trace.exchanges),
        resources=resource_list,
        variable_bindings=bindings,
        dependencies=edges,
        state_probes=list(state_probes or []),
    )
