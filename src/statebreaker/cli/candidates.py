"""`statebreaker candidates` commands."""

from __future__ import annotations

import typer

from statebreaker.cli.common import fail, open_store
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi
from statebreaker.models.discovery import RaceCandidate

app = typer.Typer(help=bi("查看发现到的竞态候选。", "Inspect discovered race candidates."))


@app.command("list")
def list_candidates(project: str = typer.Option(..., "--project", "-p")) -> None:
    """List race candidates ranked by behavioral risk score."""
    try:
        store = open_store(project)
        try:
            candidate_ids = store.list_ids("candidates")
            if not candidate_ids:
                typer.echo(
                    bi(
                        "还没有 candidate；请先运行 `statebreaker scan`。",
                        "no candidates yet; run `statebreaker scan` first",
                    )
                )
                return
            candidates = [
                store.load("candidates", cid, RaceCandidate) for cid in candidate_ids
            ]
            candidates.sort(key=lambda candidate: candidate.score, reverse=True)
            typer.echo(
                bi(
                    "候选按行为风险分排序；score/kind/id 保留英文锚点，方便脚本解析。",
                    "Candidates are sorted by behavioral risk score; "
                    "score/kind/id stay script-friendly.",
                )
            )
            for candidate in candidates:
                typer.echo(
                    f"{candidate.score:>5.1f}  {candidate.kind:<12} {candidate.candidate_id}"
                )
                for reason in candidate.rationale:
                    typer.echo(f"        - {reason}")
        finally:
            store.close()
    except StateBreakerError as exc:
        fail(exc)
