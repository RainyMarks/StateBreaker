"""`statebreaker browser-context` commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import anyio
import typer

from statebreaker.browser_context import (
    BrowserContextRunResult,
    prepare_browser_context_plan,
    render_browser_context_executor,
    run_browser_context_executor,
)
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
    har: Annotated[
        Path | None,
        typer.Option(
            "--har",
            help="Use a HAR file as request-shape evidence when rendering.",
        ),
    ] = None,
    rounds: Annotated[
        int | None,
        typer.Option("--rounds", help="Override the plan's number of race rounds."),
    ] = None,
    start_round: Annotated[
        int | None,
        typer.Option("--start-round", help="Override the first displayed round number."),
    ] = None,
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
        script = render_browser_context_executor(
            plan,
            har_path=har,
            rounds=rounds,
            start_round=start_round,
        )
        if write is None:
            typer.echo(script)
            return
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(script, encoding="utf-8")
        typer.echo(f"browser-context executor: {write}")
        if har is not None:
            typer.echo(f"HAR evidence used for request shape: {har}")
        typer.echo(
            "next step: run the generated JavaScript only inside an authorized "
            "authenticated browser page for this plan."
        )
    except StateBreakerError as exc:
        fail(exc)


def _target_url_from_plan(plan: dict[str, object]) -> str | None:
    target = plan.get("target")
    if not isinstance(target, dict):
        return None
    url = target.get("url")
    return url if isinstance(url, str) and url else None


async def _run_browser_context_executor(
    script: str,
    cdp: str,
    target_url: str | None,
    timeout_seconds: float,
    screenshot: Path | None,
) -> BrowserContextRunResult:
    return await run_browser_context_executor(
        script,
        cdp=cdp,
        target_url=target_url,
        timeout_seconds=timeout_seconds,
        screenshot_path=screenshot,
    )


@app.command("run")
def run(
    plan: Annotated[Path, typer.Argument(help="Path to a browser-context JSON plan.")],
    har: Annotated[
        Path | None,
        typer.Option(
            "--har",
            help="Use a HAR file as request-shape evidence before executing.",
        ),
    ] = None,
    cdp: Annotated[
        str,
        typer.Option(
            "--cdp",
            help="Chrome DevTools HTTP endpoint or port, e.g. http://127.0.0.1:9222.",
        ),
    ] = "http://127.0.0.1:9222",
    target_url: Annotated[
        str | None,
        typer.Option(
            "--target-url",
            help="Page URL to match in the DevTools target list; defaults to plan target.url.",
        ),
    ] = None,
    rounds: Annotated[
        int,
        typer.Option("--rounds", help="Number of race rounds to execute."),
    ] = 1,
    start_round: Annotated[
        int | None,
        typer.Option("--start-round", help="Override the first displayed round number."),
    ] = None,
    write_result: Annotated[
        Path | None,
        typer.Option("--write-result", help="Write the JSON result returned by the browser."),
    ] = None,
    screenshot: Annotated[
        Path | None,
        typer.Option("--screenshot", help="Capture the target page after the executor finishes."),
    ] = None,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", help="Runtime.evaluate timeout for the browser run."),
    ] = 90.0,
) -> None:
    """Run a browser-context executor through Chrome DevTools Protocol."""
    try:
        prepared = prepare_browser_context_plan(
            plan,
            har_path=har,
            rounds=rounds,
            start_round=start_round,
            autorun=True,
        )
        script = render_browser_context_executor(
            plan,
            har_path=har,
            rounds=rounds,
            start_round=start_round,
            autorun=True,
        )
        selected_target_url = target_url or _target_url_from_plan(prepared)
        result = anyio.run(
            _run_browser_context_executor,
            script,
            cdp,
            selected_target_url,
            timeout_seconds,
            screenshot,
        )
        if write_result is not None:
            write_result.parent.mkdir(parents=True, exist_ok=True)
            write_result.write_text(
                json.dumps(result.value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            typer.echo(f"browser-context result: {write_result}")
        rows = result.value.get("rows")
        if isinstance(rows, list):
            ok_rows = sum(
                1
                for row in rows
                if isinstance(row, dict) and row.get("okA") is True and row.get("okB") is True
            )
            typer.echo(f"successful race rounds: {ok_rows} / {len(rows)}")
        final_total = result.value.get("finalTotal")
        if isinstance(final_total, str):
            typer.echo(f"final total: {final_total}")
        typer.echo(f"executed in page: {result.target_url}")
        if har is not None:
            evidence = prepared.get("har_evidence")
            if isinstance(evidence, dict):
                template_id = evidence.get("template_exchange_id")
                count = evidence.get("exchange_count")
                typer.echo(
                    f"HAR evidence used: {har} "
                    f"({count} exchange(s), template {template_id})"
                )
        if result.screenshot_path is not None:
            typer.echo(f"screenshot: {result.screenshot_path}")
    except StateBreakerError as exc:
        fail(exc)
