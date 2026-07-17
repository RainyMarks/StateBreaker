"""Minimal interactive demo for the coupon-race pipeline.

Design goals (v2):
- One clear screen focus: menu OR one step — not nested confirm walls
- Numbered choices that always work (stdin line input)
- Compact output: short headers, full command once, then raw process output
- Optional non-interactive: ``statebreaker demo --auto``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
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

PYTHON = sys.executable
SB = [PYTHON, "-m", "statebreaker"]


@dataclass
class State:
    root: Path
    work: Path
    workflow: Path
    invariants: Path
    lab: str = "http://127.0.0.1:8080"
    plans: Path | None = None
    plan: Path | None = None
    result: Path | None = None
    findings: Path | None = None


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(f"! {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"+ {msg}", flush=True)


def _header(title: str) -> None:
    _out()
    _out(f"== {title} ==")


def _read(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        line = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        _out()
        raise SystemExit(0) from None
    return line if line else default


def _pick(prompt: str, choices: dict[str, str], default: str) -> str:
    """choices: key -> label. Keys are matched case-insensitively."""

    _out(prompt)
    for key, label in choices.items():
        mark = " *" if key.lower() == default.lower() else ""
        _out(f"  {key}) {label}{mark}")
    keys = {k.lower(): k for k in choices}
    while True:
        answer = _read(">", default).lower()
        if answer in keys:
            return keys[answer]
        _err(f"type one of: {', '.join(choices)}")


def _confirm_run(command: list[str]) -> str:
    """Return run | skip | quit. Single decision point."""

    _out()
    _out("$ " + subprocess.list2cmdline(command))
    return _pick(
        "run this command?",
        {"r": "run", "s": "skip", "q": "quit demo"},
        "r",
    )


def _exec(state: State, command: list[str], *, auto: bool) -> int:
    if not auto:
        action = _confirm_run(command)
        if action == "q":
            raise SystemExit(0)
        if action == "s":
            _out("(skipped)")
            return -1
    else:
        _out("$ " + subprocess.list2cmdline(command))

    proc = subprocess.run(
        command,
        cwd=state.root,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode == 0:
        _ok(f"exit {proc.returncode}")
    else:
        _err(f"exit {proc.returncode}")
    return proc.returncode


def _probe_lab() -> str | None:
    for port in (8080, 18080, 8000):
        try:
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:  # noqa: S310
                body = resp.read().decode("utf-8", errors="replace")
            if "coupon-race" in body:
                return f"http://127.0.0.1:{port}"
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
    return None


def _sync_workflow(state: State) -> None:
    source = state.root / "examples" / "coupon-race" / "workflow.yaml"
    data = load_model(source, Workflow).model_dump(mode="json")
    data["base_url"] = state.lab
    path = state.work / "workflow.json"
    write_json(path, Workflow.model_validate(data))
    state.workflow = path


def _init(root: Path) -> State:
    work = root / ".statebreaker" / "wizard"
    work.mkdir(parents=True, exist_ok=True)
    return State(
        root=root,
        work=work,
        workflow=root / "examples" / "coupon-race" / "workflow.yaml",
        invariants=root / "examples" / "coupon-race" / "invariants.yaml",
    )


# ----- steps -----


def step_env(state: State, *, auto: bool) -> None:
    _header("1 environment")
    _exec(state, [*SB, "doctor"], auto=auto)
    _exec(state, [*SB, "plugins", "list"], auto=auto)
    needed = [
        ("statebreaker.learner", "team.delta-learner"),
        ("statebreaker.generator", "team.race-generator"),
        ("statebreaker.executor", "team.race-executor"),
        ("statebreaker.verifier", "team.basic-verifier"),
        ("statebreaker.reporter", "team.pdf-reporter"),
    ]
    from statebreaker.plugins import PluginRegistry

    reg = PluginRegistry()
    missing = []
    for group, pid in needed:
        try:
            reg.get(group, pid)
            _ok(f"{pid}")
        except Exception:  # noqa: BLE001
            missing.append(pid)
            _err(f"missing {pid}")
    if missing:
        _err("install plugins from repo root, then retry")


def step_lab(state: State, *, auto: bool) -> None:
    _header("2 lab")
    found = _probe_lab()
    if found:
        state.lab = found
        _ok(f"lab up: {found}")
    else:
        _err("lab not found on 8080/18080")
        _out("  docker compose up --build")
        _out('  or: $env:STATEBREAKER_LAB_PORT="18080"; docker compose up --build')
        if not auto:
            state.lab = _read("base_url", state.lab).rstrip("/")
    _sync_workflow(state)
    _ok(f"workflow -> {state.workflow.name}  base_url={state.lab}")
    try:
        with urlopen(f"{state.lab}/healthz", timeout=3) as resp:  # noqa: S310
            _ok(resp.read().decode("utf-8", errors="replace").strip())
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        _err(f"healthz failed: {exc}")


def step_validate(state: State, *, auto: bool) -> None:
    _header("3 validate workflow")
    _exec(state, [*SB, "workflow", "validate", str(state.workflow)], auto=auto)


def step_learn(state: State, *, auto: bool) -> None:
    _header("4 learn (optional)")
    out = state.work / "learning-result.json"
    code = _exec(
        state,
        [
            *SB,
            "learn",
            str(state.workflow),
            "--plugin",
            "team.delta-learner",
            "-o",
            str(out),
        ],
        auto=auto,
    )
    if code != 0 or not out.is_file():
        return
    data = json.loads(out.read_text(encoding="utf-8"))
    invs = data.get("invariants", [])
    inv_path = state.work / "learned-invariants.json"
    inv_path.write_text(json.dumps(invs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _ok(f"{len(invs)} invariant(s) -> {inv_path.name}")
    if (
        invs
        and not auto
        and _pick("use learned invariants?", {"y": "yes", "n": "no, keep examples"}, "n")
        == "y"
    ):
        state.invariants = inv_path



def step_generate(state: State, *, auto: bool) -> None:
    _header("5 generate plans")
    out = state.work / "plans.json"
    code = _exec(
        state,
        [
            *SB,
            "generate",
            str(state.workflow),
            str(state.invariants),
            "--plugin",
            "team.race-generator",
            "-o",
            str(out),
        ],
        auto=auto,
    )
    if code != 0 or not out.is_file():
        return
    state.plans = out
    plans = json.loads(out.read_text(encoding="utf-8"))
    _ok(f"{len(plans)} plan(s)")
    for i, plan in enumerate(plans, 1):
        _out(f"  {i:2d}. {plan.get('attack_type')}  {plan.get('id')}")


def _select_plan(state: State, *, auto: bool) -> Path:
    if state.plans and state.plans.is_file():
        plans = TypeAdapter(list[AttackPlan]).validate_python(
            json.loads(state.plans.read_text(encoding="utf-8"))
        )
    else:
        example = state.root / "examples" / "coupon-race" / "attack-plan.yaml"
        plan = load_model(example, AttackPlan)
        path = state.work / "one-plan.json"
        write_json(path, plan)
        return path

    default = next(
        (i for i, p in enumerate(plans, 1) if p.attack_type == "concurrent-replay"),
        1,
    )
    if auto:
        chosen = plans[default - 1]
    else:
        _out("pick a plan number:")
        for i, plan in enumerate(plans, 1):
            star = " *" if i == default else ""
            _out(f"  {i}) {plan.attack_type}  {plan.id}{star}")
        while True:
            raw = _read(">", str(default))
            try:
                n = int(raw)
                if 1 <= n <= len(plans):
                    chosen = plans[n - 1]
                    break
            except ValueError:
                pass
            _err(f"1..{len(plans)}")
    path = state.work / "one-plan.json"
    write_json(path, chosen)
    _ok(chosen.id)
    return path


def step_attack(state: State, *, auto: bool) -> None:
    _header("6 attack")
    state.plan = _select_plan(state, auto=auto)
    out = state.work / "raw-result.json"
    code = _exec(
        state,
        [
            *SB,
            "attack",
            str(state.plan),
            "--workflow",
            str(state.workflow),
            "--plugin",
            "team.race-executor",
            "-o",
            str(out),
        ],
        auto=auto,
    )
    if code != 0 or not out.is_file():
        return
    state.result = out
    result = load_model(out, RawAttackResult)
    _out(f"  before {result.before_state}")
    _out(f"  after  {result.after_state}")
    _out(f"  vuln   {result.plugin_data.get('vulnerability_observed')}")
    _out(f"  codes  {[r.status_code for r in result.responses]}")


def step_verify(state: State, *, auto: bool) -> None:
    _header("7 verify")
    if not state.result or not state.result.is_file():
        _err("no attack result — run step 6 first")
        return
    out = state.work / "findings.json"
    code = _exec(
        state,
        [
            *SB,
            "verify",
            str(state.result),
            str(state.invariants),
            "--plugin",
            "team.basic-verifier",
            "-o",
            str(out),
        ],
        auto=auto,
    )
    if code != 0 or not out.is_file():
        return
    state.findings = out
    findings = TypeAdapter(list[Finding]).validate_python(
        json.loads(out.read_text(encoding="utf-8"))
    )
    for f in findings:
        _out(f"  [{f.verdict}] {f.id}")


def step_report(state: State, *, auto: bool) -> None:
    _header("8 report")
    if not state.result or not state.plan:
        _err("no attack artifacts — run step 6 first")
        return
    findings: list[Finding] = []
    if state.findings and state.findings.is_file():
        findings = TypeAdapter(list[Finding]).validate_python(
            json.loads(state.findings.read_text(encoding="utf-8"))
        )
    bundle_path = state.work / "run-bundle.json"
    write_json(
        bundle_path,
        RunBundle(
            workflow=load_model(state.workflow, Workflow),
            attack_plan=load_model(state.plan, AttackPlan),
            result=load_model(state.result, RawAttackResult),
            findings=findings,
        ),
    )
    report_dir = state.work / "report"
    code = _exec(
        state,
        [
            *SB,
            "report",
            str(bundle_path),
            "--plugin",
            "team.pdf-reporter",
            "--output-dir",
            str(report_dir),
        ],
        auto=auto,
    )
    if code == 0:
        pdf = report_dir / "statebreaker-report.pdf"
        _ok(str(pdf.resolve()))
        if not auto and pdf.is_file() and _pick("open PDF?", {"y": "yes", "n": "no"}, "n") == "y":
            _open(pdf)


def _open(path: Path) -> None:
    try:
        if sys.platform == "win32":
            import os

            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            bin_ = shutil.which("xdg-open")
            if bin_:
                subprocess.run([bin_, str(path)], check=False)
    except OSError as exc:
        _err(str(exc))


def step_show(state: State, *, auto: bool = False) -> None:
    _header("artifacts")
    _out(str(state.work.resolve()))
    for path in sorted(state.work.rglob("*")):
        if path.is_file():
            rel = path.relative_to(state.root)
            _out(f"  {rel}  ({path.stat().st_size}b)")


def run_all(state: State, *, auto: bool, skip_learn: bool) -> None:
    step_env(state, auto=auto)
    step_lab(state, auto=auto)
    step_validate(state, auto=auto)
    if not skip_learn:
        step_learn(state, auto=auto)
    else:
        _out("(learn skipped)")
    step_generate(state, auto=auto)
    step_attack(state, auto=auto)
    step_verify(state, auto=auto)
    step_report(state, auto=auto)
    step_show(state)


def run_menu(state: State) -> None:
    actions = {
        "1": ("environment", step_env),
        "2": ("lab detect", step_lab),
        "3": ("validate workflow", step_validate),
        "4": ("learn", step_learn),
        "5": ("generate plans", step_generate),
        "6": ("attack", step_attack),
        "7": ("verify", step_verify),
        "8": ("PDF report", step_report),
        "9": ("list artifacts", step_show),
        "a": ("run ALL steps (ask each command)", None),
        "q": ("quit", None),
    }
    while True:
        _header("StateBreaker demo")
        _out(f"lab  {state.lab}")
        _out(f"work {state.work}")
        _out()
        for key, (label, _) in actions.items():
            _out(f"  {key}) {label}")
        choice = _read(">", "a").lower()
        if choice not in actions:
            _err("unknown option")
            continue
        label, fn = actions[choice]
        if choice == "q":
            _out("bye")
            return
        if choice == "a":
            skip = _pick("include learn step?", {"y": "yes", "n": "skip learn"}, "n") == "n"
            run_all(state, auto=False, skip_learn=skip)
            continue
        assert fn is not None
        fn(state, auto=False)


def main_wizard(
    *,
    root: Path | None = None,
    guided: bool = False,
    auto: bool = False,
    skip_learn: bool = True,
) -> None:
    root_path = (root or Path.cwd()).resolve()
    if not (root_path / "examples" / "coupon-race").is_dir():
        _err("run from StateBreaker repo root (need examples/coupon-race)")
        raise SystemExit(2)

    state = _init(root_path)
    _out("StateBreaker demo")
    _out(f"root {root_path}")
    _out(f"work {state.work}")

    if auto:
        run_all(state, auto=True, skip_learn=skip_learn)
        return
    if guided:
        run_all(state, auto=False, skip_learn=skip_learn)
        return
    run_menu(state)
