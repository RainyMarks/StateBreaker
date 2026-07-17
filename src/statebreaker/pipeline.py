"""Deterministic orchestration for the non-interactive StateBreaker CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from statebreaker.documents import write_json
from statebreaker.errors import DocumentError, PluginError, StateBreakerError
from statebreaker.models import (
    AttackPlan,
    Finding,
    Invariant,
    RawAttackResult,
    ReportArtifacts,
    RunBundle,
    Workflow,
)
from statebreaker.plugins import PluginRegistry
from statebreaker.runtime import ExecutionRuntime


@dataclass(frozen=True)
class PipelinePlugins:
    """Plugin IDs used for one end-to-end pipeline run."""

    generator: str
    executor: str
    verifier: str
    reporter: str | None = None


@dataclass(frozen=True)
class PipelineOutcome:
    """Validated outputs and artifact location for one pipeline run."""

    run_dir: Path
    plans: list[AttackPlan]
    selected_plan: AttackPlan
    result: RawAttackResult
    findings: list[Finding]
    report_artifacts: ReportArtifacts | None


async def _call_plugin(plugin_id: str, awaitable: Any) -> Any:
    """Keep third-party implementation errors readable at the CLI boundary."""

    try:
        return await awaitable
    except StateBreakerError:
        raise
    except Exception as exc:
        raise PluginError(
            f"plugin {plugin_id!r} failed with {type(exc).__name__}: {exc}"
        ) from exc


def validate_plugin_output(value: Any, annotation: Any, plugin_id: str) -> Any:
    """Validate untrusted plugin output against the public core contract."""

    try:
        return TypeAdapter(annotation).validate_python(value)
    except ValidationError as exc:
        raise PluginError(f"plugin {plugin_id!r} returned invalid data: {exc}") from exc


def select_attack_plan(
    plans: list[AttackPlan],
    *,
    plan_id: str | None = None,
    attack_type: str | None = None,
) -> AttackPlan:
    """Select exactly one plan in a stable order for reproducible CLI runs."""

    if plan_id:
        matches = [plan for plan in plans if plan.id == plan_id]
        if not matches:
            raise DocumentError(f"generated plans do not contain plan id {plan_id!r}")
        return matches[0]

    matches = [plan for plan in plans if attack_type is None or plan.attack_type == attack_type]
    if not matches:
        available = sorted({plan.attack_type for plan in plans})
        raise DocumentError(
            f"generated plans do not contain attack type {attack_type!r}; "
            f"available types: {available}"
        )
    return sorted(matches, key=lambda plan: plan.id)[0]


def validate_plan_for_workflow(plan: AttackPlan, workflow: Workflow) -> None:
    if plan.workflow_name != workflow.name:
        raise DocumentError(
            f"attack plan targets {plan.workflow_name!r}, workflow is {workflow.name!r}"
        )
    unknown_steps = set(plan.target_steps) - {step.id for step in workflow.steps}
    if unknown_steps:
        raise DocumentError(f"attack plan references unknown steps: {sorted(unknown_steps)}")


async def run_pipeline(
    workflow: Workflow,
    invariants: list[Invariant],
    *,
    plugins: PipelinePlugins,
    registry: PluginRegistry | None = None,
    output_root: Path = Path(".statebreaker/runs"),
    plan_id: str | None = None,
    attack_type: str | None = None,
) -> PipelineOutcome:
    """Generate, execute, verify, and optionally report one attack plan."""

    plugin_registry = registry or PluginRegistry()
    generator = plugin_registry.get("statebreaker.generator", plugins.generator)
    executor = plugin_registry.get("statebreaker.executor", plugins.executor)
    verifier = plugin_registry.get("statebreaker.verifier", plugins.verifier)
    reporter = (
        plugin_registry.get("statebreaker.reporter", plugins.reporter)
        if plugins.reporter
        else None
    )

    generated = await _call_plugin(
        plugins.generator, generator.generate(workflow, invariants)
    )
    plans = validate_plugin_output(generated, list[AttackPlan], plugins.generator)
    selected = select_attack_plan(plans, plan_id=plan_id, attack_type=attack_type)
    validate_plan_for_workflow(selected, workflow)

    async with ExecutionRuntime(workflow, output_root=output_root) as runtime:
        raw_result = await _call_plugin(
            plugins.executor, executor.execute(selected, runtime)
        )
        result = validate_plugin_output(raw_result, RawAttackResult, plugins.executor)
        run_dir = runtime.run_dir

    raw_findings = await _call_plugin(
        plugins.verifier, verifier.verify(result, invariants)
    )
    findings = validate_plugin_output(raw_findings, list[Finding], plugins.verifier)
    bundle = RunBundle(
        workflow=workflow,
        attack_plan=selected,
        result=result,
        findings=findings,
    )

    artifacts: ReportArtifacts | None = None
    if reporter is not None and plugins.reporter is not None:
        raw_artifacts = await _call_plugin(
            plugins.reporter, reporter.render(bundle, run_dir / "report")
        )
        artifacts = validate_plugin_output(
            raw_artifacts, ReportArtifacts, plugins.reporter
        )

    write_json(run_dir / "workflow.json", workflow)
    write_json(run_dir / "invariants.json", invariants)
    write_json(run_dir / "attack-plans.json", plans)
    write_json(run_dir / "selected-plan.json", selected)
    write_json(run_dir / "raw-attack-result.json", result)
    write_json(run_dir / "findings.json", findings)
    write_json(run_dir / "run-bundle.json", bundle)
    if artifacts is not None:
        write_json(run_dir / "report" / "artifacts.json", artifacts)

    verdict_counts = {"confirmed": 0, "probable": 0, "rejected": 0}
    for finding in findings:
        verdict_counts[str(finding.verdict)] += 1
    write_json(
        run_dir / "summary.json",
        {
            "schema_version": "0.1",
            "run_id": result.run_id,
            "workflow": workflow.name,
            "attack_plan_id": selected.id,
            "attack_type": selected.attack_type,
            "plugins": {
                "generator": plugins.generator,
                "executor": plugins.executor,
                "verifier": plugins.verifier,
                "reporter": plugins.reporter,
            },
            "verdict_counts": verdict_counts,
            "before_state": result.before_state,
            "after_state": result.after_state,
            "report_files": artifacts.files if artifacts else [],
        },
    )

    return PipelineOutcome(
        run_dir=run_dir,
        plans=plans,
        selected_plan=selected,
        result=result,
        findings=findings,
        report_artifacts=artifacts,
    )
