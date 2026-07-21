"""Write all report artifacts for one finding into the project directory."""

from __future__ import annotations

from pathlib import Path

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.models.discovery import AttackPlan
from statebreaker.models.execution import ExecutionTrial
from statebreaker.models.findings import Finding
from statebreaker.reporting.html import render_html_report
from statebreaker.reporting.json_report import build_json_report
from statebreaker.reporting.poc import render_poc_script


def write_finding_reports(
    store: ArtifactStore,
    finding: Finding,
    plan: AttackPlan,
    *,
    control: ExecutionTrial | None,
    attacks: list[ExecutionTrial],
    poc_trial: ExecutionTrial | None,
) -> dict[str, str]:
    """Write PoC (.py), JSON bundle and HTML summary; return artifact paths."""
    reports_dir = Path(store.project_dir) / "reports"
    reports_dir.mkdir(exist_ok=True)
    paths: dict[str, str] = {}

    json_path = store.save_raw(
        "reports",
        f"{finding.finding_id}.report",
        build_json_report(finding, plan, control, attacks),
    )
    paths["json"] = str(json_path)

    html_path = reports_dir / f"{finding.finding_id}.html"
    html_path.write_text(render_html_report(finding, plan), encoding="utf-8")
    store.index.register("reports", f"{finding.finding_id}.html", html_path)
    paths["html"] = str(html_path)

    if poc_trial is not None:
        poc_path = reports_dir / f"{finding.finding_id}-poc.py"
        poc_path.write_text(
            render_poc_script(finding, plan, poc_trial), encoding="utf-8"
        )
        store.index.register("reports", f"{finding.finding_id}-poc.py", poc_path)
        paths["poc"] = str(poc_path)
    return paths
