"""`statebreaker discover`: analyze a capture without attacking."""

from __future__ import annotations

from functools import partial

import anyio
import typer

from statebreaker.cli.common import fail, latest_capture_id, load_config, open_store
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi
from statebreaker.models.capture import CapturedTrace
from statebreaker.orchestration.stages import DiscoveryResult, run_discovery


def run_project_discovery(
    project: str,
    capture_id: str | None = None,
) -> DiscoveryResult:
    """Run discovery for one persisted project and capture."""
    store = open_store(project)
    try:
        config = load_config(project)
        selected_capture_id = capture_id or latest_capture_id(store)
        trace = store.load("captures", selected_capture_id, CapturedTrace)
        return anyio.run(partial(run_discovery, config, trace, store=store))
    finally:
        store.close()


def print_discovery_summary(result: DiscoveryResult) -> None:
    """Print the stable user-facing discovery summary."""
    typer.echo(
        f"Workflow nodes: {len(result.graph.actions)}  "
        f"({bi('学到的流程动作数', 'learned workflow actions')})"
    )
    typer.echo(
        f"Confirmed variable bindings: {result.confirmed_bindings}  "
        f"({bi('已确认的请求依赖', 'confirmed request dependencies')})"
    )
    typer.echo(
        f"State probes: {len(result.probes)}  "
        f"({bi('可回读状态的检查请求', 'state-check requests')})"
    )
    typer.echo(
        f"High-risk actions: {len(result.high_risk_actions)}  "
        f"({bi('值得优先并发测试的动作', 'actions worth race testing')})"
    )
    typer.echo(
        f"Candidate action pairs: {result.candidate_pairs}  "
        f"({bi('候选并发动作组合', 'candidate concurrent action pairs')})"
    )
    if not result.replay_success:
        typer.secho(
            bi(
                "警告：正常流回放失败，依赖尚未确认",
                "warning: flow replay failed; bindings are unconfirmed",
            ),
            fg="yellow",
        )


def print_discovery_next_steps(project: str) -> None:
    """Tell beginners what to do after discovery without changing machine artifacts."""
    typer.echo("")
    typer.echo(
        bi(
            f"下一步：如果预览符合预期，运行 `statebreaker scan --project {project}` "
            "开始受控并发实验。",
            f"Next step: if this preview looks right, run `statebreaker scan --project {project}`.",
        )
    )


def discover(
    project: str = typer.Option(..., "--project", "-p"),
    capture_id: str | None = typer.Option(None, "--capture-id"),
) -> None:
    """Build the workflow graph and rank what looks race-prone."""
    try:
        result = run_project_discovery(project, capture_id)
    except StateBreakerError as exc:
        fail(exc)
        return

    print_discovery_summary(result)
    print_discovery_next_steps(project)
