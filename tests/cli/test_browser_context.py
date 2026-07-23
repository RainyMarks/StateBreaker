from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from statebreaker.browser_context import render_browser_context_executor
from statebreaker.cli.app import app as cli_app

runner = CliRunner()


def _write_plan(tmp_path: Path) -> Path:
    code_file = tmp_path / "code.json"
    code_file.write_text(json.dumps({"code": "echo 'ok';"}), encoding="utf-8")
    plan = {
        "name": "demo-plan",
        "autorun": False,
        "selectors": {
            "root": "[data-plan-root]",
            "output": "[data-plan-output]",
        },
        "runner": {
            "token_selector": "input[name='csrf_token']",
            "run_url_attribute": "data-run-url",
            "exercise_attribute": "data-exercise-id",
            "action_value": "execute",
            "code_file": str(code_file),
            "code_json_field": "code",
        },
        "fields": {
            "source": "from",
            "destination": "to",
            "amount": "amount",
        },
        "schedule": {
            "accounts": ["alpha", "beta", "gamma"],
            "initial_balances": {
                "alpha": "$100.00",
                "beta": "$1.00",
                "gamma": "$1.00",
            },
            "rounds": 2,
            "start_round": 1,
        },
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    return plan_file


def test_render_browser_context_executor_embeds_code_without_token(tmp_path: Path) -> None:
    script = render_browser_context_executor(_write_plan(tmp_path))

    assert "StateBreakerBrowserContextRunner" in script
    assert "echo 'ok';" in script
    assert "token_selector" in script
    assert "secret-token-value" not in script


def test_browser_context_render_command_writes_executor(tmp_path: Path) -> None:
    plan_file = _write_plan(tmp_path)
    output = tmp_path / "executor.js"

    result = runner.invoke(
        cli_app,
        ["browser-context", "render", str(plan_file), "--write", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "browser-context executor:" in result.output
    assert output.is_file()
    assert "demo-plan" in output.read_text(encoding="utf-8")
