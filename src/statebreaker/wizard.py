"""Interactive English demo wizard for the coupon-race pipeline.

Shows each command, runs it on demand, prints output, then offers the next step.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from pydantic import TypeAdapter

from statebreaker.documents import load_model, write_json
from statebreaker.models import (
    AttackPlan,
    Finding,
    RawAttackResult,
    RunBundle,
    Workflow,
)

# Prefer the same interpreter that launched the wizard (editable installs).
PYTHON = sys.executable
STATEBREAKER = [PYTHON, "-m", "statebreaker"]


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
    print()
    print("=" * 64)
    print("  StateBreaker Interactive Demo Wizard")
    print("  Uncle Wang's milk-tea lab (BUG50)")
    print("  Step-by-step · show commands · confirm · skip · next")
    print("=" * 64)
    print()


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default or ""
    return raw if raw else (default or "")


def _choose(prompt: str, options: list[tuple[str, str]], *, default: str) -> str:
    """options: list of (key, label). Returns chosen key."""

    print(prompt)
    keys = {key.lower() for key, _ in options}
    for key, label in options:
        mark = " (default)" if key.lower() == default.lower() else ""
        print(f"  [{key}] {label}{mark}")
    while True:
        answer = _ask("Choice", default).lower()
        if answer in keys:
            return answer
        print(f"  Invalid input. Choose one of: {sorted(keys)}")


def _print_panel(title: str, body: str) -> None:
    print()
    print(f"-- {title} " + "-" * max(0, 50 - len(title)))
    for line in body.splitlines() or [""]:
        print(f"  {line}")
    print("-" * 56)


def _run_command(
    state: WizardState,
    *,
    title: str,
    argv: list[str],
    explain: str,
) -> tuple[int, str]:
    """Show command, ask to run, execute, print output. Returns (exit_code, combined output)."""

    cmd_display = subprocess.list2cmdline(argv)
    _print_panel(
        title,
        textwrap.dedent(
            f"""\
            What: {explain}
            CWD:  {state.root}
            Command:
              {cmd_display}
            """
        ).strip(),
    )
    action = _choose(
        "Next action?",
        [
            ("r", "Run this command"),
            ("s", "Skip this step"),
            ("q", "Quit wizard"),
        ],
        default="r",
    )
    if action == "q":
        raise SystemExit(0)
    if action == "s":
        print("  -> Skipped.")
        state.history.append(f"SKIP {title}")
        return -1, ""

    print("  -> Running...\n")
    completed = subprocess.run(
        argv,
        cwd=state.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = ""
    if completed.stdout:
        combined += completed.stdout
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        combined += completed.stderr
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    if completed.returncode == 0:
        print(f"\n  OK (exit={completed.returncode})")
        state.history.append(f"OK   {title}")
    else:
        print(f"\n  FAILED (exit={completed.returncode})")
        state.history.append(f"FAIL {title} exit={completed.returncode}")
    return completed.returncode, combined


def _probe_lab(ports: list[int] | None = None) -> str | None:
    """Return base_url of a live coupon-race lab, if found."""

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
    """Write a demo workflow whose base_url matches the live lab."""

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
    print("Checking core installation...")
    code, _ = _run_command(
        state,
        title="Environment check · doctor",
        argv=[*STATEBREAKER, "doctor"],
        explain="Verify Python / core API versions.",
    )
    if code not in (0, -1):
        print("  Hint: if doctor fails, run: python -m pip install -e .")
    _run_command(
        state,
        title="Installed plugins",
        argv=[*STATEBREAKER, "plugins", "list"],
        explain="Expect learner / generator / executor / verifier / reporter.",
    )
    missing: list[str] = []
    try:
        from statebreaker.plugins import PluginRegistry

        registry = PluginRegistry()
        for group, plugin_id in plugins:
            try:
                registry.get(group, plugin_id)
            except Exception:  # noqa: BLE001 - list missing plugins only
                missing.append(f"{group} / {plugin_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"  Plugin scan error: {exc}")
        return
    if missing:
        print("  Missing plugins (install from repo root):")
        for item in missing:
            print(f"    - {item}")
        print(
            textwrap.dedent(
                """\
                Install example:
                  python -m pip install -e ".[dev]"
                  python -m pip install -e ./race-generator ./race-executor
                  python -m pip install -e ./statebreaker-learner-delta
                  python -m pip install -e ./statebreaker-verifier-basic
                  python -m pip install -e ./statebreaker-reporter-pdf
                """
            )
        )
    else:
        print("  All demo plugins are loaded.")


def step_check_lab(state: WizardState) -> None:
    _print_panel(
        "Lab · Uncle Wang's milk-tea shop",
        textwrap.dedent(
            """\
            If the lab is not running yet, in another terminal:
              docker compose up --build
            If port 8080 is busy:
              set STATEBREAKER_LAB_PORT=18080   (cmd)
              $env:STATEBREAKER_LAB_PORT="18080"  (PowerShell)
              docker compose up --build
            """
        ).strip(),
    )
    found = _probe_lab()
    if found:
        state.lab_base_url = found
        print(f"  Detected lab: {found}/healthz")
    else:
        print("  Lab not detected on 8080/18080.")
        custom = _ask(
            "Enter base_url manually (Enter keeps default)",
            state.lab_base_url,
        )
        state.lab_base_url = custom.rstrip("/")
    _ensure_workflow_for_lab(state)
    print(f"  Demo workflow written: {state.workflow_path}")
    print(f"  base_url = {state.lab_base_url}")
    try:
        with urlopen(f"{state.lab_base_url}/healthz", timeout=3) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
        print(f"  healthz -> {body.strip()}")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"  Warning: healthz unreachable: {exc}")
        print("  learn/attack will fail until Docker lab is up.")


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
        inv_path.write_text(
            json.dumps(data.get("invariants", []), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        count = len(data.get("invariants", []))
        print(f"  Learned {count} invariant candidate(s).")
        if count:
            use = _choose(
                "Use learned invariants instead of examples/coupon-race/invariants.yaml?",
                [
                    ("y", "Yes — use learned-invariants.json"),
                    ("n", "No — keep example invariants"),
                ],
                default="n",
            )
            if use == "y":
                state.invariants_path = inv_path
                print(f"  invariants = {inv_path}")


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
        explain="Build bounded race AttackPlan list from workflow + invariants.",
    )
    if code == 0 and out.is_file():
        state.plans_path = out
        plans = json.loads(out.read_text(encoding="utf-8"))
        print(f"  {len(plans)} plan(s). Types:")
        counts: dict[str, int] = {}
        for plan in plans:
            kind = str(plan.get("attack_type", "?"))
            counts[kind] = counts.get(kind, 0) + 1
        for kind, count in sorted(counts.items()):
            print(f"    - {kind}: {count}")


def step_select_and_attack(state: WizardState) -> None:
    if state.plans_path is None or not state.plans_path.is_file():
        example = state.root / "examples" / "coupon-race" / "attack-plan.yaml"
        print(f"  No plans.json — using example plan: {example}")
        plan_path = state.work_dir / "one-plan.json"
        plan = load_model(example, AttackPlan)
        write_json(plan_path, plan)
        state.selected_plan_path = plan_path
    else:
        plans_raw = json.loads(state.plans_path.read_text(encoding="utf-8"))
        plans = TypeAdapter(list[AttackPlan]).validate_python(plans_raw)
        print("  Attack plans:")
        for index, plan in enumerate(plans, start=1):
            print(f"    {index:2d}. [{plan.attack_type}] {plan.id}")
        default_idx = next(
            (i for i, p in enumerate(plans, start=1) if p.attack_type == "concurrent-replay"),
            1,
        )
        while True:
            raw = _ask("Enter plan number", str(default_idx))
            try:
                choice = int(raw)
                if 1 <= choice <= len(plans):
                    selected = plans[choice - 1]
                    break
            except ValueError:
                pass
            print("  Enter a valid number.")
        plan_path = state.work_dir / "one-plan.json"
        write_json(plan_path, selected)
        state.selected_plan_path = plan_path
        print(f"  Selected: {selected.id}")

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
        print("  Result summary:")
        print(f"    before: {result.before_state}")
        print(f"    after:  {result.after_state}")
        print(f"    vulnerability_observed: {result.plugin_data.get('vulnerability_observed')}")
        print(f"    status codes: {[r.status_code for r in result.responses]}")


def step_verify(state: WizardState) -> None:
    if state.raw_result_path is None or not state.raw_result_path.is_file():
        print("  No raw-result — run the attack step first.")
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
        print("  Findings:")
        for finding in findings:
            print(f"    - [{finding.verdict}] {finding.id}: {finding.title}")


def step_report(state: WizardState) -> None:
    if state.raw_result_path is None or state.selected_plan_path is None:
        print("  Missing attack artifacts — cannot build report.")
        return
    if state.findings_path is None or not state.findings_path.is_file():
        print("  No findings yet — report will use an empty findings list.")
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
    print(f"  RunBundle written: {bundle_path}")

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
        print(f"  PDF: {pdf.resolve()}")
        open_pdf = _ask("Open PDF with the system default app? (y/N)", "n")
        if pdf.is_file() and open_pdf.lower() == "y":
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
        print(f"  Could not open file: {exc}")


def step_show_artifacts(state: WizardState) -> None:
    print("  Artifacts under work dir:")
    for path in sorted(state.work_dir.rglob("*")):
        if path.is_file():
            print(f"    {path.relative_to(state.root)}  ({path.stat().st_size} bytes)")
    print("  Step history:")
    for item in state.history:
        print(f"    {item}")


def run_guided(state: WizardState) -> None:
    """Sequential pipeline with confirm-each-step UX."""

    steps: list[tuple[str, Callable[[WizardState], None]]] = [
        ("1/8 Environment check", step_check_env),
        ("2/8 Detect lab", step_check_lab),
        ("3/8 Validate workflow", step_validate_workflow),
        ("4/8 Learn rules (optional)", step_learn),
        ("5/8 Generate attack plans", step_generate),
        ("6/8 Select plan & attack", step_select_and_attack),
        ("7/8 Verify findings", step_verify),
        ("8/8 Generate PDF report", step_report),
    ]
    for title, fn in steps:
        print()
        print("#" * 64)
        print(f"# {title}")
        print("#" * 64)
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
            state.history.append(f"SKIP-STEP {title}")
            continue
        fn(state)
    step_show_artifacts(state)
    print("\nDemo finished. Artifacts:", state.work_dir.resolve())


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
        print()
        print("Main menu")
        for key, (label, _) in actions.items():
            print(f"  [{key}] {label}")
        print(f"  Current lab: {state.lab_base_url}")
        print(f"  Work dir:    {state.work_dir}")
        choice = _ask("Select", "g").lower()
        if choice not in actions:
            print("  Invalid option.")
            continue
        label, fn = actions[choice]
        if fn is None:
            print("Bye.")
            return
        if choice == "g":
            fn(state)
            return
        print(f"\n>>> {label}")
        fn(state)


def main_wizard(*, root: Path | None = None, guided: bool = False) -> None:
    root_path = (root or Path.cwd()).resolve()
    if not (root_path / "examples" / "coupon-race").is_dir():
        print(
            "Error: run this from the StateBreaker repo root "
            "(needs examples/coupon-race)."
        )
        raise SystemExit(2)
    _banner()
    state = _default_paths(root_path)
    print(f"Repo root:  {root_path}")
    print(f"Artifacts:  {state.work_dir}")
    print()
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
