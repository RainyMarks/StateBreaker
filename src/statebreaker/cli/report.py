"""`statebreaker report` and `statebreaker reproduce` commands."""

from __future__ import annotations

import typer

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.cli.common import fail, open_store
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi
from statebreaker.models.discovery import AttackPlan
from statebreaker.models.execution import ExecutionTrial
from statebreaker.models.findings import Finding
from statebreaker.reporting import render_poc_script, write_finding_reports


def _load_finding_context(
    store: ArtifactStore,
    finding_id: str,
) -> tuple[Finding, AttackPlan, ExecutionTrial | None, list[ExecutionTrial]]:
    finding = store.load("findings", finding_id, Finding)
    plan_id = finding.minimized_plan_id
    if plan_id is None:
        raise StateBreakerError(
            f"finding {finding_id!r} has no minimized plan; reports need a confirmed verdict"
        )
    plan = store.load("plans", plan_id, AttackPlan)
    trials = [
        store.load("trials", trial_id, ExecutionTrial)
        for trial_id in finding.evidence_refs
        if store.exists("trials", trial_id)
    ]
    control = next((t for t in trials if t.control_or_attack == "control"), None)
    attacks = [t for t in trials if t.control_or_attack == "attack"]
    return finding, plan, control, attacks


def generate_finding_report(project: str, finding_id: str) -> dict[str, str]:
    """Generate all report formats for a persisted confirmed finding."""
    store = open_store(project)
    try:
        finding, plan, control, attacks = _load_finding_context(store, finding_id)
        return write_finding_reports(
            store,
            finding,
            plan,
            control=control,
            attacks=attacks,
            poc_trial=attacks[0] if attacks else None,
        )
    finally:
        store.close()


def print_report_paths(paths: dict[str, str]) -> None:
    """Print generated report paths in their stable CLI format."""
    for kind, path in paths.items():
        typer.echo(f"{kind}: {path}")
    if paths:
        typer.echo(
            bi(
                "下一步：打开 html 阅读摘要；需要复现实验时查看 poc 文件，"
                "JSON bundle 保留机器可解析证据。",
                "Next step: open the html summary; use the poc file to reproduce and keep "
                "the JSON bundle for machine-readable evidence.",
            )
        )


def report(
    finding_id: str,
    project: str = typer.Option(..., "--project", "-p"),
) -> None:
    """Generate PoC, JSON evidence bundle and HTML report for a finding."""
    try:
        print_report_paths(generate_finding_report(project, finding_id))
    except StateBreakerError as exc:
        fail(exc)


def reproduce(
    finding_id: str,
    project: str = typer.Option(..., "--project", "-p"),
    write: bool = typer.Option(
        False,
        "--write",
        help=bi(
            "把 PoC 写入文件，而不是打印到 stdout。",
            "Write the PoC to a file instead of stdout.",
        ),
    ),
) -> None:
    """Print (or write) the executable PoC for a confirmed finding."""
    try:
        store = open_store(project)
        try:
            finding, plan, _, attacks = _load_finding_context(store, finding_id)
            if not attacks:
                raise StateBreakerError(
                    f"finding {finding_id!r} has no recorded attack trial"
                )
            script = render_poc_script(finding, plan, attacks[0])
            if not write:
                typer.echo(script)
                return
            path = store.project_dir / "reports" / f"{finding.finding_id}-poc.py"
            path.write_text(script, encoding="utf-8")
            typer.echo(f"poc: {path}")
            typer.echo(
                bi(
                    "下一步：在授权测试环境中审阅并运行该 PoC。",
                    "Next step: review and run this PoC only in an authorized test environment.",
                )
            )
        finally:
            store.close()
    except StateBreakerError as exc:
        fail(exc)
