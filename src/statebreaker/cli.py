"""Stable command-line surface for the StateBreaker core and external plugins."""

from __future__ import annotations

import asyncio
import json
import platform
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel, TypeAdapter, ValidationError

from statebreaker import __version__
from statebreaker.documents import load_model, load_typed, write_json
from statebreaker.errors import DocumentError, PluginError, StateBreakerError
from statebreaker.models import (
    API_VERSION,
    AttackPlan,
    Extractor,
    Finding,
    Invariant,
    LearningResult,
    PluginManifest,
    RawAttackResult,
    ReportArtifacts,
    RequestStep,
    RunBundle,
    RunEvent,
    StateProfile,
    Workflow,
)
from statebreaker.plugins import PluginRegistry
from statebreaker.runtime import ExecutionRuntime

app = typer.Typer(
    name="statebreaker",
    help="面向授权业务逻辑安全测试的可扩展工作流骨架。",
    no_args_is_help=True,
    add_completion=False,
)
plugins_app = typer.Typer(help="发现和检查已安装插件。")
schema_app = typer.Typer(help="导出稳定的 JSON Schema 契约。")
workflow_app = typer.Typer(help="导入和校验正常业务工作流。")
app.add_typer(plugins_app, name="plugins")
app.add_typer(schema_app, name="schema")
app.add_typer(workflow_app, name="workflow")

EXIT_VALIDATION = 2
EXIT_PLUGIN = 3
EXIT_RUNTIME = 4


def _abort(message: str, code: int) -> None:
    typer.echo(f"错误：{message}", err=True)
    raise typer.Exit(code=code)


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _abort_plugin_contract(exc: BaseException, plugin_id: str | None = None) -> None:
    """Map unexpected plugin contract failures to the stable plugin exit code."""

    prefix = f"plugin {plugin_id!r} failed: " if plugin_id else ""
    _abort(f"{prefix}{exc}", EXIT_PLUGIN)


def _validate_plugin_output(value: Any, annotation: Any, plugin_id: str) -> Any:
    try:
        return TypeAdapter(annotation).validate_python(value)
    except ValidationError as exc:
        raise PluginError(f"plugin {plugin_id!r} returned invalid data: {exc}") from exc


def _write_output(path: Path, value: Any) -> None:
    write_json(path, value)
    typer.echo(f"已写入：{path.resolve()}")


@app.command()
def doctor() -> None:
    """Check the local core installation without making network requests."""

    supported = sys.version_info >= (3, 11)
    typer.echo(f"StateBreaker core: {__version__} (API {API_VERSION})")
    typer.echo(f"Python: {platform.python_version()} {'OK' if supported else '需要 3.11+'}")
    typer.echo(f"运行目录: {Path.cwd()}")
    typer.echo("目标限制: 未启用；仅可测试自有或已明确授权的系统")
    if not supported:
        raise typer.Exit(code=EXIT_RUNTIME)


@plugins_app.command("list")
def list_plugins(
    group: Annotated[str | None, typer.Option(help="只显示指定 entry-point 组")] = None,
) -> None:
    """List validated plugins and their capabilities."""

    try:
        discovered = PluginRegistry().discover(group)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    if not discovered:
        typer.echo("未发现插件。可从 plugin-template 复制一个独立插件包。")
        return
    typer.echo("GROUP\tPLUGIN ID\tVERSION\tCAPABILITIES")
    for manifest, _ in discovered:
        typer.echo(
            f"{manifest.group}\t{manifest.plugin_id}\t{manifest.version}\t"
            f"{','.join(manifest.capabilities) or '-'}"
        )


SCHEMA_MODELS: tuple[type[BaseModel], ...] = (
    Workflow,
    RequestStep,
    Extractor,
    StateProfile,
    Invariant,
    LearningResult,
    AttackPlan,
    RunEvent,
    RawAttackResult,
    Finding,
    RunBundle,
    ReportArtifacts,
    PluginManifest,
)


