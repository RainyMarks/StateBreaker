"""`statebreaker findings` commands."""

from __future__ import annotations

import typer

from statebreaker.cli.common import fail, open_store
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi
from statebreaker.models.findings import Finding

app = typer.Typer(help=bi("查看扫描结论。", "Inspect scan findings."))

_VERDICT_COLORS = {
    "confirmed": "green",
    "probable": "yellow",
    "rejected": "white",
    "inconclusive": "cyan",
}


def load_project_findings(project: str) -> list[Finding]:
    """Load all findings stored for a project."""
    store = open_store(project)
    try:
        return [
            store.load("findings", finding_id, Finding)
            for finding_id in store.list_ids("findings")
        ]
    finally:
        store.close()


def print_findings(
    findings: list[Finding],
    *,
    empty_message: str | None = None,
) -> None:
    """Print findings in the compact format shared by list and wizard."""
    if not findings:
        typer.echo(
            empty_message
            or bi(
                "还没有 finding；请先运行 `statebreaker scan`。",
                "no findings yet; run `statebreaker scan` first",
            )
        )
        return
    for finding in findings:
        color = _VERDICT_COLORS.get(finding.verdict, "white")
        typer.secho(
            f"{finding.verdict.upper():<12} {finding.confidence:>4.2f}  "
            f"{finding.finding_id}  actions={finding.candidate.action_ids}",
            fg=color,
        )
        if finding.success_rate is not None and finding.verdict == "confirmed":
            typer.echo(
                f"             success_rate={finding.success_rate}  "
                f"({bi('重复实验命中率', 'repeat hit rate')})"
            )


@app.command("list")
def list_findings(project: str = typer.Option(..., "--project", "-p")) -> None:
    """List all findings of a project with verdict and confidence."""
    try:
        print_findings(load_project_findings(project))
    except StateBreakerError as exc:
        fail(exc)
