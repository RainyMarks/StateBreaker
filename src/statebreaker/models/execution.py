"""Execution-layer models: budgets, timelines, responses, trials."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from statebreaker.models.base import ContractModel
from statebreaker.models.state import StateSnapshot

TrialRole = Literal["baseline", "control", "attack"]

TimelineEventKind = Literal[
    "connection_opened",
    "headers_started",
    "body_started",
    "gate_ready",
    "released",
    "first_byte_received",
    "completed",
]


class ScanBudget(ContractModel):
    """Hard limits every scan stage must respect."""

    maximum_requests: int = 1000
    maximum_trial_rounds: int = 100
    maximum_concurrency: int = 16
    maximum_minutes: float = 30.0
    requests_per_second: float = 10.0
    max_candidates: int = 20
    max_action_pairs: int = 30


class TimelineEvent(ContractModel):
    """A high-resolution timestamp for one stage of one request."""

    instance_id: str
    event: TimelineEventKind
    at_ns: int


class HttpResponseRecord(ContractModel):
    """The outcome of one fired request inside a trial."""

    instance_id: str
    status: int = 0
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    started_at_ns: int = 0
    completed_at_ns: int = 0
    error: str | None = None


class ExecutionTrial(ContractModel):
    """One isolated experiment: reset, before-state, fire, after-state."""

    trial_id: str
    candidate_id: str = ""
    plan_id: str = ""
    control_or_attack: TrialRole = "attack"
    requests: list[PreparedRequest] = Field(default_factory=list)
    before_state: list[StateSnapshot] = Field(default_factory=list)
    responses: list[HttpResponseRecord] = Field(default_factory=list)
    after_state: list[StateSnapshot] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    reset_context: dict[str, Any] = Field(default_factory=dict)
    started_at_ns: int = 0
    completed_at_ns: int = 0


class TrialContext(ContractModel):
    """Isolation handle produced by a reset strategy for one trial."""

    context_id: str
    variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreparedRequest(ContractModel):
    """A fully rendered request ready for a scheduler backend."""

    instance_id: str
    session_id: str = "default"
    method: str = "GET"
    url: str = "http://localhost/"
    headers: dict[str, str] = Field(default_factory=dict)
    body: bytes | None = None


class PreparedRace(ContractModel):
    """Backend-specific handle for requests staged and awaiting release."""

    race_id: str
    scheduler: str
    requests: list[PreparedRequest] = Field(default_factory=list)
    offsets_ms: list[float] = Field(default_factory=list)
    connection_strategy: str = "separate_connections"
    backend_state: dict[str, Any] = Field(default_factory=dict)


ExecutionTrial.model_rebuild()
