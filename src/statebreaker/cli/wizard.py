"""Guided StateBreaker workflow for users who prefer prompts over long commands."""

from __future__ import annotations

from pathlib import Path

import typer

from statebreaker.cli.capture import (
    import_capture_file,
    print_proxy_capture_summary,
    record_browser_capture,
    record_proxy_capture,
    save_capture_trace,
)
from statebreaker.cli.common import fail, list_projects, load_config, open_store, project_dir
from statebreaker.cli.discover import print_discovery_summary, run_project_discovery
from statebreaker.cli.findings import print_findings
from statebreaker.cli.project import create_project
from statebreaker.cli.report import generate_finding_report, print_report_paths
from statebreaker.cli.scan import print_scan_summary, run_project_scan
from statebreaker.errors import StateBreakerError

_CAPTURE_FILE_OPTION = typer.Option(
    None,
    "--capture-file",
    help="Import this HAR or Postman file.",
)


def wizard(
    project: str | None = typer.Option(None, "--project", "-p", help="Use or create a project."),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Base URL when the selected project must be created.",
    ),
    capture_file: Path | None = _CAPTURE_FILE_OPTION,
    capture_id: str | None = typer.Option(
        None,
        "--capture-id",
        help="Use this capture, or assign this id while importing or recording.",
    ),
    proxy_capture: bool = typer.Option(
        False,
        "--proxy-capture",
        help="Record the normal flow through a local HTTP proxy.",
    ),
    browser_capture: bool = typer.Option(
        False,
        "--browser-capture",
        help="Open a browser and record the normal flow through CDP; supports HTTPS.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Run the scan and generate confirmed reports without confirmation.",
    ),
    skip_scan: bool = typer.Option(
        False,
        "--skip-scan",
        help="Stop after the discovery preview.",
    ),
) -> None:
    """Guide project setup, capture import, discovery, scanning, and reporting."""
    try:
        _run_workflow(
            project=project,
            base_url=base_url,
            capture_file=capture_file,
            capture_id=capture_id,
            browser_capture=browser_capture,
            proxy_capture=proxy_capture,
            skip_scan=skip_scan,
            skip_scan_message=(
                "Stopped after discovery preview (--skip-scan).  "
                "Next: run scan or restart wizard after reviewing the preview."
            ),
            confirm_scan=not auto,
            generate_reports=True,
            confirm_reports=not auto,
            auto_select_latest_capture=False,
        )
    except StateBreakerError as exc:
        fail(exc)


def run(
    project: str | None = typer.Option(None, "--project", "-p", help="Use or create a project."),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Base URL when the selected project must be created.",
    ),
    capture_file: Path | None = _CAPTURE_FILE_OPTION,
    capture_id: str | None = typer.Option(
        None,
        "--capture-id",
        help="Use this capture, or assign this id while importing or recording.",
    ),
    proxy_capture: bool = typer.Option(
        False,
        "--proxy-capture",
        help="Record the normal flow through a local HTTP proxy.",
    ),
    browser_capture: bool = typer.Option(
        False,
        "--browser-capture",
        help="Open a browser and record the normal flow through CDP; supports HTTPS.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Accepted for wizard parity; run skips confirmations by default.",
    ),
    discovery_only: bool = typer.Option(
        False,
        "--discovery-only",
        help="Stop after discovery without running race experiments.",
    ),
    skip_report: bool = typer.Option(
        False,
        "--skip-report",
        help="Do not generate reports for confirmed findings.",
    ),
) -> None:
    """Run the one-command workflow: project -> capture -> discover -> scan -> report."""
    _ = auto
    try:
        _run_workflow(
            project=project,
            base_url=base_url,
            capture_file=capture_file,
            capture_id=capture_id,
            browser_capture=browser_capture,
            proxy_capture=proxy_capture,
            skip_scan=discovery_only,
            skip_scan_message=(
                "Stopped after discovery preview (--discovery-only).  "
                "Next: run scan after reviewing the preview."
            ),
            confirm_scan=False,
            generate_reports=not skip_report,
            confirm_reports=False,
            auto_select_latest_capture=True,
        )
    except StateBreakerError as exc:
        fail(exc)


def _run_workflow(
    *,
    project: str | None,
    base_url: str | None,
    capture_file: Path | None,
    capture_id: str | None,
    browser_capture: bool,
    proxy_capture: bool,
    skip_scan: bool,
    skip_scan_message: str,
    confirm_scan: bool,
    generate_reports: bool,
    confirm_reports: bool,
    auto_select_latest_capture: bool,
) -> None:
    project_name = _select_project(project, base_url)
    selected_capture_id = _select_capture(
        project_name,
        capture_file=capture_file,
        capture_id=capture_id,
        browser_capture=browser_capture,
        proxy_capture=proxy_capture,
        auto_select_latest=auto_select_latest_capture,
    )

    _heading("Discovery preview")
    discovery = run_project_discovery(project_name, selected_capture_id)
    print_discovery_summary(discovery)

    if skip_scan:
        typer.echo(skip_scan_message)
        return
    if confirm_scan and not typer.confirm(
        "Run concurrent race experiments against this target?",
        default=False,
    ):
        typer.echo("Scan cancelled; discovery artifacts were kept.")
        return

    _heading("Race scan")
    outcome = run_project_scan(project_name, selected_capture_id)
    scan_findings = print_scan_summary(project_name, outcome)

    _heading("Findings from this scan")
    print_findings(scan_findings, empty_message="No findings produced by this scan.")
    confirmed = [finding for finding in scan_findings if finding.verdict == "confirmed"]
    if not confirmed or not generate_reports:
        return
    if confirm_reports and not typer.confirm(
        f"Generate reports for {len(confirmed)} confirmed finding(s)?",
        default=True,
    ):
        return

    _heading("Reports")
    for finding in confirmed:
        typer.echo(f"{finding.finding_id}:")
        print_report_paths(generate_finding_report(project_name, finding.finding_id))


