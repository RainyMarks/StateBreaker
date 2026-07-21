"""Discovery-layer models: race candidates and attack plans."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from statebreaker.models.base import ContractModel
from statebreaker.models.capture import RequestTemplate

CandidateKind = Literal[
    "same_action",
    "cross_action",
    "cross_user",
    "lifecycle",
    "quota",
]

SchedulerId = Literal["async-http", "http1-last-byte", "http2-stream-gate", "http3-quic"]

ConnectionStrategy = Literal[
    "same_connection",
    "separate_connections",
    "same_session",
    "separate_sessions",
    "warm_connection",
    "cold_connection",
]


class RaceCandidate(ContractModel):
    """A scored hypothesis that certain actions may race."""

    candidate_id: str
    kind: CandidateKind
    action_ids: list[str] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    rationale: list[str] = Field(default_factory=list)


class ActionInstance(ContractModel):
    """One concrete request (or request chain) to fire inside an attack plan."""

    instance_id: str
    action_id: str
    session_id: str = "default"
    exchange_templates: list[RequestTemplate] = Field(default_factory=list)
    bindings: dict[str, str] = Field(default_factory=dict)


class AttackPlan(ContractModel):
    """A concrete, executable race schedule derived from a candidate."""

    plan_id: str
    candidate_id: str
    action_instances: list[ActionInstance] = Field(default_factory=list)
    sessions: list[str] = Field(default_factory=list)
    scheduler: str = "async-http"
    concurrency: int = 2
    offsets_ms: list[float] = Field(default_factory=list)
    connection_strategy: str = "separate_connections"
    reset_strategy: str = "fresh-resource"
    state_probe_ids: list[str] = Field(default_factory=list)
    setup_action_ids: list[str] = Field(default_factory=list)
