from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
from statebreaker.models import Workflow
from statebreaker.runtime import ExecutionRuntime

from statebreaker_har_capture.plugin import HarCapturePlugin

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LAB_ROOT = REPOSITORY_ROOT / "labs" / "coupon-race"
sys.path.insert(0, str(LAB_ROOT))

from app.main import COUPON_CODE, RUNS, app  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "coupon-race-normal.har"
RECORDED_RUN_ID = "recordedrunid000000000000000001"
VARIANT_RECORDED_RUN_ID = "recordedrunid999999999999999999"
CAPTURE_OPTIONS = {
    "setup_entry_indices": [0],
    "state_probe_entry_indices": [1, 3],
}


class RecordingASGITransport(httpx.ASGITransport):
    """ASGI transport that records every request before dispatching it to the real app."""

    def __init__(self, *, app: Any) -> None:
        super().__init__(app=app)
        self.handled_requests: list[tuple[str, str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.handled_requests.append((request.method, request.url.host, request.url.path))
        return await super().handle_async_request(request)


@pytest.fixture(autouse=True)
def isolated_coupon_race_state() -> Iterator[None]:
    RUNS.clear()
    yield
    RUNS.clear()


async def capture_coupon_race(source: Path = FIXTURE) -> Workflow:
    return await HarCapturePlugin().capture(source, CAPTURE_OPTIONS)


def assert_workflow_references_are_complete(workflow: Workflow) -> None:
    steps_by_id = {step.id: step for step in workflow.steps}
    assert len(steps_by_id) == len(workflow.steps)
    for step in workflow.steps:
        assert all(dependency in steps_by_id for dependency in step.depends_on)
        assert step.id not in step.depends_on
    for probe_id in workflow.state_probe_steps:
        assert probe_id in steps_by_id
        assert steps_by_id[probe_id].role == "probe"
    assert Workflow.model_validate(workflow.model_dump(mode="json")) == workflow


def assert_no_real_credentials(value: str) -> None:
    folded = value.casefold()
    for marker in (
        "authorization",
        "cookie",
        "bearer ",
        "private key",
    ):
        assert marker not in folded


@pytest.mark.asyncio
async def test_coupon_race_capture_is_deterministic_and_replayable() -> None:
    fixture_text = FIXTURE.read_text(encoding="utf-8")
    assert_no_real_credentials(fixture_text)

    first = await capture_coupon_race()
    second = await capture_coupon_race()
    assert isinstance(first, Workflow)
    assert Workflow.model_validate(first.model_dump()) == first
    assert first.model_dump() == second.model_dump()
    assert_workflow_references_are_complete(first)
    assert len(first.steps) == 4

    create, before_probe, redeem, after_probe = first.steps
    assert [step.request.method for step in first.steps] == ["POST", "GET", "POST", "GET"]
    assert [step.request.path for step in first.steps] == [
        "/api/runs",
        "/api/runs/${run_id}/state",
        "/api/runs/${run_id}/redeem",
        "/api/runs/${run_id}/state",
    ]
    assert [step.id.split("-", maxsplit=2)[1] for step in first.steps] == [
        "0000",
        "0001",
        "0002",
        "0003",
    ]

    assert [step.role for step in first.steps] == ["setup", "probe", "action", "probe"]
    assert create.role == "setup"
    assert len(create.extract) == 1
    extractor = create.extract[0]
    assert extractor.name == "run_id"
    assert extractor.kind == "jsonpath"
    assert extractor.expression == "$.run_id"
    assert extractor.required is True
    assert sum(len(step.extract) for step in first.steps) == 1

    assert first.state_probe_steps == [before_probe.id, after_probe.id]
    assert before_probe.role == "probe"
    assert after_probe.role == "probe"
    for consumer in first.steps[1:]:
        assert create.id in consumer.depends_on
    assert before_probe.depends_on == [create.id]
    assert redeem.depends_on == [before_probe.id, create.id]
    assert after_probe.depends_on == [redeem.id, create.id]

    assert redeem.request.json_body == {"coupon_code": COUPON_CODE}
    assert "${" not in json.dumps(redeem.request.json_body)
    assert all(RECORDED_RUN_ID not in step.request.path for step in first.steps)
    assert all(RECORDED_RUN_ID not in step.id for step in first.steps)
    assert all(
        RECORDED_RUN_ID not in dependency
        for step in first.steps
        for dependency in step.depends_on
    )
    assert all(RECORDED_RUN_ID not in probe_id for probe_id in first.state_probe_steps)
    assert RECORDED_RUN_ID not in first.model_dump_json()
    assert all(not session.cookies for session in first.sessions.values())
    assert all(
        "authorization" not in step.request.headers and "cookie" not in step.request.headers
        for step in first.steps
    )


@pytest.mark.asyncio
async def test_recorded_run_id_does_not_affect_captured_workflow(tmp_path: Path) -> None:
    original_document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    variant_document = deepcopy(original_document)
    replacement_count = 0
    for entry in variant_document["log"]["entries"]:
        request_url = entry["request"]["url"]
        replacement_count += request_url.count(RECORDED_RUN_ID)
        entry["request"]["url"] = request_url.replace(
            RECORDED_RUN_ID, VARIANT_RECORDED_RUN_ID
        )

        response_text = entry["response"]["content"]["text"]
        replacement_count += response_text.count(RECORDED_RUN_ID)
        entry["response"]["content"]["text"] = response_text.replace(
            RECORDED_RUN_ID, VARIANT_RECORDED_RUN_ID
        )
    assert replacement_count == 7

    variant_path = tmp_path / "coupon-race-variant.har"
    variant_path.write_text(json.dumps(variant_document), encoding="utf-8")
    original = await capture_coupon_race()
    variant = await capture_coupon_race(variant_path)

    assert [step.id for step in original.steps] == [step.id for step in variant.steps]
    assert [
        [extractor.model_dump(mode="json") for extractor in step.extract]
        for step in original.steps
    ] == [
        [extractor.model_dump(mode="json") for extractor in step.extract]
        for step in variant.steps
    ]
    assert [step.request.model_dump(mode="json") for step in original.steps] == [
        step.request.model_dump(mode="json") for step in variant.steps
    ]
    assert [step.depends_on for step in original.steps] == [
        step.depends_on for step in variant.steps
    ]
    assert original.state_probe_steps == variant.state_probe_steps
    assert original.model_dump(mode="json") == variant.model_dump(mode="json")
    for recorded_id in (RECORDED_RUN_ID, VARIANT_RECORDED_RUN_ID):
        assert recorded_id not in original.model_dump_json()
        assert recorded_id not in variant.model_dump_json()
    assert_workflow_references_are_complete(original)
    assert_workflow_references_are_complete(variant)


@pytest.mark.asyncio
async def test_coupon_race_capture_replays_through_real_runtime(
    tmp_path: Path,
) -> None:
    assert not RUNS
    workflow = await capture_coupon_race()
    transport = RecordingASGITransport(app=app)
    output_root = tmp_path / "runtime-output"

    async with ExecutionRuntime(
        workflow,
        output_root=output_root,
        transport=transport,
    ) as runtime:
        responses = await runtime.execute_workflow()

        assert [record.status_code for record in responses] == [201, 200, 200, 200]
        create_payload = json.loads(responses[0].body_preview)
        actual_run_id = create_payload["run_id"]
        assert actual_run_id
        assert actual_run_id != RECORDED_RUN_ID
        assert runtime.variables["run_id"] == actual_run_id

        expected_requests = [
            ("POST", "coupon-race.test", "/api/runs"),
            ("GET", "coupon-race.test", f"/api/runs/{actual_run_id}/state"),
            ("POST", "coupon-race.test", f"/api/runs/{actual_run_id}/redeem"),
            ("GET", "coupon-race.test", f"/api/runs/{actual_run_id}/state"),
        ]
        assert isinstance(transport, httpx.ASGITransport)
        assert transport.handled_requests == expected_requests
        assert all(RECORDED_RUN_ID not in path for _, _, path in transport.handled_requests)

        records_by_step = {record.step_id: record for record in responses}
        before = json.loads(records_by_step[workflow.state_probe_steps[0]].body_preview)
        after = json.loads(records_by_step[workflow.state_probe_steps[1]].body_preview)
        assert before["discount_yuan"] == 0
        assert before["coupon_used"] is False
        assert before["successful_redemptions"] == 0
        assert after["discount_yuan"] == 50
        assert after["coupon_used"] is True
        assert after["successful_redemptions"] == 1

        assert list(RUNS) == [actual_run_id]
        run = RUNS[actual_run_id]
        assert [event.kind for event in run.events] == [
            "run.created",
            "coupon.checked",
            "coupon.committed",
        ]
        assert runtime.run_dir.parent == output_root
        assert runtime.run_dir.is_relative_to(tmp_path)
        assert (runtime.run_dir / "events.jsonl").is_file()
        assert len(runtime.events) == 8
        assert all(
            event.kind in {"request.started", "request.completed"} for event in runtime.events
        )

    assert RECORDED_RUN_ID not in RUNS


def test_coupon_race_lab_state_is_isolated_between_tests() -> None:
    assert not RUNS