def _select_project(requested: str | None, base_url: str | None) -> str:
    if requested is not None:
        name = requested.strip()
        if not name:
            raise StateBreakerError("project name cannot be empty")
        if project_dir(name).exists():
            typer.echo(f"Using project {name!r}.")
            return name
        return _create_project(name, base_url)

    projects = list_projects()
    if projects:
        typer.echo("Available projects:")
        for index, name in enumerate(projects, start=1):
            typer.echo(f"  {index}. {name}")
        create_index = len(projects) + 1
        typer.echo(f"  {create_index}. Create a new project")
        raw_selection = typer.prompt("Select a project", default="1")
        try:
            selection = int(raw_selection)
        except ValueError as exc:
            raise StateBreakerError(f"invalid project selection: {raw_selection!r}") from exc
        if 1 <= selection <= len(projects):
            return projects[selection - 1]
        if selection != create_index:
            raise StateBreakerError(f"project selection must be between 1 and {create_index}")
    else:
        typer.echo("No StateBreaker projects found; create the first one.")

    name = typer.prompt("Project name").strip()
    return _create_project(name, base_url)


def _create_project(name: str, base_url: str | None) -> str:
    selected_base_url = base_url or typer.prompt(
        "Base URL",
        default="http://127.0.0.1:8080",
    )
    directory = create_project(name, selected_base_url)
    typer.echo(f"Created project at {directory}.")
    return name


def _select_capture(
    project: str,
    *,
    capture_file: Path | None,
    capture_id: str | None,
    browser_capture: bool,
    proxy_capture: bool,
    auto_select_latest: bool = False,
) -> str:
    if capture_file is not None:
        trace = import_capture_file(capture_file.expanduser(), project, capture_id=capture_id)
        _print_import(trace.capture_id, len(trace.exchanges))
        return trace.capture_id
    if browser_capture:
        return _record_browser_capture(project, capture_id)
    if proxy_capture:
        return _record_proxy_capture(project, capture_id)

    store = open_store(project)
    missing_requested_capture = False
    try:
        capture_ids = store.list_ids("captures")
        if capture_id is not None:
            if store.exists("captures", capture_id):
                typer.echo(f"Using capture {capture_id!r}.")
                return capture_id
            missing_requested_capture = True
            typer.echo(
                f"Capture {capture_id!r} was not found; it will be used for a new import "
                "or recording."
            )
    finally:
        store.close()

    if capture_ids:
        latest = capture_ids[-1]
        if missing_requested_capture:
            typer.echo(
                f"Latest capture {latest!r} exists, but requested capture "
                f"{capture_id!r} will be created instead."
            )
        else:
            typer.echo(f"Latest capture: {latest!r}")
        if auto_select_latest and not missing_requested_capture:
            typer.echo("Using latest capture.")
            return latest
        if not missing_requested_capture and typer.confirm("Use this capture?", default=True):
            return latest
    else:
        typer.echo("No captures found; import a normal-flow trace.")

    typer.echo("Capture options:")
    typer.echo("  1. Browser auto-recording (recommended for HTTPS)")
    typer.echo("  2. Record through the local HTTP proxy")
    typer.echo("  3. Import HAR/Postman file")
    selection = typer.prompt("Select capture method", default="1").strip().lower()
    if selection in {"1", "browser", "b"}:
        return _record_browser_capture(project, capture_id)
    if selection in {"2", "proxy", "p", "y", "yes"}:
        return _record_proxy_capture(project, capture_id)
    if selection not in {"3", "import", "i", "n", "no"}:
        raise StateBreakerError(f"invalid capture method selection: {selection!r}")
    file = Path(typer.prompt("HAR or Postman file")).expanduser()
    trace = import_capture_file(file, project, capture_id=capture_id)
    _print_import(trace.capture_id, len(trace.exchanges))
    return trace.capture_id


def _record_browser_capture(project: str, capture_id: str | None) -> str:
    start_url = load_config(project).project.base_url
    trace = record_browser_capture(project, capture_id=capture_id, start_url=start_url)
    save_capture_trace(project, trace)
    print_proxy_capture_summary(trace, leading_verb="Recorded")
    return trace.capture_id


def _record_proxy_capture(project: str, capture_id: str | None) -> str:
    trace = record_proxy_capture(project, capture_id=capture_id)
    save_capture_trace(project, trace)
    print_proxy_capture_summary(trace, leading_verb="Recorded")
    return trace.capture_id


def _print_import(capture_id: str, exchanges: int) -> None:
    typer.echo(f"Imported {exchanges} exchanges as capture {capture_id!r}.")


def _heading(title: str) -> None:
    typer.secho(f"\n{title}", bold=True)
