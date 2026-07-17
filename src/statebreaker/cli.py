"""Stable, non-interactive command-line interface for StateBreaker."""

from __future__ import annotations

import asyncio
import json
import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from statebreaker import __version__
from statebreaker.documents import load_model, load_typed, read_data, write_json
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
    select_attack_plan,
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
invariants_app = typer.Typer(help="查看业务状态规则。")
plans_app = typer.Typer(help="检查并选择生成的攻击计划。")
bundle_app = typer.Typer(help="组装报告插件需要的 RunBundle。")
app.add_typer(plugins_app, name="plugins")
app.add_typer(schema_app, name="schema")
app.add_typer(workflow_app, name="workflow")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(invariants_app, name="invariants")
app.add_typer(plans_app, name="plans")
app.add_typer(bundle_app, name="bundle")

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


def _display_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _show_workflow_steps(workflow: Workflow) -> None:
    typer.echo(f"Workflow: {workflow.name}")
    typer.echo(f"Target: {workflow.base_url}")
    typer.echo(f"Steps: {len(workflow.steps)}")
    for index, step in enumerate(workflow.steps, start=1):
        dependencies = ", ".join(step.depends_on) or "none"
        typer.echo(
            f"  {index}. {str(step.role).upper():<6} "
            f"{step.request.method:<4} {step.request.path}"
        )
        typer.echo(
            f"     id={step.id} session={step.session} depends_on={dependencies}"
        )
        if step.extract:
            extracted = ", ".join(
                f"{item.name} <- {item.kind}:{item.expression}" for item in step.extract
            )
            typer.echo(f"     extract: {extracted}")


def _show_invariants(invariants: list[Invariant]) -> None:
    typer.echo(f"Business invariants: {len(invariants)}")
    for invariant in invariants:
        typer.echo(
            f"  - {invariant.id}: kind={invariant.kind} selector={invariant.selector}"
        )
        typer.echo(
            f"    probes={invariant.before_probe or '-'} -> "
            f"{invariant.after_probe or '-'} "
            f"parameters={_display_value(invariant.parameters)}"
        )


def _show_plan(plan: AttackPlan) -> None:
    typer.echo(f"Plan: {plan.id}")
    typer.echo(f"  workflow: {plan.workflow_name}")
    typer.echo(f"  attack_type: {plan.attack_type}")
    typer.echo(f"  target_steps: {', '.join(plan.target_steps)}")
    typer.echo(f"  invariant_ids: {', '.join(plan.invariant_ids) or '-'}")
    typer.echo(
        f"  schedule: concurrency={plan.schedule.concurrency}, "
        f"offsets_ms={plan.schedule.offsets_ms}, "
        f"options={_display_value(plan.schedule.options)}"
    )


def _show_plan_list(plans: list[AttackPlan]) -> None:
    typer.echo(f"Generated attack plans: {len(plans)}")
    typer.echo("INDEX  ATTACK TYPE                  C  TARGET                PLAN ID")
    for index, plan in enumerate(sorted(plans, key=lambda item: item.id), start=1):
        typer.echo(
            f"{index:>5}  {plan.attack_type:<27} "
            f"{plan.schedule.concurrency:>2}  "
            f"{','.join(plan.target_steps):<20} {plan.id}"
        )


