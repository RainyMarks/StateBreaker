"""`statebreaker browser-context` commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from statebreaker.browser_context import render_browser_context_executor
from statebreaker.cli.common import fail
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi

app = typer.Typer(
    help=bi(
        "Render an authenticated browser-context executor for an authorized plan.",
        "Render an authenticated browser-context executor for an authorized plan.",
    ),
    no_args_is_help=True,
)


@app.command("render")
def render(
    plan: Annotated[Path, typer.Argument(help="Path to a browser-context JSON plan.")],
    write: Annotated[
        Path | None,
        typer.Option(
            "--write",
            "-w",
            help="Write the generated JavaScript executor to this file.",
        ),
    ] = None,
) -> None:
    """Render a browser-console/CDP executor from a generic plan."""
    try:
        script = render_browser_context_executor(plan)
        if write is None:
            typer.echo(script)
            return
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(script, encoding="utf-8")
        typer.echo(f"browser-context executor: {write}")
        typer.echo(
            "next step: run the generated JavaScript only inside an authorized "
            "authenticated browser page for this plan."
        )
    except StateBreakerError as exc:
        fail(exc)
