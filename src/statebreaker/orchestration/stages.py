"""Reusable orchestration stages (shared by `discover` and `scan`)."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.config.loader import ScopeGuard
from statebreaker.config.models import ProjectConfig, SessionConfig
from statebreaker.execution.client import BudgetTracker, HttpSender
from statebreaker.execution.reset import ApiResetStrategy
from statebreaker.execution.sessions import SessionManager
from statebreaker.intelligence.dependency_inference import evaluate_bindings, replay_flow
from statebreaker.intelligence.lineage import infer_bindings
from statebreaker.intelligence.normalizer import normalize_trace
from statebreaker.intelligence.probe_discovery import (
    clone_probes_for_sessions,
    discover_probe_candidates,
)
from statebreaker.intelligence.templates import build_templates, harvest_session_headers
from statebreaker.intelligence.workflow_builder import build_graph
from statebreaker.models.capture import CapturedTrace, RequestTemplate
from statebreaker.models.execution import ScanBudget
from statebreaker.models.state import StateProbe
from statebreaker.models.workflow import WorkflowGraph


def session_configs(
    project: ProjectConfig, trace: CapturedTrace
) -> dict[str, SessionConfig]:
    """Configured sessions enriched with identity headers from the capture."""
    configs = dict(project.sessions)
    harvested = harvest_session_headers(trace)
    for session_id, headers in harvested.items():
        config = configs.get(session_id, SessionConfig())
        merged = {**headers, **config.headers}
        configs[session_id] = config.model_copy(update={"headers": merged})
    default_headers = harvested.get("default")
    if default_headers and project.sessions:
        primary = next(iter(project.sessions))
        primary_config = configs.get(primary, SessionConfig())
        if not primary_config.headers:
            configs[primary] = primary_config.model_copy(update={"headers": default_headers})
    if not configs:
        for session_id in trace.sessions or ["default"]:
            configs.setdefault(session_id, SessionConfig())
    return configs


@dataclass
class DiscoveryResult:
    """What `statebreaker discover` reports (analysis only, no attacks)."""

    graph: WorkflowGraph
    templates: list[RequestTemplate]
    probes: list[StateProbe]
    replay_success: bool
    high_risk_actions: list[str] = field(default_factory=list)
    candidate_pairs: int = 0

    @property
    def confirmed_bindings(self) -> int:
        return sum(
            1 for binding in self.graph.variable_bindings if binding.status == "confirmed"
        )


@dataclass
class GraphDiscoveryResult:
    """Shared graph-discovery stage used by both `discover` and `scan`."""

    trace: CapturedTrace
    graph: WorkflowGraph
    templates: list[RequestTemplate]
    probes: list[StateProbe]
    replay_success: bool

    @property
    def confirmed_bindings(self) -> int:
        return sum(
            1 for binding in self.graph.variable_bindings if binding.status == "confirmed"
        )


async def build_graph_discovery(
    project: ProjectConfig,
    trace: CapturedTrace,
    sender: HttpSender,
    *,
    session_id: str,
    session_ids: list[str],
    clone_session_probes: bool = False,
) -> GraphDiscoveryResult:
    """Normalize, infer, actively validate, build graph, and discover probes."""
    trace = normalize_trace(trace, base_url=project.project.base_url)
    bindings = infer_bindings(trace.exchanges)
    templates = build_templates(trace.exchanges, bindings)

    replay = await replay_flow(templates, bindings, sender, session_id=session_id)
    confirmed = evaluate_bindings(bindings, replay)

    graph = build_graph(trace, confirmed, graph_id=f"graph-{trace.capture_id}")
    probes = discover_probe_candidates(graph)
    if clone_session_probes:
        probes = clone_probes_for_sessions(probes, session_ids)
    graph = graph.model_copy(update={"state_probes": probes})

    return GraphDiscoveryResult(
        trace=trace,
        graph=graph,
        templates=templates,
        probes=probes,
        replay_success=replay.success,
    )


async def run_discovery(
    project: ProjectConfig,
    trace: CapturedTrace,
    *,
    store: ArtifactStore | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DiscoveryResult:
    """Normalize -> infer -> actively validate -> probes. Nothing fires twice."""
    configs = session_configs(project, trace)
    sessions = SessionManager(
        project.project.base_url,
        configs,
        transport=transport,
    )
    sender = HttpSender(
        sessions,
        ScopeGuard(project),
        budget=BudgetTracker(ScanBudget(maximum_requests=200)),
        requests_per_second=project.scope.requests_per_second or 1000,
    )
    try:
        session_id = next(iter(project.sessions), "default")
        if project.reset is not None and project.reset.strategy == "api" and project.reset.endpoint:
            reset = ApiResetStrategy(sender, project.reset.endpoint, session_id=session_id)
            await reset.prepare_trial(f"discover-{trace.capture_id}")
        result = await build_graph_discovery(
            project,
            trace,
            sender,
            session_id=session_id,
            session_ids=list(configs) or ["default"],
        )
    finally:
        await sessions.aclose()

    if store is not None:
        store.save("graphs", result.graph.graph_id, result.graph)

    probe_source_ids = {
        probe.request_template.source_exchange_id or probe.request_template.template_id
        for probe in result.probes
    }
    probe_indexes = [
        index
        for index, template in enumerate(result.templates)
        if template.template_id in probe_source_ids
    ]
    high_risk = [
        template.template_id
        for index, template in enumerate(result.templates)
        if template.method in {"POST", "PUT", "PATCH", "DELETE"}
        and "${" in template.path_template
    ]
    fixed_path_high_risk = [
        template.template_id
        for index, template in enumerate(result.templates)
        if template.method in {"POST", "PUT", "PATCH", "DELETE"}
        and "${" not in template.path_template
        and any(probe_index > index for probe_index in probe_indexes)
    ]
    high_risk.extend(fixed_path_high_risk)
    shared_resources = sum(
        1
        for resource in result.graph.resources
        if len(set(resource.consumer_exchange_ids)) >= 2
    )
    return DiscoveryResult(
        graph=result.graph,
        templates=result.templates,
        probes=result.probes,
        replay_success=result.replay_success,
        high_risk_actions=high_risk,
        candidate_pairs=shared_resources,
    )