def _show_attack_result(plan: AttackPlan, result: RawAttackResult) -> None:
    typer.echo("Attack execution")
    _show_plan(plan)
    target_steps = set(plan.target_steps)
    target_events = [
        event
        for event in result.events
        if event.step_id in target_steps
        and event.kind in {"request.started", "request.completed", "request.failed"}
    ]
    typer.echo("  request timeline:")
    if target_events:
        origin = min(event.monotonic_ns for event in target_events)
        for event in target_events:
            relative_ms = (event.monotonic_ns - origin) / 1_000_000
            ordinal = (event.request_ordinal or 0) + 1
            if event.kind == "request.started":
                request = event.request or {}
                typer.echo(
                    f"    +{relative_ms:8.3f} ms SEND #{ordinal} "
                    f"{request.get('method', '?')} {request.get('path', '?')}"
                )
            elif event.kind == "request.completed":
                response = event.response or {}
                typer.echo(
                    f"    +{relative_ms:8.3f} ms DONE #{ordinal} "
                    f"HTTP {response.get('status_code', '?')} "
                    f"elapsed={float(response.get('elapsed_ms', 0)):.3f} ms"
                )
            else:
                typer.echo(
                    f"    +{relative_ms:8.3f} ms FAIL #{ordinal} {event.message}"
                )
    else:
        typer.echo("    no target-step timing events recorded")

    evidence = result.plugin_data
    if "checked_events" in evidence:
        typer.echo(
            "  server evidence: "
            f"checks={evidence.get('checked_events', 0)}, "
            f"commits={evidence.get('committed_events', 0)}, "
            f"rejections={evidence.get('rejected_events', 0)}"
        )
    typer.echo("  state changes:")
    changed_fields = sorted(
        key
        for key in set(result.before_state) | set(result.after_state)
        if result.before_state.get(key) != result.after_state.get(key)
    )
    for key in changed_fields:
        before = result.before_state.get(key)
        after = result.after_state.get(key)
        delta = ""
        if (
            isinstance(before, (int, float))
            and not isinstance(before, bool)
            and isinstance(after, (int, float))
            and not isinstance(after, bool)
        ):
            delta = f" (delta={after - before:+g})"
        typer.echo(
            f"    {key}: {_display_value(before)} -> {_display_value(after)}{delta}"
        )


def _show_findings(findings: list[Finding]) -> None:
    typer.echo(f"Findings: {len(findings)}")
    for finding in findings:
        details = finding.details
        typer.echo(
            f"  {str(finding.verdict).upper()}: {finding.id} "
            f"(rule={finding.invariant_id or '-'})"
        )
        if "observed_delta" in details:
            maximum = details.get("parameters", {}).get("max_delta")
            typer.echo(
                f"    observed_delta={details['observed_delta']} allowed_max={maximum}"
            )
        typer.echo(f"    {finding.title}")


def _show_detailed_pipeline(
    workflow: Workflow,
    invariants: list[Invariant],
    outcome: PipelineOutcome,
    plugins: PipelinePlugins,
) -> None:
    """Print the concrete data flow teachers and plugin authors need to inspect."""

    typer.echo("\n=== StateBreaker concrete execution flow ===")
    typer.echo(f"Target: {workflow.base_url}")

    typer.echo(f"\n[1/6] Normal workflow ({len(workflow.steps)} HTTP steps)")
    _show_workflow_steps(workflow)

    typer.echo(f"\n[2/6] Business invariants ({len(invariants)} rules)")
    _show_invariants(invariants)

    type_counts = Counter(plan.attack_type for plan in outcome.plans)
    generated_types = ", ".join(
        f"{attack_type} x{count}" for attack_type, count in sorted(type_counts.items())
    )
    plan = outcome.selected_plan
    typer.echo("\n[3/6] Attack-plan generation")
    typer.echo(f"  generator: {plugins.generator}")
    typer.echo(f"  candidates: {len(outcome.plans)} ({generated_types})")
    typer.echo(f"  selected: {plan.id}")
    typer.echo(f"  attack_type: {plan.attack_type}")
    typer.echo(f"  target_steps: {', '.join(plan.target_steps)}")
    typer.echo(
        f"  schedule: concurrency={plan.schedule.concurrency}, "
        f"offsets_ms={plan.schedule.offsets_ms}, "
        f"options={_display_value(plan.schedule.options)}"
    )

    typer.echo("\n[4/6] Concrete attack execution timeline")
    typer.echo(f"  executor: {plugins.executor}")
    _show_attack_result(plan, outcome.result)

    typer.echo("\n[5/6] State comparison and verdict")
    changed_fields = sorted(
        key
        for key in set(outcome.result.before_state) | set(outcome.result.after_state)
        if outcome.result.before_state.get(key) != outcome.result.after_state.get(key)
    )
    for key in changed_fields:
        before = outcome.result.before_state.get(key)
        after = outcome.result.after_state.get(key)
        delta = ""
        if (
            isinstance(before, (int, float))
            and not isinstance(before, bool)
            and isinstance(after, (int, float))
            and not isinstance(after, bool)
        ):
            delta = f" (delta={after - before:+g})"
        typer.echo(
            f"  {key}: {_display_value(before)} -> {_display_value(after)}{delta}"
        )
    for finding in outcome.findings:
        details = finding.details
        comparison = ""
        if "observed_delta" in details:
            maximum = details.get("parameters", {}).get("max_delta")
            comparison = (
                f"; observed_delta={details['observed_delta']}, allowed_max={maximum}"
            )
        typer.echo(
            f"  VERDICT={str(finding.verdict).upper()}  rule={finding.invariant_id or '-'}"
            f"{comparison}"
        )
        typer.echo(f"  reason: {finding.title}")

    typer.echo("\n[6/6] Reproducible artifacts")
    typer.echo(f"  run directory: {outcome.run_dir.resolve()}")
    typer.echo(f"  machine summary: {(outcome.run_dir / 'summary.json').resolve()}")
    typer.echo(f"  full evidence: {(outcome.run_dir / 'run-bundle.json').resolve()}")
    if outcome.report_artifacts:
        for artifact in outcome.report_artifacts.files:
            typer.echo(f"  report: {artifact}")
    else:
        typer.echo("  report: skipped")


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
    verbose: bool,
) -> None:
    try:
        workflow = _with_target(load_model(workflow_path, Workflow), target)
        invariants = load_typed(invariants_path, list[Invariant])
        if not verbose:
            typer.echo(f"[1/4] 工作流有效：{workflow.name}，目标 {workflow.base_url}")
        else:
            typer.echo(
                f"Running {workflow.name} with {generator} -> {executor} -> "
                f"{verifier}{f' -> {reporter}' if reporter else ''} ..."
            )
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
        if verbose:
            _show_detailed_pipeline(
                workflow,
                invariants,
                outcome,
                _plugins(generator, executor, verifier, reporter),
            )
        else:
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


