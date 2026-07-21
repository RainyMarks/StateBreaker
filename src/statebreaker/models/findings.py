"""Finding-layer models: evidence, verdicts, scan outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from statebreaker.models.base import ContractModel, utc_now
from statebreaker.models.discovery import AttackPlan, RaceCandidate

Verdict = Literal["confirmed", "probable", "rejected", "inconclusive"]

ScanStatus = Literal["running", "completed", "failed", "budget_exhausted"]


class EvidenceBundle(ContractModel):
    """Portable proof backing a finding: trials, states, comparisons."""

    bundle_id: str
    finding_id: str | None = None
    trial_ids: list[str] = Field(default_factory=list)
    control_trial_ids: list[str] = Field(default_factory=list)
    attack_trial_ids: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class RunStatistics(ContractModel):
    """Repeatability of one minimized plan over several attack rounds."""

    rounds: int = 0
    successes: int = 0
    success_rate: float = 0.0
    median_release_skew_ms: float = 0.0
    mean_trigger_time_ms: float = 0.0


class Finding(ContractModel):
    """A verdict on one race candidate, always traceable to real trials."""

    finding_id: str
    verdict: Verdict
    confidence: float = 0.0
    candidate: RaceCandidate
    minimized_plan_id: str | None = None
    minimized_plan: AttackPlan | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)
    violated_invariant_ids: list[str] = Field(default_factory=list)
    success_rate: float | None = None
    minimum_concurrency: int | None = None
    best_scheduler: str | None = None
    statistics: RunStatistics | None = None


class ScanOutcome(ContractModel):
    """Top-level result of one AutoRaceScanner run."""

    scan_id: str
    project: str
    capture_id: str
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    graph_id: str | None = None
    baseline_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    plan_ids: list[str] = Field(default_factory=list)
    trial_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    status: ScanStatus = "running"
    stats: dict[str, Any] = Field(default_factory=dict)
