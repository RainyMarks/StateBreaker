"""`statebreaker scan`: the fully automatic race discovery run."""

from __future__ import annotations

from functools import partial

import anyio
import typer

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.cli.common import fail, latest_capture_id, load_config, open_store
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi
from statebreaker.models.execution import ExecutionTrial
from statebreaker.models.findings import Finding, ScanOutcome
from statebreaker.models.workflow import WorkflowGraph
from statebreaker.oracle.comparator import summarize_trial
from statebreaker.orchestration.scanner import AutoRaceScanner


def run_project_scan(
    project: str,
    capture_id: str | None = None,
) -> ScanOutcome:
    """Run the automatic scanner for one persisted project and capture."""
    store = open_store(project)
    try:
        config = load_config(project)
        selected_capture_id = capture_id or latest_capture_id(store)
        scanner = AutoRaceScanner(store)
        return anyio.run(
            partial(
                scanner.scan,
                config,
                capture_id=selected_capture_id,
                budget=config.budget,
            )
        )
    finally:
        store.close()


def print_scan_summary(project: str, outcome: ScanOutcome) -> list[Finding]:
    """Print a scan outcome and return the findings loaded for that run."""
    store = open_store(project)
    try:
        graph = (
            store.load("graphs", outcome.graph_id, WorkflowGraph)
            if outcome.graph_id
            else None
        )
        actions = len(graph.actions) if graph else 0
        confirmed_bindings = (
            sum(1 for binding in graph.variable_bindings if binding.status == "confirmed")
            if graph
            else 0
        )
        probes = len(graph.state_probes) if graph else 0

        typer.echo(
            f"Captured actions: {actions}  "
            f"({bi('录制到的动作', 'captured workflow actions')})"
        )
        typer.echo(
            f"Confirmed dependencies: {confirmed_bindings}  "
            f"({bi('扫描前确认的请求依赖', 'confirmed request dependencies')})"
        )
        typer.echo(
            f"State probes discovered: {probes}  "
            f"({bi('用于对比状态变化的检查请求', 'state-check requests for comparison')})"
        )
        typer.echo(
            f"High-risk actions: {len(outcome.candidate_ids)}  "
            f"({bi('进入计划生成的候选动作', 'candidate actions sent to planning')})"
        )
        typer.echo(
            f"Race plans tested: {len(outcome.plan_ids)}  "
            f"({bi('真实执行过的并发计划', 'concurrent plans actually tested')})"
        )
        typer.echo("")

        findings = [
            store.load("findings", finding_id, Finding)
            for finding_id in outcome.finding_ids
        ]
        for finding in findings:
            if finding.verdict == "confirmed":
                _print_confirmed(store, finding)

        counts: dict[str, int] = {}
        for finding in findings:
            counts[finding.verdict] = counts.get(finding.verdict, 0) + 1
        summary = ", ".join(
            f"{verdict}: {count}" for verdict, count in sorted(counts.items())
        )
        typer.echo(
            f"Findings: {summary or 'none'}  "
            f"({bi('本次扫描结论', 'scan verdict summary')})"
        )
        typer.echo(
            f"Budget: {outcome.stats.get('requests_used', 0)} requests, "
            f"{outcome.stats.get('trials_used', 0)} trials, "
            f"{outcome.stats.get('elapsed_seconds', 0)}s"
        )
        _print_scan_next_steps(project, findings)
        return findings
    finally:
        store.close()


def scan(
    project: str = typer.Option(..., "--project", "-p"),
    capture_id: str | None = typer.Option(None, "--capture-id"),
    auto: bool = typer.Option(
        False,
        "--auto",
        help=bi(
            "兼容选项：scan 当前本身就是非交互命令，保留该参数方便脚本复用。",
            "Compatibility flag: scan is already non-interactive; kept for scripts.",
        ),
    ),
) -> None:
    """Run the full automatic race scan: capture -> findings."""
    try:
        outcome = run_project_scan(project, capture_id)
        print_scan_summary(project, outcome)
    except StateBreakerError as exc:
        fail(exc)
        return


def _print_scan_next_steps(project: str, findings: list[Finding]) -> None:
    confirmed = [finding for finding in findings if finding.verdict == "confirmed"]
    typer.echo("")
    if confirmed:
        finding_id = confirmed[0].finding_id
        typer.echo(
            bi(
                f"下一步：运行 `statebreaker report {finding_id} --project {project}` 生成报告，"
                "或用 `statebreaker findings list` 查看全部结论。",
                f"Next step: run `statebreaker report {finding_id} --project {project}` "
                "to generate reports, or `statebreaker findings list` to inspect all findings.",
            )
        )
        return
    typer.echo(
        bi(
            "下一步：查看 `statebreaker findings list`；如果没有有用结论，"
            "尝试补充更完整的正常流量。",
            "Next step: inspect `statebreaker findings list`; if nothing useful appears, "
            "record a more complete normal flow.",
        )
    )


def _print_confirmed(store: ArtifactStore, finding: Finding) -> None:
    typer.secho(
        f"Finding: {finding.verdict.upper()}  ({finding.finding_id})", fg="green", bold=True
    )
    if finding.evidence_refs:
        control = store.load("trials", finding.evidence_refs[0], ExecutionTrial)
        control_effects = summarize_trial(control).side_effect_count
        attack_effects = 0.0
        if len(finding.evidence_refs) > 1:
            attack = store.load("trials", finding.evidence_refs[1], ExecutionTrial)
            attack_effects = summarize_trial(attack).side_effect_count
        typer.echo(
            f"Control result:    business side effects = {control_effects:g}  "
            f"({bi('正常顺序执行结果', 'sequential control result')})"
        )
        typer.echo(
            f"Concurrent result: business side effects = {attack_effects:g}  "
            f"({bi('并发攻击执行结果', 'concurrent attack result')})"
        )
    for line in finding.explanation:
        typer.echo(f"  {line}")
    if finding.success_rate is not None:
        total = len(finding.evidence_refs) - 1
        hits = round(finding.success_rate * total)
        typer.echo(f"Success rate: {hits}/{total}  ({bi('重复实验命中比例', 'repeat hit ratio')})")
    typer.echo("")