@workflow_app.command("show")
def show_workflow(
    path: Path,
    target: Annotated[str | None, typer.Option("--target", help="临时覆盖 base_url。")]
    = None,
) -> None:
    """展开将要执行的真实 HTTP 步骤、会话、变量和依赖。"""

    try:
        workflow = _with_target(load_model(path, Workflow), target)
        _show_workflow_steps(workflow)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)


@invariants_app.command("show")
def show_invariant_file(path: Path) -> None:
    """展示攻击结果必须满足的业务状态规则。"""

    try:
        invariants = load_typed(path, list[Invariant])
        _show_invariants(invariants)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)


@workflow_app.command("import")
def import_workflow(
    source: Path,
    plugin_id: Annotated[str, typer.Option("--plugin", help="capture 插件 ID")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("workflow.json"),
    options_path: Annotated[
        Path | None,
        typer.Option("--options", help="传给 capture 插件的 JSON/YAML 配置。"),
    ] = None,
) -> None:
    """调用 capture 插件生成统一 Workflow。"""

    try:
        options: dict[str, Any] = {}
        if options_path is not None:
            raw_options = read_data(options_path)
            if not isinstance(raw_options, dict):
                raise DocumentError("capture options must be a JSON/YAML object")
            options = raw_options
        plugin = PluginRegistry().get("statebreaker.capture", plugin_id)
        workflow = validate_plugin_output(
            _run(plugin.capture(source, options)), Workflow, plugin_id
        )
        _write_output(output, workflow)
        _show_workflow_steps(workflow)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
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
        typer.echo("Normal workflow replay")
        _show_workflow_steps(workflow)
        typer.echo("Responses:")
        for index, response in enumerate(responses, start=1):
            typer.echo(
                f"  {index}. step={response.step_id} HTTP {response.status_code} "
                f"elapsed={response.elapsed_ms:.3f} ms"
            )
            if response.step_id in workflow.state_probe_steps:
                try:
                    snapshot = json.loads(response.body_preview)
                except json.JSONDecodeError:
                    snapshot = response.body_preview
                typer.echo(f"     state snapshot: {_display_value(snapshot)}")
        typer.echo(f"Extracted variables: {_display_value(variables)}")
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
    verbose: Annotated[
        bool, typer.Option("--verbose/--quiet", help="展开或精简具体执行流程。")
    ] = True,
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
        verbose=verbose,
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
        _show_plan_list(plans)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@plans_app.command("list")
def list_attack_plans(path: Path) -> None:
    """列出 generator 产生的全部候选策略，不发送请求。"""

    try:
        plans = load_typed(path, list[AttackPlan])
        _show_plan_list(plans)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)


