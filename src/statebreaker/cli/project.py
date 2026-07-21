"""`statebreaker project` commands."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import typer

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.cli.common import fail, project_dir
from statebreaker.errors import StateBreakerError
from statebreaker.i18n import bi

app = typer.Typer(help=bi("管理 StateBreaker 项目。", "Manage StateBreaker projects."))

_PROJECT_YAML = """schema_version: "0.2"
project:
  name: {name}
  base_url: {base_url}
scope:
  allowed_hosts: [{host}]
  excluded_paths: []
  requests_per_second: 10
sessions:
  alice: {{}}
reset:
  strategy: fresh-resource
discovery:
  max_candidates: 20
  max_action_pairs: 30
execution:
  schedulers: [async-http]
  concurrency: [2, 4, 8]
  offsets_ms: [0]
  repetitions: 5
oracle:
  require_state_evidence_for_confirmed: true
budget:
  maximum_requests: 1000
  maximum_minutes: 30
"""


def create_project(name: str, base_url: str) -> Path:
    """Create a project workspace and return its directory."""
    name = name.strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise StateBreakerError(
            bi(
                "项目名必须是单个非空目录名",
                "project name must be a single non-empty directory name",
            )
        )
    directory = project_dir(name)
    if directory.exists():
        raise StateBreakerError(
            bi(
                f"项目 {name!r} 已存在：{directory}",
                f"project {name!r} already exists at {directory}",
            )
        )

    host = urlparse(base_url).hostname or "127.0.0.1"
    store = ArtifactStore(directory)
    try:
        (directory / "project.yaml").write_text(
            _PROJECT_YAML.format(
                name=json.dumps(name),
                base_url=json.dumps(base_url),
                host=json.dumps(host),
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise StateBreakerError(
            bi(
                f"无法写入项目配置：{directory}: {exc}",
                f"cannot write project config at {directory}: {exc}",
            )
        ) from exc
    finally:
        store.close()
    return directory


@app.command("init")
def init(
    name: str,
    base_url: str = typer.Option(
        "http://127.0.0.1:8080",
        "--base-url",
        help=bi("目标测试环境的基础 URL。", "Base URL of the target test environment."),
    ),
) -> None:
    """Create a project workspace with a starter project.yaml."""
    try:
        directory = create_project(name, base_url)
        typer.echo(
            f"project initialized at {directory}  "
            f"({bi('项目已初始化', 'starter project created')})"
        )
        typer.echo(
            bi(
                "下一步：导入正常流量，或运行 `statebreaker run --proxy-capture` 进入一键流程。",
                "Next step: import a normal flow, or run `statebreaker run --proxy-capture`.",
            )
        )
    except StateBreakerError as exc:
        fail(exc)
