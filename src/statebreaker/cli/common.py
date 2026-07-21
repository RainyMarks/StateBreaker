"""Shared CLI helpers: workspace layout, config and store resolution."""

from __future__ import annotations

from pathlib import Path

import typer

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.config.loader import load_project_config
from statebreaker.config.models import ProjectConfig
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi


def project_dir(name: str, workspace: Path | None = None) -> Path:
    root = workspace or Path.cwd()
    return ArtifactStore.default_project_dir(root, name)


def list_projects(workspace: Path | None = None) -> list[str]:
    """List configured projects in the current StateBreaker workspace."""
    root = workspace or Path.cwd()
    projects_dir = root / ".statebreaker" / "projects"
    if not projects_dir.exists():
        return []
    try:
        return sorted(
            entry.name
            for entry in projects_dir.iterdir()
            if entry.is_dir() and (entry / "project.yaml").is_file()
        )
    except OSError as exc:
        raise StateBreakerError(f"cannot list projects in {projects_dir}: {exc}") from exc


def open_store(name: str) -> ArtifactStore:
    directory = project_dir(name)
    if not directory.exists():
        raise StateBreakerError(
            bi(
                f"项目 {name!r} 不存在：{directory}；请先运行 `statebreaker project init`",
                f"project {name!r} not found at {directory}; run `statebreaker project init` first",
            )
        )
    return ArtifactStore(directory)


def load_config(name: str) -> ProjectConfig:
    config_path = project_dir(name) / "project.yaml"
    return load_project_config(config_path)


def latest_capture_id(store: ArtifactStore) -> str:
    captures = store.list_ids("captures")
    if not captures:
        raise StateBreakerError(
            bi(
                "还没有 capture；请先导入或录制一段正常流程",
                "no captures stored; import a trace first",
            )
        )
    return captures[-1]


def fail(exc: StateBreakerError) -> None:
    typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code=2) from exc
