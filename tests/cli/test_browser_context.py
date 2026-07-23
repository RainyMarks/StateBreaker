from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from statebreaker.browser_context import BrowserContextRunResult, render_browser_context_executor
from statebreaker.cli import browser_context as browser_context_cli
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


def _write_har(tmp_path: Path) -> Path:
    har_file = tmp_path / "flow.har"
    har_file.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": "https://example.test/run?token=not-auth",
                                "headers": [
                                    {
                                        "name": "Content-Type",
                                        "value": "application/x-www-form-urlencoded",
                                    }
                                ],
                                "postData": {
                                    "mimeType": "application/x-www-form-urlencoded",
                                    "params": [
                                        {"name": "csrf_token", "value": "REDACTED_BROWSER_CSRF"},
                                        {"name": "action", "value": "execute-from-har"},
                                        {"name": "code", "value": "echo 'from har';"},
                                        {
                                            "name": "post_data",
                                            "value": json.dumps(
                                                {
                                                    "from": "alpha",
                                                    "to": "beta",
                                                    "amount": "10",
                                                }
                                            ),
                                        },
                                    ],
                                },
                            },
                            "response": {"status": 200, "headers": [], "content": {}},
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    return har_file


def test_render_browser_context_executor_embeds_code_without_token(tmp_path: Path) -> None:
    script = render_browser_context_executor(_write_plan(tmp_path))

    assert "StateBreakerBrowserContextRunner" in script
    assert "echo 'ok';" in script
    assert "token_selector" in script
    assert "secret-token-value" not in script


def test_render_browser_context_executor_can_use_har_request_shape(tmp_path: Path) -> None:
    script = render_browser_context_executor(
        _write_plan(tmp_path),
        har_path=_write_har(tmp_path),
        rounds=1,
    )

    assert "echo 'from har';" in script
    assert "execute-from-har" in script
    assert '"rounds": 1' in script
    assert "REDACTED_BROWSER_CSRF" not in script


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


def test_browser_context_run_command_uses_har_and_writes_result(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    async def fake_run(
        script: str,
        cdp: str,
        target_url: str | None,
        timeout_seconds: float,
        screenshot: Path | None,
    ) -> BrowserContextRunResult:
        assert "from har" in script
        assert cdp == "http://127.0.0.1:9333"
        assert target_url is None
        assert timeout_seconds == 12
        if screenshot is not None:
            screenshot.write_bytes(b"png")
        return BrowserContextRunResult(
            value={
                "rows": [{"okA": True, "okB": True}],
                "finalTotal": "$120.00",
            },
            target_url="https://example.test/run",
            screenshot_path=screenshot,
        )

    monkeypatch.setattr(browser_context_cli, "_run_browser_context_executor", fake_run)
    result_file = tmp_path / "result.json"
    screenshot = tmp_path / "shot.png"

    result = runner.invoke(
        cli_app,
        [
            "browser-context",
            "run",
            str(_write_plan(tmp_path)),
            "--har",
            str(_write_har(tmp_path)),
            "--cdp",
            "http://127.0.0.1:9333",
            "--rounds",
            "1",
            "--write-result",
            str(result_file),
            "--screenshot",
            str(screenshot),
            "--timeout-seconds",
            "12",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "successful race rounds: 1 / 1" in result.output
    assert "final total: $120.00" in result.output
    assert "HAR evidence used:" in result.output
    assert json.loads(result_file.read_text(encoding="utf-8"))["finalTotal"] == "$120.00"
    assert screenshot.read_bytes() == b"png"