@plans_app.command("select")
def select_plan_command(
    path: Path,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        "selected-plan.json"
    ),
    plan_id: Annotated[str | None, typer.Option("--plan-id")] = None,
    attack_type: Annotated[str | None, typer.Option("--attack-type")] = None,
) -> None:
    """从候选列表中确定一份即将真实执行的 AttackPlan。"""

    try:
        plans = load_typed(path, list[AttackPlan])
        if plan_id is None and attack_type is None:
            raise DocumentError("provide --plan-id or --attack-type to select an attack plan")
        selected = select_attack_plan(
            plans,
            plan_id=plan_id,
            attack_type=attack_type,
        )
        _write_output(output, selected)
        _show_plan(selected)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)


@app.command()
def attack(
    plan_path: Path,
    workflow_path: Annotated[Path, typer.Option("--workflow", help="对应 Workflow 文件")],
    plugin_id: Annotated[str, typer.Option("--plugin", help="executor 插件 ID")],
    target: Annotated[str | None, typer.Option("--target", help="临时覆盖 base_url。")]
    = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        "raw-attack-result.json"
    ),
    verbose: Annotated[
        bool, typer.Option("--verbose/--quiet", help="显示并发时间线和状态差分。")
    ] = True,
) -> None:
    """调用 executor 插件执行一份 AttackPlan。"""

    try:
        workflow = _with_target(load_model(workflow_path, Workflow), target)
        plan = load_model(plan_path, AttackPlan)
        validate_plan_for_workflow(plan, workflow)
        plugin = PluginRegistry().get("statebreaker.executor", plugin_id)

        async def invoke() -> Any:
            async with ExecutionRuntime(workflow) as runtime:
                return await plugin.execute(plan, runtime)

        result = validate_plugin_output(_run(invoke()), RawAttackResult, plugin_id)
        _write_output(output, result)
        if verbose:
            _show_attack_result(plan, result)
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
        _show_findings(findings)
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)
    except PluginError as exc:
        _abort(str(exc), EXIT_PLUGIN)
    except (TypeError, ValueError) as exc:
        _abort_plugin_contract(exc, plugin_id)
    except (StateBreakerError, OSError) as exc:
        _abort(str(exc), EXIT_RUNTIME)


@bundle_app.command("build")
def build_bundle(
    workflow_path: Annotated[Path, typer.Option("--workflow")],
    plan_path: Annotated[Path, typer.Option("--plan")],
    result_path: Annotated[Path, typer.Option("--result")],
    findings_path: Annotated[Path, typer.Option("--findings")],
    target: Annotated[str | None, typer.Option("--target", help="临时覆盖 base_url。")]
    = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("run-bundle.json"),
) -> None:
    """把分阶段产物组装为 reporter 的标准输入。"""

    try:
        workflow = _with_target(load_model(workflow_path, Workflow), target)
        plan = load_model(plan_path, AttackPlan)
        result = load_model(result_path, RawAttackResult)
        findings = load_typed(findings_path, list[Finding])
        validate_plan_for_workflow(plan, workflow)
        if result.attack_plan_id != plan.id:
            raise DocumentError(
                f"result targets plan {result.attack_plan_id!r}, selected plan is {plan.id!r}"
            )
        bundle = RunBundle(
            workflow=workflow,
            attack_plan=plan,
            result=result,
            findings=findings,
        )
        _write_output(output, bundle)
        typer.echo(
            f"Bundle ready: workflow={workflow.name}, plan={plan.id}, "
            f"findings={len(findings)}"
        )
    except DocumentError as exc:
        _abort(str(exc), EXIT_VALIDATION)


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
        for artifact in artifacts.files:
            typer.echo(f"报告文件：{artifact}")
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
