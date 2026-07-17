from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from statebreaker.cli import app

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
            "--no-report",
            "--output-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 3
    assert "not found" in result.stderr


def test_demo_is_a_non_interactive_command() -> None:
    result = runner.invoke(app, ["demo", "--help"])
    assert result.exit_code == 0
    assert "--target" in result.stdout
    assert "--no-report" in result.stdout
