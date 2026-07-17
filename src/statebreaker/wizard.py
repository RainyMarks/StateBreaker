"""Interactive demo wizard with a Rich-powered UI.

Shows each command, runs it on demand, prints styled output, then offers the next step.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from pydantic import TypeAdapter
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from statebreaker.documents import load_model, write_json
from statebreaker.models import (
    AttackPlan,
    Finding,
    RawAttackResult,
    RunBundle,
    Workflow,
)

PYTHON = sys.executable
STATEBREAKER = [PYTHON, "-m", "statebreaker"]

THEME = Theme(
    {
        "ok": "bold green",
        "fail": "bold red",
        "warn": "bold yellow",
        "muted": "dim",
        "accent": "bold cyan",
        "title": "bold white",
        "cmd": "bold magenta",
    }
)
console = Console(theme=THEME)


@dataclass
class WizardState:
    root: Path
    work_dir: Path
    workflow_path: Path
    invariants_path: Path
    lab_base_url: str = "http://127.0.0.1:8080"
    plans_path: Path | None = None
    selected_plan_path: Path | None = None
    raw_result_path: Path | None = None
    findings_path: Path | None = None
    learning_path: Path | None = None
    report_dir: Path | None = None
    history: list[str] = field(default_factory=list)


def _banner() -> None:
    console.print()
    console.print(
        Panel(
            Align.center(
                Group(
                    Text("StateBreaker", style="bold cyan"),
                    Text("Interactive Demo Wizard", style="title"),
                    Text("Uncle Wang's milk-tea lab · BUG50 race", style="muted"),
                    Text("show command → confirm → run → inspect → next", style="muted"),
                )
            ),
            border_style="cyan",
            box=box.DOUBLE,
            padding=(1, 2),
        )
    )
    console.print()


def _status_bar(state: WizardState) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="muted")
    table.add_column()
    table.add_row("Lab", f"[accent]{state.lab_base_url}[/]")
    table.add_row("Work dir", f"[accent]{state.work_dir}[/]")
    table.add_row("Workflow", str(state.workflow_path.name))
    table.add_row("Invariants", str(state.invariants_path.name))
    console.print(Panel(table, title="[title]Session[/]", border_style="blue", box=box.ROUNDED))


def _step_header(title: str, index: str | None = None) -> None:
    label = f"{index}  {title}" if index else title
    console.print()
    console.print(Rule(f"[title]{label}[/]", style="cyan"))


def _info(message: str) -> None:
    console.print(f"  [accent]i[/]  {message}")


def _ok(message: str) -> None:
    console.print(f"  [ok]OK[/]  {message}")


def _fail(message: str) -> None:
    console.print(f"  [fail]X[/]  {message}")


def _warn(message: str) -> None:
    console.print(f"  [warn]![/]  {message}")


def _choose(prompt: str, options: list[tuple[str, str]], *, default: str) -> str:
    table = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 1))
    table.add_column("Key", style="bold cyan", width=4)
    table.add_column("Action")
    keys = {key.lower() for key, _ in options}
    for key, label in options:
        suffix = " [muted](default)[/]" if key.lower() == default.lower() else ""
        table.add_row(f"[{key}]", f"{label}{suffix}")
    console.print(
        Panel(
            table,
            title=f"[title]{prompt}[/]",
            border_style="magenta",
            box=box.ROUNDED,
        )
    )
    while True:
        answer = Prompt.ask("[accent]Choice[/]", default=default).strip().lower()
        if answer in keys:
            return answer
        _warn(f"Invalid input. Choose one of: {', '.join(sorted(keys))}")


def _run_command(
    state: WizardState,
    *,
    title: str,
    argv: list[str],
    explain: str,
) -> tuple[int, str]:
    cmd_display = subprocess.list2cmdline(argv)
    body = Group(
        Text(explain, style="white"),
        Text(),
        Text("Working directory", style="muted"),
        Text(str(state.root), style="accent"),
        Text(),
        Text("Command", style="muted"),
        Syntax(cmd_display, "bash", theme="monokai", word_wrap=True, padding=1),
    )
    console.print(
        Panel(
            body,
            title=f"[cmd]{title}[/]",
            border_style="magenta",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )

    action = _choose(
        "What next?",
        [
            ("r", "Run this command"),
            ("s", "Skip this step"),
            ("q", "Quit wizard"),
        ],
        default="r",
    )
    if action == "q":
        console.print("[warn]Wizard stopped by user.[/]")
        raise SystemExit(0)
    if action == "s":
        _warn("Skipped.")
        state.history.append(f"SKIP {title}")
        return -1, ""

    with console.status("[accent]Running command…[/]", spinner="dots"):
        completed = subprocess.run(
            argv,
            cwd=state.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    combined = (completed.stdout or "") + (completed.stderr or "")
    if completed.stdout and completed.stdout.strip():
        console.print(
            Panel(
                completed.stdout.rstrip(),
                title="[title]stdout[/]",
                border_style="green",
                box=box.ROUNDED,
            )
        )
    if completed.stderr and completed.stderr.strip():
        console.print(
            Panel(
                completed.stderr.rstrip(),
                title="[title]stderr[/]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    if completed.returncode == 0:
        _ok(f"Succeeded  (exit={completed.returncode})")
        state.history.append(f"OK   {title}")
    else:
        _fail(f"Failed  (exit={completed.returncode})")
        state.history.append(f"FAIL {title} exit={completed.returncode}")
    return completed.returncode, combined


def _probe_lab(ports: list[int] | None = None) -> str | None:
    ports = ports or [8080, 18080, 8000]
    for port in ports:
        url = f"http://127.0.0.1:{port}/healthz"
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - local lab only
                body = response.read().decode("utf-8", errors="replace")
            if "coupon-race" in body:
                return f"http://127.0.0.1:{port}"
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
    return None


def _ensure_workflow_for_lab(state: WizardState) -> None:
    source = state.root / "examples" / "coupon-race" / "workflow.yaml"
    workflow = load_model(source, Workflow)
    data = workflow.model_dump(mode="json")
    data["base_url"] = state.lab_base_url
    demo_wf = state.work_dir / "workflow.json"
    write_json(demo_wf, Workflow.model_validate(data))
    state.workflow_path = demo_wf


def _default_paths(root: Path) -> WizardState:
    work = root / ".statebreaker" / "wizard"
    work.mkdir(parents=True, exist_ok=True)
    return WizardState(
        root=root,
        work_dir=work,
        workflow_path=root / "examples" / "coupon-race" / "workflow.yaml",
        invariants_path=root / "examples" / "coupon-race" / "invariants.yaml",
    )


def step_check_env(state: WizardState) -> None:
    plugins = [
        ("statebreaker.learner", "team.delta-learner"),
        ("statebreaker.generator", "team.race-generator"),
        ("statebreaker.executor", "team.race-executor"),
        ("statebreaker.verifier", "team.basic-verifier"),
        ("statebreaker.reporter", "team.pdf-reporter"),
    ]
    _run_command(
        state,
        title="Environment · doctor",
        argv=[*STATEBREAKER, "doctor"],
        explain="Verify Python / core API versions.",
    )
    _run_command(
        state,
        title="Environment · plugins list",
        argv=[*STATEBREAKER, "plugins", "list"],
        explain="Expect learner / generator / executor / verifier / reporter.",
    )

    table = Table(title="Demo plugin checklist", box=box.ROUNDED, border_style="cyan")
    table.add_column("Group", style="muted")
    table.add_column("Plugin ID", style="accent")
    table.add_column("Status")
    missing: list[str] = []
    try:
        from statebreaker.plugins import PluginRegistry

        registry = PluginRegistry()
        for group, plugin_id in plugins:
            try:
                registry.get(group, plugin_id)
                table.add_row(group, plugin_id, "[ok]ready[/]")
            except Exception:  # noqa: BLE001
                table.add_row(group, plugin_id, "[fail]missing[/]")
                missing.append(f"{group} / {plugin_id}")
    except Exception as exc:  # noqa: BLE001
        _fail(f"Plugin scan error: {exc}")
        return
    console.print(table)
    if missing:
        _warn("Install missing plugins from the repo root:")
        console.print(
            Markdown(
                """
