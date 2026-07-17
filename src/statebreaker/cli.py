"""Stable, non-interactive command-line interface for StateBreaker."""

from __future__ import annotations

import asyncio
import json
import platform
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

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
from statebreaker.pipeline import (
    PipelineOutcome,
    PipelinePlugins,
    run_pipeline,
    validate_plan_for_workflow,
    validate_plugin_output,
)
from statebreaker.plugins import PluginRegistry
from statebreaker.runtime import ExecutionRuntime

app = typer.Typer(
    name="statebreaker",
    help="可扩展、可脚本化的业务逻辑安全测试骨架。",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
)
plugins_app = typer.Typer(help="发现并检查独立插件。")
schema_app = typer.Typer(help="导出稳定的 JSON Schema 数据契约。")
workflow_app = typer.Typer(help="校验、导入或重放正常业务工作流。")
pipeline_app = typer.Typer(help="串联生成、执行、验证和报告插件。")
app.add_typer(plugins_app, name="plugins")
app.add_typer(schema_app, name="schema")
app.add_typer(workflow_app, name="workflow")
app.add_typer(pipeline_app, name="pipeline")

EXIT_VALIDATION = 2
EXIT_PLUGIN = 3
EXIT_RUNTIME = 4

DEFAULT_GENERATOR = "team.race-generator"
DEFAULT_EXECUTOR = "team.race-executor"
DEFAULT_VERIFIER = "team.basic-verifier"
DEFAULT_REPORTER = "team.pdf-reporter"


@app.callback()
def root(
    version: Annotated[
        bool, typer.Option("--version", help="显示核心版本并退出。", is_eager=True)
    ] = False,
) -> None:
    if version:
        typer.echo(f"StateBreaker {__version__} (core API {API_VERSION})")
        raise typer.Exit()


def _abort(message: str, code: int) -> None:
    typer.echo(f"错误：{message}", err=True)
    raise typer.Exit(code=code)


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _abort_plugin_contract(exc: BaseException, plugin_id: str | None = None) -> None:
    prefix = f"plugin {plugin_id!r} failed: " if plugin_id else ""
    _abort(f"{prefix}{exc}", EXIT_PLUGIN)


def _write_output(path: Path, value: Any) -> None:
    write_json(path, value)
    typer.echo(f"已写入：{path.resolve()}")


def _with_target(workflow: Workflow, target: str | None) -> Workflow:
    if target is None:
        return workflow
    payload = workflow.model_dump(mode="json")
    payload["base_url"] = target
    try:
        return Workflow.model_validate(payload)
    except ValueError as exc:
        raise DocumentError(f"invalid target URL {target!r}: {exc}") from exc


def _plugins(
    generator: str,
    executor: str,
    verifier: str,
    reporter: str | None,
) -> PipelinePlugins:
    return PipelinePlugins(
        generator=generator,
        executor=executor,
        verifier=verifier,
        reporter=reporter,
    )


def _show_pipeline_outcome(outcome: PipelineOutcome) -> None:
    counts = {"confirmed": 0, "probable": 0, "rejected": 0}
    for finding in outcome.findings:
        counts[str(finding.verdict)] += 1
    typer.echo(f"[2/4] 已执行计划：{outcome.selected_plan.id}")
    typer.echo(
        "[3/4] 验证结论："
        f"confirmed={counts['confirmed']} "
        f"probable={counts['probable']} rejected={counts['rejected']}"
    )
    if outcome.report_artifacts is None:
        typer.echo("[4/4] 已跳过报告插件。")
    else:
        typer.echo(f"[4/4] 已生成报告文件：{len(outcome.report_artifacts.files)}")
    typer.echo(f"运行产物：{outcome.run_dir.resolve()}")


def _invoke_pipeline(
    workflow_path: Path,
    invariants_path: Path,
    *,
    target: str | None,
    generator: str,
    executor: str,
    verifier: str,
    reporter: str | None,
    output_root: Path,
    plan_id: str | None,
    attack_type: str | None,
) -> None:
    try:
        workflow = _with_target(load_model(workflow_path, Workflow), target)
        invariants = load_typed(invariants_path, list[Invariant])
        typer.echo(f"[1/4] 工作流有效：{workflow.name}，目标 {workflow.base_url}")
        outcome = _run(
            run_pipeline(
                workflow,
                invariants,
                plugins=_plugins(generator, executor, verifier, reporter),
                output_root=output_root,
                plan_id=plan_id,
                attack_type=attack_type,
            )
        )
        _show_pipeline_outcome(outcome)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@app.command()
def doctor() -> None:
    """检查核心环境，不发送任何网络请求。"""

    supported = sys.version_info >= (3, 11)
    typer.echo(f"StateBreaker core: {__version__} (API {API_VERSION})")
    typer.echo(f"Python: {platform.python_version()} {'OK' if supported else '需要 3.11+'}")
    typer.echo(f"运行目录：{Path.cwd()}")
    typer.echo("网络策略：核心不限制目标；请仅测试自有或已明确授权的系统。")
    if not supported:
        raise typer.Exit(code=EXIT_RUNTIME)


@plugins_app.command("list")
def list_plugins(
    group: Annotated[str | None, typer.Option(help="只显示指定 entry-point 组。")]
    = None,
) -> None:
    """列出已安装且契约有效的插件。"""

    try:
        discovered = PluginRegistry().discover(group)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    if not discovered:
        typer.echo("未发现插件。请安装一个独立插件包后重试。")
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
    """为每个公共模型导出一份确定性的 JSON Schema。"""

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
    """校验 YAML/JSON 工作流，不发送流量。"""

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
    """调用 capture 插件生成统一 Workflow。"""

    try:
        plugin = PluginRegistry().get("statebreaker.capture", plugin_id)
        workflow = validate_plugin_output(_run(plugin.capture(source, {})), Workflow, plugin_id)
        _write_output(output, workflow)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@workflow_app.command("replay")
