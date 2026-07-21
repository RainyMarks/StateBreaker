"""Workflow-layer models: value lineage, resources, dependencies, the graph."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from statebreaker.models.base import ContractModel
from statebreaker.models.capture import HttpExchange
from statebreaker.models.state import StateProbe

DependencyKind = Literal[
    "produces",
    "consumes",
    "must_precede",
    "same_resource",
    "same_session",
    "observes_state",
    "invalidates",
]

BindingStatus = Literal["candidate", "confirmed", "rejected"]


class VariableBinding(ContractModel):
    """A value produced by one exchange and consumed by another."""

    variable_id: str
    producer_exchange_id: str
    producer_selector: str
    consumer_exchange_id: str
    consumer_location: str
    value_type: str = "unknown"
    confidence: float = 0.0
    status: BindingStatus = "candidate"


class ResourceNode(ContractModel):
    """A server-side entity whose identifier flows through the workflow."""

    resource_id: str
    variable_id: str | None = None
    producer_exchange_id: str | None = None
    consumer_exchange_ids: list[str] = Field(default_factory=list)
    kind: str = "unknown"


class DependencyEdge(ContractModel):
    """A typed relation between two nodes (exchanges, actions, or resources)."""

    edge_type: DependencyKind
    source_id: str
    target_id: str
    confidence: float = 1.0
    evidence: list[str] = Field(default_factory=list)


class ActionNode(ContractModel):
    """A replayable unit of the workflow (one user action, one or more exchanges)."""

    action_id: str
    session_id: str = "default"
    exchange_ids: list[str] = Field(default_factory=list)
    label: str | None = None


class WorkflowGraph(ContractModel):
    """The full inferred structure of a captured normal flow."""

    graph_id: str
    capture_id: str
    actions: list[ActionNode] = Field(default_factory=list)
    exchanges: list[HttpExchange] = Field(default_factory=list)
    resources: list[ResourceNode] = Field(default_factory=list)
    variable_bindings: list[VariableBinding] = Field(default_factory=list)
    dependencies: list[DependencyEdge] = Field(default_factory=list)
    state_probes: list[StateProbe] = Field(default_factory=list)