```bash
python -m pip install -e ".[dev]"
python -m pip install -e ./race-generator ./race-executor
python -m pip install -e ./statebreaker-learner-delta
python -m pip install -e ./statebreaker-verifier-basic
python -m pip install -e ./statebreaker-reporter-pdf
```
"""
            )
        )
    else:
        _ok("All demo plugins are loaded.")


def step_check_lab(state: WizardState) -> None:
    console.print(
        Panel(
            Markdown(
                """
If the lab is **not** running yet, open another terminal:

```powershell
docker compose up --build
```

If port **8080** is busy:

```powershell
$env:STATEBREAKER_LAB_PORT = "18080"
docker compose up --build
```
"""
            ),
            title="[title]Lab · Uncle Wang's milk-tea shop[/]",
            border_style="yellow",
            box=box.ROUNDED,
        )
    )
    with console.status("[accent]Probing lab ports 8080 / 18080 / 8000…[/]", spinner="dots"):
        found = _probe_lab()
    if found:
        state.lab_base_url = found
        _ok(f"Detected lab at {found}/healthz")
    else:
        _warn("Lab not detected automatically.")
        custom = Prompt.ask("Enter base_url manually", default=state.lab_base_url)
        state.lab_base_url = custom.rstrip("/")
    _ensure_workflow_for_lab(state)
    _info(f"Demo workflow → {state.workflow_path}")
    _info(f"base_url = {state.lab_base_url}")
    try:
        with urlopen(f"{state.lab_base_url}/healthz", timeout=3) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
        _ok(f"healthz → {body.strip()}")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        _warn(f"healthz unreachable: {exc}")
        _warn("learn/attack will fail until Docker lab is up.")


def step_validate_workflow(state: WizardState) -> None:
    _run_command(
        state,
        title="Validate workflow",
        argv=[*STATEBREAKER, "workflow", "validate", str(state.workflow_path)],
        explain="No network I/O — checks step graph and template variables only.",
    )


def step_learn(state: WizardState) -> None:
    out = state.work_dir / "learning-result.json"
    code, _ = _run_command(
        state,
        title="Learn normal state (learner)",
        argv=[
            *STATEBREAKER,
            "learn",
            str(state.workflow_path),
            "--plugin",
            "team.delta-learner",
            "--output",
            str(out),
        ],
        explain=(
            "Replay honest redemptions and propose candidate invariants "
            "(lab required). Safe to skip."
        ),
    )
    if code == 0 and out.is_file():
        state.learning_path = out
        data = json.loads(out.read_text(encoding="utf-8"))
        inv_path = state.work_dir / "learned-invariants.json"
        invs = data.get("invariants", [])
        inv_path.write_text(
            json.dumps(invs, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        table = Table(title="Learned invariants", box=box.ROUNDED, border_style="green")
        table.add_column("ID", style="accent")
        table.add_column("Kind")
        table.add_column("Selector", style="muted")
        for item in invs[:12]:
            table.add_row(
                str(item.get("id", "")),
                str(item.get("kind", "")),
                str(item.get("selector", "")),
            )
        if invs:
            console.print(table)
        _info(f"Learned {len(invs)} candidate(s).")
        if invs and Confirm.ask(
            "Use learned invariants instead of examples?", default=False
        ):
            state.invariants_path = inv_path
            _ok(f"invariants = {inv_path.name}")


def step_generate(state: WizardState) -> None:
    out = state.work_dir / "plans.json"
    code, _ = _run_command(
        state,
        title="Generate attack plans (generator)",
        argv=[
            *STATEBREAKER,
            "generate",
            str(state.workflow_path),
            str(state.invariants_path),
            "--plugin",
            "team.race-generator",
            "--output",
            str(out),
        ],
        explain="Build a bounded race AttackPlan list from workflow + invariants.",
    )
    if code == 0 and out.is_file():
        state.plans_path = out
        plans = json.loads(out.read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for plan in plans:
            kind = str(plan.get("attack_type", "?"))
            counts[kind] = counts.get(kind, 0) + 1
        table = Table(title=f"{len(plans)} plan(s) generated", box=box.ROUNDED)
        table.add_column("Attack type", style="accent")
        table.add_column("Count", justify="right")
        for kind, count in sorted(counts.items()):
            table.add_row(kind, str(count))
        console.print(table)


def step_select_and_attack(state: WizardState) -> None:
    if state.plans_path is None or not state.plans_path.is_file():
        example = state.root / "examples" / "coupon-race" / "attack-plan.yaml"
        _warn(f"No plans.json — using example plan: {example.name}")
        plan_path = state.work_dir / "one-plan.json"
        write_json(plan_path, load_model(example, AttackPlan))
        state.selected_plan_path = plan_path
    else:
        plans = TypeAdapter(list[AttackPlan]).validate_python(
            json.loads(state.plans_path.read_text(encoding="utf-8"))
        )
        table = Table(title="Select an attack plan", box=box.ROUNDED, border_style="cyan")
        table.add_column("#", style="bold cyan", justify="right")
        table.add_column("Type", style="accent")
        table.add_column("Plan ID")
        for index, plan in enumerate(plans, start=1):
            table.add_row(str(index), plan.attack_type, plan.id)
        console.print(table)
        default_idx = next(
            (i for i, p in enumerate(plans, start=1) if p.attack_type == "concurrent-replay"),
            1,
        )
        while True:
            choice = IntPrompt.ask("Plan number", default=default_idx)
            if 1 <= choice <= len(plans):
                selected = plans[choice - 1]
                break
            _warn("Enter a valid number from the table.")
        plan_path = state.work_dir / "one-plan.json"
        write_json(plan_path, selected)
        state.selected_plan_path = plan_path
        _ok(f"Selected [accent]{selected.id}[/]")

    assert state.selected_plan_path is not None
    out = state.work_dir / "raw-result.json"
    code, _ = _run_command(
        state,
        title="Execute attack (executor)",
        argv=[
            *STATEBREAKER,
            "attack",
            str(state.selected_plan_path),
            "--workflow",
            str(state.workflow_path),
            "--plugin",
            "team.race-executor",
            "--output",
            str(out),
        ],
        explain="Send planned requests to the lab and collect before/after state evidence.",
    )
    if code == 0 and out.is_file():
        state.raw_result_path = out
        result = load_model(out, RawAttackResult)
        table = Table(title="Attack result summary", box=box.ROUNDED, border_style="green")
        table.add_column("Field", style="muted")
        table.add_column("Value")
        table.add_row("before_state", json.dumps(result.before_state, ensure_ascii=False))
        table.add_row("after_state", json.dumps(result.after_state, ensure_ascii=False))
        table.add_row(
            "vulnerability_observed",
            str(result.plugin_data.get("vulnerability_observed")),
        )
        table.add_row(
            "status codes",
            str([record.status_code for record in result.responses]),
        )
        console.print(table)


def step_verify(state: WizardState) -> None:
    if state.raw_result_path is None or not state.raw_result_path.is_file():
        _warn("No raw-result — run the attack step first.")
        return
    out = state.work_dir / "findings.json"
    code, _ = _run_command(
        state,
        title="Verify invariants (verifier)",
        argv=[
            *STATEBREAKER,
            "verify",
            str(state.raw_result_path),
            str(state.invariants_path),
            "--plugin",
            "team.basic-verifier",
            "--output",
            str(out),
        ],
        explain="Compare state evidence to invariants → confirmed / probable / rejected.",
    )
    if code == 0 and out.is_file():
        state.findings_path = out
        findings = TypeAdapter(list[Finding]).validate_python(
            json.loads(out.read_text(encoding="utf-8"))
        )
        table = Table(title="Findings", box=box.ROUNDED, border_style="red")
        table.add_column("Verdict")
        table.add_column("ID", style="accent")
        table.add_column("Title")
        for finding in findings:
            style = {
                "confirmed": "ok",
                "probable": "warn",
                "rejected": "muted",
            }.get(str(finding.verdict), "white")
            table.add_row(
                f"[{style}]{finding.verdict}[/]",
                finding.id,
                finding.title,
            )
        console.print(table)


def step_report(state: WizardState) -> None:
    if state.raw_result_path is None or state.selected_plan_path is None:
        _warn("Missing attack artifacts — cannot build report.")
        return
    if state.findings_path is None or not state.findings_path.is_file():
        _warn("No findings yet — report will use an empty findings list.")
        findings: list[Finding] = []
    else:
        findings = TypeAdapter(list[Finding]).validate_python(
            json.loads(state.findings_path.read_text(encoding="utf-8"))
        )

    bundle_path = state.work_dir / "run-bundle.json"
    bundle = RunBundle(
        workflow=load_model(state.workflow_path, Workflow),
        attack_plan=load_model(state.selected_plan_path, AttackPlan),
        result=load_model(state.raw_result_path, RawAttackResult),
        findings=findings,
    )
    write_json(bundle_path, bundle)
    _ok(f"RunBundle → {bundle_path.name}")

    report_dir = state.work_dir / "report"
    code, _ = _run_command(
        state,
        title="Render PDF report (reporter)",
        argv=[
            *STATEBREAKER,
            "report",
            str(bundle_path),
            "--plugin",
            "team.pdf-reporter",
            "--output-dir",
            str(report_dir),
        ],
        explain="Render the full RunBundle to statebreaker-report.pdf.",
    )
    if code == 0:
        state.report_dir = report_dir
        pdf = report_dir / "statebreaker-report.pdf"
        _ok(f"PDF → {pdf.resolve()}")
        if pdf.is_file() and Confirm.ask("Open PDF with the system default app?", default=False):
            _open_path(pdf)


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            import os

            os.startfile(str(path))  # noqa: S606 - open local report file
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.run([opener, str(path)], check=False)
    except OSError as exc:
        _fail(f"Could not open file: {exc}")


def step_show_artifacts(state: WizardState) -> None:
    files = Table(title="Artifacts", box=box.ROUNDED, border_style="blue")
    files.add_column("Path", style="accent")
    files.add_column("Size", justify="right")
    for path in sorted(state.work_dir.rglob("*")):
        if path.is_file():
            files.add_row(str(path.relative_to(state.root)), f"{path.stat().st_size} B")
    console.print(files)

    hist = Table(title="Step history", box=box.SIMPLE)
    hist.add_column("Event")
    for item in state.history:
        style = "ok" if item.startswith("OK") else "warn" if item.startswith("SKIP") else "fail"
        hist.add_row(f"[{style}]{item}[/]")
    console.print(hist)


def run_guided(state: WizardState) -> None:
    steps: list[tuple[str, str, Callable[[WizardState], None]]] = [
        ("1/8", "Environment check", step_check_env),
        ("2/8", "Detect lab", step_check_lab),
        ("3/8", "Validate workflow", step_validate_workflow),
        ("4/8", "Learn rules (optional)", step_learn),
        ("5/8", "Generate attack plans", step_generate),
        ("6/8", "Select plan & attack", step_select_and_attack),
        ("7/8", "Verify findings", step_verify),
        ("8/8", "Generate PDF report", step_report),
    ]
    for index, title, fn in steps:
        _step_header(title, index)
        _status_bar(state)
        cont = _choose(
            f"Enter step: {title}?",
            [
                ("y", "Enter this step"),
                ("s", "Skip entire step"),
                ("q", "End wizard"),
            ],
            default="y",
        )
        if cont == "q":
            break
        if cont == "s":
            state.history.append(f"SKIP-STEP {index} {title}")
            _warn(f"Skipped {title}")
            continue
        fn(state)
    _step_header("Summary")
    step_show_artifacts(state)
    console.print(
        Panel(
            f"[ok]Demo finished.[/]\nArtifacts: [accent]{state.work_dir.resolve()}[/]",
            border_style="green",
            box=box.DOUBLE,
        )
    )


def run_menu(state: WizardState) -> None:
    actions: dict[str, tuple[str, Callable[[WizardState], None] | None]] = {
        "1": ("Environment check", step_check_env),
        "2": ("Detect lab / align workflow", step_check_lab),
        "3": ("Validate workflow", step_validate_workflow),
        "4": ("Learn (learner)", step_learn),
        "5": ("Generate plans (generator)", step_generate),
        "6": ("Attack (executor)", step_select_and_attack),
        "7": ("Verify (verifier)", step_verify),
        "8": ("PDF report (reporter)", step_report),
        "9": ("Show artifacts & history", step_show_artifacts),
        "g": ("Guided full pipeline", run_guided),
        "q": ("Quit", None),
    }
    while True:
        _status_bar(state)
        choice = _choose(
            "Main menu",
            [(key, label) for key, (label, _) in actions.items()],
            default="g",
        )
        label, fn = actions[choice]
        if fn is None:
            console.print("[muted]Bye.[/]")
            return
        if choice == "g":
            fn(state)
            return
        _step_header(label)
        fn(state)


def main_wizard(*, root: Path | None = None, guided: bool = False) -> None:
    root_path = (root or Path.cwd()).resolve()
    if not (root_path / "examples" / "coupon-race").is_dir():
        console.print(
            "[fail]Error:[/] run from the StateBreaker repo root "
            "(needs [accent]examples/coupon-race[/])."
        )
        raise SystemExit(2)
    _banner()
    state = _default_paths(root_path)
    console.print(f"[muted]Repo root:[/]  [accent]{root_path}[/]")
    console.print(f"[muted]Artifacts:[/]  [accent]{state.work_dir}[/]")
    console.print()
    if guided:
        run_guided(state)
        return
    mode = _choose(
        "Select mode",
        [
            ("g", "Guided mode — walk the full pipeline in order (recommended)"),
            ("m", "Menu mode — pick each step freely"),
        ],
        default="g",
    )
    if mode == "g":
        run_guided(state)
    else:
        run_menu(state)