def replay_workflow(
    workflow_path: Path,
    target: Annotated[str | None, typer.Option("--target", help="临时覆盖 base_url。")]
    = None,
    output_root: Annotated[Path, typer.Option("--output-root")]
    = Path(".statebreaker/runs"),
) -> None:
    """使用共享运行时顺序重放一次正常工作流。"""

    try:
        workflow = _with_target(load_model(workflow_path, Workflow), target)

        async def invoke() -> tuple[Path, list[Any], dict[str, Any]]:
            async with ExecutionRuntime(workflow, output_root=output_root) as runtime:
                responses = await runtime.execute_workflow()
                return runtime.run_dir, responses, dict(runtime.variables)

        run_dir, responses, variables = _run(invoke())
        write_json(run_dir / "workflow.json", workflow)
        write_json(run_dir / "responses.json", responses)
        write_json(run_dir / "variables.json", variables)
        typer.echo(f"重放完成：{len(responses)} 个响应。")
        typer.echo(f"运行产物：{run_dir.resolve()}")
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@pipeline_app.command("run")
def run_pipeline_command(
    workflow_path: Path,
    invariants_path: Path,
    generator: Annotated[str, typer.Option("--generator")] = DEFAULT_GENERATOR,
    executor: Annotated[str, typer.Option("--executor")] = DEFAULT_EXECUTOR,
    verifier: Annotated[str, typer.Option("--verifier")] = DEFAULT_VERIFIER,
    reporter: Annotated[str | None, typer.Option("--reporter")] = DEFAULT_REPORTER,
    no_report: Annotated[bool, typer.Option("--no-report")] = False,
    attack_type: Annotated[str | None, typer.Option("--attack-type")] = "concurrent-replay",
    plan_id: Annotated[str | None, typer.Option("--plan-id")] = None,
    target: Annotated[str | None, typer.Option("--target", help="临时覆盖 base_url。")]
    = None,
    output_root: Annotated[Path, typer.Option("--output-root")]
    = Path(".statebreaker/runs"),
) -> None:
    """用独立插件完成 Generate → Execute → Verify → Report。"""

    _invoke_pipeline(
        workflow_path,
        invariants_path,
        target=target,
        generator=generator,
        executor=executor,
        verifier=verifier,
        reporter=None if no_report else reporter,
        output_root=output_root,
        plan_id=plan_id,
        attack_type=attack_type,
    )


@app.command()
def demo(
    workflow_path: Annotated[Path, typer.Option("--workflow")] = Path(
        "examples/coupon-race/workflow.yaml"
    ),
    invariants_path: Annotated[Path, typer.Option("--invariants")] = Path(
        "examples/coupon-race/invariants.yaml"
    ),
    target: Annotated[str, typer.Option("--target")] = "http://127.0.0.1:18080",
    output_root: Annotated[Path, typer.Option("--output-root")]
    = Path(".statebreaker/runs"),
    no_report: Annotated[bool, typer.Option("--no-report")] = False,
) -> None:
    """一条命令运行“老王奶茶券”竞态演示。"""

    _invoke_pipeline(
        workflow_path,
        invariants_path,
        target=target,
        generator=DEFAULT_GENERATOR,
        executor=DEFAULT_EXECUTOR,
        verifier=DEFAULT_VERIFIER,
        reporter=None if no_report else DEFAULT_REPORTER,
        output_root=output_root,
        plan_id=None,
        attack_type="concurrent-replay",
    )


@app.command()
def learn(
    workflow_path: Path,
    plugin_id: Annotated[str, typer.Option("--plugin", help="learner 插件 ID")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("learning-result.json"),
) -> None:
    """调用 learner 插件分析正常状态变化。"""

    try:
        workflow = load_model(workflow_path, Workflow)
        plugin = PluginRegistry().get("statebreaker.learner", plugin_id)

        async def invoke() -> Any:
            async with ExecutionRuntime(workflow) as runtime:
                return await plugin.learn(workflow, runtime)

        result = validate_plugin_output(_run(invoke()), LearningResult, plugin_id)
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
    """调用 generator 插件生成攻击计划。"""

    try:
        workflow = load_model(workflow_path, Workflow)
        invariants = load_typed(invariants_path, list[Invariant])
        plugin = PluginRegistry().get("statebreaker.generator", plugin_id)
        plans = validate_plugin_output(
            _run(plugin.generate(workflow, invariants)), list[AttackPlan], plugin_id
        )
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
    """调用 executor 插件执行一份 AttackPlan。"""

    try:
        workflow = load_model(workflow_path, Workflow)
        plan = load_model(plan_path, AttackPlan)
        validate_plan_for_workflow(plan, workflow)
        plugin = PluginRegistry().get("statebreaker.executor", plugin_id)

        async def invoke() -> Any:
            async with ExecutionRuntime(workflow) as runtime:
                return await plugin.execute(plan, runtime)

        result = validate_plugin_output(_run(invoke()), RawAttackResult, plugin_id)
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
    """调用 verifier 插件根据状态证据输出 Finding。"""

    try:
        result = load_model(result_path, RawAttackResult)
        invariants = load_typed(invariants_path, list[Invariant])
        plugin = PluginRegistry().get("statebreaker.verifier", plugin_id)
        findings = validate_plugin_output(
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
    """调用 reporter 插件输出最终报告。"""

    try:
        bundle = load_model(bundle_path, RunBundle)
        plugin = PluginRegistry().get("statebreaker.reporter", plugin_id)
        artifacts = validate_plugin_output(
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