@schema_app.command("export")
def export_schema(
    output_dir: Annotated[Path, typer.Argument()] = Path("schemas"),
) -> None:
    """Export one deterministic JSON Schema document per public model."""

    output_dir.mkdir(parents=True, exist_ok=True)
    for model_type in SCHEMA_MODELS:
        destination = output_dir / f"{model_type.__name__}.schema.json"
        destination.write_text(
            json.dumps(model_type.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    typer.echo(f"已导出 {len(SCHEMA_MODELS)} 个契约：{output_dir.resolve()}")


@workflow_app.command("validate")
def validate_workflow(path: Path) -> None:
    """Validate a YAML or JSON workflow without sending traffic."""

    try:
        workflow = load_model(path, Workflow)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    typer.echo(
        f"工作流有效：{workflow.name}，{len(workflow.steps)} 步，"
        f"{len(workflow.sessions)} 个隔离会话。"
    )


@workflow_app.command("import")
def import_workflow(
    source: Path,
    plugin_id: Annotated[str, typer.Option("--plugin", help="capture 插件 ID")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("workflow.json"),
) -> None:
    """Ask an installed capture plugin to produce a Workflow."""

    try:
        plugin = PluginRegistry().get("statebreaker.capture", plugin_id)
        result = _run(plugin.capture(source, {}))
        workflow = _validate_plugin_output(result, Workflow, plugin_id)
        _write_output(output, workflow)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@app.command()
def learn(
    workflow_path: Path,
    plugin_id: Annotated[str, typer.Option("--plugin", help="learner 插件 ID")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("learning-result.json"),
) -> None:
    """Dispatch normal-state learning to an installed plugin."""

    try:
        workflow = load_model(workflow_path, Workflow)
        plugin = PluginRegistry().get("statebreaker.learner", plugin_id)

        async def invoke() -> Any:
            async with ExecutionRuntime(workflow) as runtime:
                return await plugin.learn(workflow, runtime)

        result = _validate_plugin_output(_run(invoke()), LearningResult, plugin_id)
        _write_output(output, result)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@app.command()
def generate(
    workflow_path: Path,
    invariants_path: Path,
    plugin_id: Annotated[str, typer.Option("--plugin", help="generator 插件 ID")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("attack-plans.json"),
) -> None:
    """Dispatch attack-plan generation to an installed plugin."""

    try:
        workflow = load_model(workflow_path, Workflow)
        invariants = load_typed(invariants_path, list[Invariant])
        plugin = PluginRegistry().get("statebreaker.generator", plugin_id)
        result = _run(plugin.generate(workflow, invariants))
        plans = _validate_plugin_output(result, list[AttackPlan], plugin_id)
        _write_output(output, plans)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@app.command()
def attack(
    plan_path: Path,
    workflow_path: Annotated[Path, typer.Option("--workflow", help="对应 Workflow 文件")],
    plugin_id: Annotated[str, typer.Option("--plugin", help="executor 插件 ID")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        "raw-attack-result.json"
    ),
) -> None:
    """Dispatch an AttackPlan to an installed executor plugin."""

    try:
        workflow = load_model(workflow_path, Workflow)
        plan = load_model(plan_path, AttackPlan)
        if plan.workflow_name != workflow.name:
            raise DocumentError(
                f"attack plan targets {plan.workflow_name!r}, workflow is {workflow.name!r}"
            )
        unknown_steps = set(plan.target_steps) - {step.id for step in workflow.steps}
        if unknown_steps:
            raise DocumentError(f"attack plan references unknown steps: {sorted(unknown_steps)}")
        plugin = PluginRegistry().get("statebreaker.executor", plugin_id)

        async def invoke() -> Any:
            async with ExecutionRuntime(workflow) as runtime:
                return await plugin.execute(plan, runtime)

        result = _validate_plugin_output(_run(invoke()), RawAttackResult, plugin_id)
        _write_output(output, result)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@app.command()
def verify(
    result_path: Path,
    invariants_path: Path,
    plugin_id: Annotated[str, typer.Option("--plugin", help="verifier 插件 ID")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("findings.json"),
) -> None:
    """Dispatch state evidence to an installed verifier plugin."""

    try:
        result = load_model(result_path, RawAttackResult)
        invariants = load_typed(invariants_path, list[Invariant])
        plugin = PluginRegistry().get("statebreaker.verifier", plugin_id)
        findings = _validate_plugin_output(
            _run(plugin.verify(result, invariants)), list[Finding], plugin_id
        )
        _write_output(output, findings)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@app.command()
def report(
    bundle_path: Path,
    plugin_id: Annotated[str, typer.Option("--plugin", help="reporter 插件 ID")],
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path("report"),
) -> None:
    """Dispatch a complete run bundle to an installed reporter plugin."""

    try:
        bundle = load_model(bundle_path, RunBundle)
        plugin = PluginRegistry().get("statebreaker.reporter", plugin_id)
        artifacts = _validate_plugin_output(
            _run(plugin.render(bundle, output_dir)), ReportArtifacts, plugin_id
        )
        _write_output(output_dir / "artifacts.json", artifacts)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


def main() -> None:
    app()
