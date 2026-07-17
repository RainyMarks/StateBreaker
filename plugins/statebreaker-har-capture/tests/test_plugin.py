from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

import pytest
from statebreaker.cli import app
from statebreaker.models import Workflow
from statebreaker.plugins import PluginRegistry
from typer.testing import CliRunner

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.options import HarCaptureOptions
from statebreaker_har_capture.plugin import HarCapturePlugin

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_capture_returns_real_workflow_and_is_deterministic() -> None:
    plugin = HarCapturePlugin()

    first = await plugin.capture(FIXTURES / "minimal.har", {})
    second = await plugin.capture(FIXTURES / "minimal.har", {})

    assert isinstance(first, Workflow)
    assert first == second
    assert first.sessions["default"].headers == {}
    assert first.sessions["default"].cookies == {}
    assert first.variables == {}
    assert all(step.extract == [] for step in first.steps)


@pytest.mark.asyncio
async def test_capture_preserves_authenticated_json_request_for_replay() -> None:
    workflow = await HarCapturePlugin().capture(FIXTURES / "replayable-json.har", {})

    request = workflow.steps[0].request
    assert str(workflow.base_url) == "http://127.0.0.1:18080/"
    assert request.method == "POST"
    assert request.path == "/api/runs/demo/redeem"
    assert request.headers["authorization"] == "Bearer TEST-AUTH-TOKEN"
    assert request.headers["cookie"] == "session=TEST-SESSION"
    assert request.json_body == {"coupon_code": "BUG50"}


@pytest.mark.asyncio
async def test_state_probe_option_uses_original_entry_index() -> None:
    workflow = await HarCapturePlugin().capture(
        FIXTURES / "minimal.har", {"state_probe_entry_indices": [1]}
    )

    assert workflow.steps[0].role == "action"
    assert workflow.steps[1].role == "probe"
    assert workflow.state_probe_steps == [workflow.steps[1].id]


@pytest.mark.asyncio
async def test_invalid_state_probe_indices_fail_cleanly() -> None:
    plugin = HarCapturePlugin()

    with pytest.raises(HarCaptureError, match=r"entry 9.*out of range"):
        await plugin.capture(FIXTURES / "minimal.har", {"state_probe_entry_indices": [9]})
    with pytest.raises(HarCaptureError, match="must not contain duplicates"):
        await plugin.capture(FIXTURES / "minimal.har", {"state_probe_entry_indices": [0, 0]})
    with pytest.raises(HarCaptureError, match="non-negative"):
        await plugin.capture(FIXTURES / "minimal.har", {"state_probe_entry_indices": [-1]})


def test_options_forbid_unknown_fields() -> None:
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        HarCaptureOptions.model_validate({"unsupported": True})


def test_editable_install_entry_point_is_discoverable() -> None:
    entry_points = metadata.entry_points().select(group="statebreaker.capture", name="har.capture")
    assert len(entry_points) == 1

    instance = PluginRegistry().get("statebreaker.capture", "har.capture")
    assert instance.manifest.plugin_id == "har.capture"
    assert isinstance(instance, HarCapturePlugin)


def test_cli_workflow_import_writes_revalidatable_json(tmp_path: Path) -> None:
    output = tmp_path / "workflow.json"
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "import",
            str(FIXTURES / "minimal.har"),
            "--plugin",
            "har.capture",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    workflow = Workflow.model_validate(json.loads(output.read_text(encoding="utf-8")))
    assert isinstance(workflow, Workflow)
    validate_result = CliRunner().invoke(app, ["workflow", "validate", str(output)])
    assert validate_result.exit_code == 0, validate_result.output
