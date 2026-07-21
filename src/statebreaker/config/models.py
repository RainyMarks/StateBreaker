"""Project configuration models (loaded from project.yaml)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from statebreaker.models.base import ContractModel
from statebreaker.models.execution import ScanBudget


class ProjectInfo(ContractModel):
    name: str
    base_url: str

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name_to_str(cls, value: Any) -> Any:
        # YAML loads bare ``123`` as int; keep project names stringly typed.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return value


class ScopeConfig(ContractModel):
    allowed_hosts: list[str] = Field(default_factory=list)
    excluded_paths: list[str] = Field(default_factory=list)
    requests_per_second: float = 10.0


class SessionConfig(ContractModel):
    capture_context: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)


class CaptureConfig(ContractModel):
    source: Literal["browser", "har", "openapi", "postman", "manual"] = "har"
    trace: str | None = None


class ResetConfig(ContractModel):
    strategy: Literal["workflow", "api", "fresh-resource", "none"] = "fresh-resource"
    workflow: str | None = None
    endpoint: str | None = None


class DiscoveryConfig(ContractModel):
    max_candidates: int = 20
    max_action_pairs: int = 30
    use_semantic_advisor: bool = False


class ExecutionConfig(ContractModel):
    schedulers: list[str] = Field(default_factory=lambda: ["async-http"])
    concurrency: list[int] = Field(default_factory=lambda: [2, 4, 8])
    offsets_ms: list[float] = Field(default_factory=lambda: [0.0])
    repetitions: int = 10


class OracleConfig(ContractModel):
    require_state_evidence_for_confirmed: bool = True


class ProjectConfig(ContractModel):
    """Root of ``project.yaml``; contains no target-business-specific fields."""

    project: ProjectInfo
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    sessions: dict[str, SessionConfig] = Field(default_factory=dict)
    capture: CaptureConfig | None = None
    reset: ResetConfig | None = None
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    oracle: OracleConfig = Field(default_factory=OracleConfig)
    budget: ScanBudget = Field(default_factory=ScanBudget)
