"""Versioned public contracts used between the core and third-party plugins."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

API_VERSION: Literal["0.1"] = "0.1"
SCHEMA_VERSION: Literal["0.1"] = "0.1"
IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]*$"
TEMPLATE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


def utc_now() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    """Strict, versioned base for every public model."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    schema_version: Literal["0.1"] = SCHEMA_VERSION


class StepRole(StrEnum):
    SETUP = "setup"
    ACTION = "action"
    PROBE = "probe"


class ExtractorKind(StrEnum):
    JSONPATH = "jsonpath"
    HEADER = "header"
    REGEX = "regex"


class FindingVerdict(StrEnum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    REJECTED = "rejected"


class SessionDefinition(ContractModel):
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    follow_redirects: bool = True


class RequestSpec(ContractModel):
    method: str = Field(pattern=r"^[A-Z]+$")
    path: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    json_body: Any | None = None
    form_body: dict[str, Any] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)

    @model_validator(mode="after")
    def body_is_unambiguous(self) -> RequestSpec:
        if self.json_body is not None and self.form_body is not None:
            raise ValueError("json_body and form_body are mutually exclusive")
        return self


class Extractor(ContractModel):
    name: str = Field(pattern=IDENTIFIER_PATTERN)
    kind: ExtractorKind
    expression: str = Field(min_length=1)
    required: bool = True


class RequestStep(ContractModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    role: StepRole = StepRole.ACTION
    session: str = Field(default="default", pattern=IDENTIFIER_PATTERN)
    request: RequestSpec
    extract: list[Extractor] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for key, item in value.items():
            found.extend(_collect_strings(key))
            found.extend(_collect_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_collect_strings(item))
        return found
    return []


class Workflow(ContractModel):
    name: str = Field(min_length=1)
    description: str = ""
    base_url: AnyHttpUrl
    sessions: dict[str, SessionDefinition] = Field(
        default_factory=lambda: {"default": SessionDefinition()}
    )
    variables: dict[str, Any] = Field(default_factory=dict)
    steps: list[RequestStep] = Field(min_length=1)
    state_probe_steps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_workflow_graph(self) -> Workflow:
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("workflow step ids must be unique")

        known_steps: set[str] = set()
        declared_variables = set(self.variables)
        produced_by: dict[str, str] = {}
        for step in self.steps:
            if step.session not in self.sessions:
                raise ValueError(f"step {step.id!r} references unknown session {step.session!r}")
            for dependency in step.depends_on:
                if dependency not in known_steps:
                    raise ValueError(
                        f"step {step.id!r} depends on {dependency!r}, which must appear earlier"
                    )
            extractor_names = [extractor.name for extractor in step.extract]
            if len(extractor_names) != len(set(extractor_names)):
                raise ValueError(f"step {step.id!r} has duplicate extractor names")
            for name in extractor_names:
                if name in declared_variables or name in produced_by:
                    raise ValueError(f"variable {name!r} is defined more than once")
                produced_by[name] = step.id
            known_steps.add(step.id)

        unknown_probes = set(self.state_probe_steps) - set(step_ids)
        if unknown_probes:
            raise ValueError(f"unknown state probe steps: {sorted(unknown_probes)}")
        non_probe = [
            step_id
            for step_id in self.state_probe_steps
            if next(step for step in self.steps if step.id == step_id).role != StepRole.PROBE
        ]
        if non_probe:
            raise ValueError(f"state probe steps must use role='probe': {non_probe}")

        available = declared_variables.copy()
        for step in self.steps:
            request_strings = _collect_strings(step.request.model_dump(mode="python"))
            referenced = {
                match.group(1)
                for text in request_strings
                for match in TEMPLATE_PATTERN.finditer(text)
            }
            unknown = referenced - available
            if unknown:
                raise ValueError(
                    f"step {step.id!r} references variables not yet available: {sorted(unknown)}"
                )
            available.update(extractor.name for extractor in step.extract)
        return self


class StateProfile(ContractModel):
    workflow_name: str
    samples: list[dict[str, Any]] = Field(default_factory=list)
    stable_fields: list[str] = Field(default_factory=list)
    ignored_fields: list[str] = Field(default_factory=list)


class Invariant(ContractModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    kind: str = Field(pattern=IDENTIFIER_PATTERN)
    selector: str = Field(min_length=1)
    before_probe: str | None = None
    after_probe: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class LearningResult(ContractModel):
    profile: StateProfile
    invariants: list[Invariant] = Field(default_factory=list)


class AttackSchedule(ContractModel):
    concurrency: int = Field(default=1, ge=1)
    offsets_ms: list[float] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class AttackPlan(ContractModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    workflow_name: str = Field(min_length=1)
    attack_type: str = Field(pattern=IDENTIFIER_PATTERN)
    target_steps: list[str] = Field(min_length=1)
    session_bindings: dict[str, str] = Field(default_factory=dict)
    schedule: AttackSchedule = Field(default_factory=AttackSchedule)
    invariant_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunEvent(ContractModel):
    event_id: str
    run_id: str
    kind: str
    timestamp: datetime = Field(default_factory=utc_now)
    monotonic_ns: int
    correlation_id: str
    step_id: str | None = None
    request_ordinal: int | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    message: str | None = None


class ResponseRecord(ContractModel):
    correlation_id: str
    step_id: str
    request_ordinal: int
    status_code: int
    elapsed_ms: float = Field(ge=0)
    headers: dict[str, str] = Field(default_factory=dict)
    body_preview: str = ""


class RawAttackResult(ContractModel):
    run_id: str
    attack_plan_id: str
    started_at: datetime
    finished_at: datetime
    responses: list[ResponseRecord] = Field(default_factory=list)
    before_state: dict[str, Any] = Field(default_factory=dict)
    after_state: dict[str, Any] = Field(default_factory=dict)
    events: list[RunEvent] = Field(default_factory=list)
    plugin_data: dict[str, Any] = Field(default_factory=dict)


class Finding(ContractModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    verdict: FindingVerdict
    title: str = Field(min_length=1)
    invariant_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ReportArtifacts(ContractModel):
    files: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunBundle(ContractModel):
    workflow: Workflow
    attack_plan: AttackPlan
    result: RawAttackResult
    findings: list[Finding] = Field(default_factory=list)


class PluginManifest(ContractModel):
    plugin_id: str = Field(pattern=IDENTIFIER_PATTERN)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    api_version: Literal["0.1"] = API_VERSION
    group: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""
