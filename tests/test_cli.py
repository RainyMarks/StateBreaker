from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from statebreaker.cli import app
from statebreaker.documents import load_model, write_json
from statebreaker.models import AttackPlan

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


def test_doctor() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "StateBreaker core" in result.stdout
    assert "核心不限制目标" in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "StateBreaker 0.1.0" in result.stdout


def test_validate_example_workflow() -> None:
    result = runner.invoke(
        app,
        ["workflow", "validate", str(ROOT / "examples/coupon-race/workflow.yaml")],
    )
    assert result.exit_code == 0
    assert "工作流有效" in result.stdout


def test_bad_workflow_has_validation_exit_code(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("name: broken\n", encoding="utf-8")

    result = runner.invoke(app, ["workflow", "validate", str(path)])
    assert result.exit_code == 2
    assert "failed validation" in result.stderr


def test_missing_plugin_has_plugin_exit_code() -> None:
    result = runner.invoke(
        app,
        [
            "attack",
            str(ROOT / "examples/coupon-race/attack-plan.yaml"),
            "--workflow",
            str(ROOT / "examples/coupon-race/workflow.yaml"),
            "--plugin",
            "not-installed",
        ],
    )
    assert result.exit_code == 3
    assert "not found" in result.stderr


def test_schema_export(tmp_path: Path) -> None:
    output = tmp_path / "schemas"
    result = runner.invoke(app, ["schema", "export", str(output)])

    assert result.exit_code == 0
    workflow_schema = json.loads((output / "Workflow.schema.json").read_text(encoding="utf-8"))
    assert workflow_schema["title"] == "Workflow"
    assert (output / "AttackPlan.schema.json").exists()


def test_pipeline_missing_plugin_has_plugin_exit_code(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "pipeline",
            "run",
            str(ROOT / "examples/coupon-race/workflow.yaml"),
            str(ROOT / "examples/coupon-race/invariants.yaml"),
            "--generator",
            "not-installed",
            "--executor",
            "not-installed",
            "--verifier",
            "not-installed",
            "--attack-type",
            "concurrent-replay",
            "--output-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 3
    assert "not found" in result.stderr


def test_pipeline_requires_explicit_plugins_and_plan_selector() -> None:
    help_result = runner.invoke(app, ["pipeline", "run", "--help"])
    assert help_result.exit_code == 0
    for option in ("--generator", "--executor", "--verifier"):
        assert option in help_result.stdout
    assert "team.race" not in help_result.stdout

    result = runner.invoke(
        app,
        [
            "pipeline",
            "run",
            str(ROOT / "examples/coupon-race/workflow.yaml"),
            str(ROOT / "examples/coupon-race/invariants.yaml"),
            "--generator",
            "any.generator",
            "--executor",
            "any.executor",
            "--verifier",
            "any.verifier",
        ],
    )
    assert result.exit_code == 2
    assert "--plan-id" in result.stderr


def test_cli_exposes_stepwise_race_workflow() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("workflow", "invariants", "plans", "attack", "verify", "bundle"):
        assert command in result.stdout
    assert "demo" not in result.stdout


def test_plan_list_and_select_are_separate_steps(tmp_path: Path) -> None:
    plan = load_model(ROOT / "examples/coupon-race/attack-plan.yaml", AttackPlan)
    plans_path = tmp_path / "plans.json"
    selected_path = tmp_path / "selected.json"
    write_json(plans_path, [plan])

    listed = runner.invoke(app, ["plans", "list", str(plans_path)])
    assert listed.exit_code == 0
    assert "concurrent-replay" in listed.stdout
    assert "double-hand-coupon" in listed.stdout

    selected = runner.invoke(
        app,
        [
            "plans",
            "select",
            str(plans_path),
            "--attack-type",
            "concurrent-replay",
            "--output",
            str(selected_path),
        ],
    )
    assert selected.exit_code == 0
    assert selected_path.exists()
    assert load_model(selected_path, AttackPlan).id == "double-hand-coupon"
