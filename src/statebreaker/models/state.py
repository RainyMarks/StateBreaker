"""State-layer models: probes, snapshots, learned effects and invariants."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from statebreaker.models.base import ContractModel
from statebreaker.models.capture import RequestTemplate

RepeatBehavior = Literal["unknown", "once", "limited", "idempotent", "unstable"]

InvariantType = Literal[
    "numeric_bound",
    "one_shot",
    "state_transition",
    "lower_bound",
    "uniqueness",
    "ownership",
]


class StateProbe(ContractModel):
    """A request template believed to expose business state for a resource."""

    probe_id: str
    request_template: RequestTemplate
    resource_ids: list[str] = Field(default_factory=list)
    observed_paths: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class NormalizedState(ContractModel):
    """Business state after unstable fields (timestamps, trace ids, ...) are dropped."""

    fields: dict[str, Any] = Field(default_factory=dict)
    ignored_paths: list[str] = Field(default_factory=list)


class StateSnapshot(ContractModel):
    """One observation of a probe at a point in time."""

    snapshot_id: str
    probe_id: str
    taken_at_ns: int = 0
    raw: Any | None = None
    normalized: NormalizedState | None = None


class FieldChange(ContractModel):
    """A single observed mutation of a state field."""

    path: str
    before: Any | None = None
    after: Any | None = None
    delta: float | None = None


class ResponseSignature(ContractModel):
    """Semantic fingerprint of a response, learned from repetition."""

    status: int = 0
    body_shape: str | None = None
    semantic_class: str | None = None


class OperationEffect(ContractModel):
    """What one action does to observable state under normal execution."""

    action_id: str
    state_changes: list[FieldChange] = Field(default_factory=list)
    response_signature: ResponseSignature | None = None
    success_probability: float = 0.0
    repeat_behavior: RepeatBehavior = "unknown"
    supporting_trial_ids: list[str] = Field(default_factory=list)


class LearnedInvariant(ContractModel):
    """A candidate business rule learned from normal-execution experiments.

    Always backed by the trials that support it; never hand-authored in the
    default flow.
    """

    invariant_id: str
    invariant_type: InvariantType
    target_paths: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    supporting_trials: list[str] = Field(default_factory=list)


class BaselineProfile(ContractModel):
    """The learned normal behavior of a captured workflow."""

    profile_id: str
    capture_id: str
    graph_id: str | None = None
    effects: list[OperationEffect] = Field(default_factory=list)
    invariants: list[LearnedInvariant] = Field(default_factory=list)
    probe_ids: list[str] = Field(default_factory=list)
    trial_ids: list[str] = Field(default_factory=list)
