from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from statebreaker.cli import app
from statebreaker.models import Workflow
from typer.testing import CliRunner

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.har_parser import parse_har
from statebreaker_har_capture.inference import collect_response_candidates
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions
from statebreaker_har_capture.plugin import HarCapturePlugin
from statebreaker_har_capture.response_body import (
    ResponseJsonFailure,
    decode_json_response,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOISY_FIXTURE = FIXTURES / "coupon-race-browser-noisy.har"
NOISY_OPTIONS = {
    "exclude_entry_indices": [0, 1, 2, 3, 4, 5],
    "setup_entry_indices": [6],
    "state_probe_entry_indices": [7, 9],
    "normalize_browser_headers": True,
}
_MISSING = object()
_TEMPLATE = "$" + "{run_id}"


def _entry(
    *,
    index: int = 0,
    text: Any = "{}",
    mime_type: Any = "application/json",
    encoding: Any = _MISSING,
    status: Any = 200,
    response_present: bool = True,
    content_present: bool = True,
    response_flags: dict[str, Any] | None = None,
    content_flags: dict[str, Any] | None = None,
    resource_type: str = "fetch",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "_resourceType": resource_type,
        "request": {
            "method": "GET",
            "url": f"https://example.test/api/items/{index}",
            "headers": [
                {"name": "User-Agent", "value": "synthetic-browser"},
                {
                    "name": "Authorization",
                    "value": "Bearer synthetic-test-credential",
                },
            ],
        },
    }
    if not response_present:
        return entry

    response: dict[str, Any] = {"status": status}
    if response_flags:
        response.update(response_flags)
    if content_present:
        content: dict[str, Any] = {"mimeType": mime_type}
        if text is not _MISSING:
            content["text"] = text
        if encoding is not _MISSING:
            content["encoding"] = encoding
        if content_flags:
            content.update(content_flags)
        response["content"] = content
    entry["response"] = response
    return entry


def _document(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"log": {"version": "1.2", "entries": list(entries)}}


def _normalize(
    *entries: dict[str, Any],
    required: list[int] | None = None,
    **option_overrides: Any,
) -> dict[str, Any]:
    options = HarCaptureOptions(
        required_response_body_entry_indices=required or [],
        **option_overrides,
    )
    return normalize_har(_document(*entries), options)


def _required_error(
    entry: dict[str, Any],
    expected_reason: str,
    *,
    options: dict[str, Any] | None = None,
) -> str:
    configured = {
        "required_response_body_entry_indices": [0],
        **(options or {}),
    }
    with pytest.raises(
        HarCaptureError,
        match=rf"HAR required response body error at entry 0: {expected_reason}",
    ) as error:
        normalize_har(_document(entry), HarCaptureOptions.model_validate(configured))
    rendered = str(error.value)
    assert "https://" not in rendered
    assert "/api/" not in rendered
    assert "Bearer" not in rendered
    assert "Authorization" not in rendered
    return rendered


def test_required_response_option_defaults_and_accepts_indices() -> None:
    assert HarCaptureOptions().required_response_body_entry_indices == []
    assert HarCaptureOptions(
        required_response_body_entry_indices=[3]
    ).required_response_body_entry_indices == [3]
    assert HarCaptureOptions(
        required_response_body_entry_indices=[3, 1]
    ).required_response_body_entry_indices == [3, 1]


@pytest.mark.parametrize(
    "value",
    [
        [-1],
        [0, 0],
        ["0"],
        [True],
        [0.0],
        None,
    ],
)
def test_required_response_option_is_strict(value: object) -> None:
    with pytest.raises(ValidationError):
        HarCaptureOptions.model_validate(
            {"required_response_body_entry_indices": value}
        )


def test_required_response_unknown_option_remains_forbidden() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HarCaptureOptions.model_validate(
            {
                "required_response_body_entry_indices": [0],
                "required_response_entries": [0],
            }
        )


def test_required_response_cannot_overlap_exclusion() -> None:
    with pytest.raises(
        ValidationError,
        match=(
            "exclude_entry_indices and required_response_body_entry_indices"
            r".*\[1\]"
        ),
    ) as error:
        HarCaptureOptions.model_validate(
            {
                "exclude_entry_indices": [1],
                "required_response_body_entry_indices": [1],
            }
        )
    rendered = str(error.value)
    assert "example.test" not in rendered
    assert "Authorization" not in rendered


def test_required_response_may_overlap_setup_or_probe() -> None:
    setup = HarCaptureOptions(
        required_response_body_entry_indices=[0],
        setup_entry_indices=[0],
    )
    probe = HarCaptureOptions(
        required_response_body_entry_indices=[0],
        state_probe_entry_indices=[0],
    )
    assert setup.setup_entry_indices == [0]
    assert probe.state_probe_entry_indices == [0]

def test_required_probe_overlap_preserves_role_and_probe_reference() -> None:
    workflow = Workflow.model_validate(
        _normalize(
            _entry(),
            required=[0],
            state_probe_entry_indices=[0],
        )
    )
    assert workflow.steps[0].role == "probe"
    assert workflow.state_probe_steps == [workflow.steps[0].id]


def test_required_response_out_of_range_uses_smallest_original_index() -> None:
    with pytest.raises(
        HarCaptureError,
        match=r"entry 2: index is out of range for 1 entries",
    ):
        _normalize(
            _entry(),
            required=[9, 2],
        )


def test_required_response_filtered_entry_fails_safely() -> None:
    static_entry = _entry(
        text="{}",
        mime_type="text/javascript",
        resource_type="script",
    )
    _required_error(
        static_entry,
        "selected entry was filtered",
    )


def test_filter_disabled_routes_static_entry_to_body_validation() -> None:
    static_entry = _entry(
        text="{}",
        mime_type="text/javascript",
        resource_type="script",
    )
    _required_error(
        static_entry,
        "response MIME is not JSON-compatible",
        options={"filter_static_resources": False},
    )


def test_required_response_uses_original_index_after_exclusion() -> None:
    with pytest.raises(
        HarCaptureError,
        match=r"entry 1: response\.content\.text is missing",
    ):
        _normalize(
            _entry(index=0),
            _entry(index=1, text=_MISSING),
            required=[1],
            exclude_entry_indices=[0],
        )


def test_required_indices_are_checked_in_numeric_order() -> None:
    document = _document(
        _entry(index=0, text=_MISSING),
        _entry(index=1, text=""),
    )
    first = HarCaptureOptions(required_response_body_entry_indices=[1, 0])
    second = HarCaptureOptions(required_response_body_entry_indices=[0, 1])
    errors = []
    for options in (first, second):
        with pytest.raises(HarCaptureError) as error:
            normalize_har(document, options)
        errors.append(str(error.value))
    assert errors[0] == errors[1]
    assert "entry 0" in errors[0]


@pytest.mark.parametrize(
    ("entry", "failure", "reason"),
    [
        (
            _entry(response_present=False),
            ResponseJsonFailure.RESPONSE_MISSING,
            "response is missing",
        ),
        (
            _entry(content_present=False),
            ResponseJsonFailure.CONTENT_MISSING,
            r"response\.content is missing",
        ),
        (
            _entry(text=_MISSING),
            ResponseJsonFailure.TEXT_MISSING,
            r"response\.content\.text is missing",
        ),
        (
            _entry(text=""),
            ResponseJsonFailure.BODY_EMPTY,
            "response body is empty",
        ),
        (
            _entry(text="   "),
            ResponseJsonFailure.INVALID_JSON,
            "response body is not valid JSON",
        ),
        (
            _entry(text="{}", mime_type="text/plain"),
            ResponseJsonFailure.MIME_NOT_JSON,
            "response MIME is not JSON-compatible",
        ),
        (
            _entry(text="{invalid"),
            ResponseJsonFailure.INVALID_JSON,
            "response body is not valid JSON",
        ),
        (
            _entry(text="e30=", encoding="gzip"),
            ResponseJsonFailure.UNSUPPORTED_ENCODING,
            "response body uses an unsupported encoding",
        ),
        (
            _entry(text="%%%", encoding="base64"),
            ResponseJsonFailure.INVALID_BASE64_JSON,
            "response body is not valid base64-encoded UTF-8 JSON",
        ),
        (
            _entry(
                text=base64.b64encode(b"\xff").decode(),
                encoding="base64",
            ),
            ResponseJsonFailure.INVALID_BASE64_JSON,
            "response body is not valid base64-encoded UTF-8 JSON",
        ),
        (
            _entry(
                text=base64.b64encode(b"{invalid").decode(),
                encoding="base64",
            ),
            ResponseJsonFailure.INVALID_BASE64_JSON,
            "response body is not valid base64-encoded UTF-8 JSON",
        ),
        (
            _entry(response_flags={"_truncated": True}),
            ResponseJsonFailure.TRUNCATED,
            "response body is explicitly truncated",
        ),
        (
            _entry(content_flags={"truncated": True}),
            ResponseJsonFailure.TRUNCATED,
            "response body is explicitly truncated",
        ),
        (
            _entry(status=204),
            ResponseJsonFailure.STATUS_204,
            "status 204 cannot provide a required response body",
        ),
    ],
)
def test_required_response_failures_are_shared_and_safe(
    entry: dict[str, Any],
    failure: ResponseJsonFailure,
    reason: str,
) -> None:
    assert decode_json_response(entry).failure == failure
    _required_error(entry, reason)
    assert collect_response_candidates(0, "step-0000", entry) == ()


@pytest.mark.parametrize(
    ("value", "mime_type", "encoding"),
    [
        ({}, "application/json", _MISSING),
        ([], "application/json; charset=utf-8", None),
        ("value", "Application/JSON", ""),
        (1234, "application/problem+json", _MISSING),
        (1.25, "application/vnd.test+json; version=1", _MISSING),
        (True, "application/json", _MISSING),
        (None, "application/json", _MISSING),
    ],
)
def test_all_valid_json_values_and_mime_variants_pass(
    value: Any,
    mime_type: str,
    encoding: Any,
) -> None:
    entry = _entry(
        text=json.dumps(value),
        mime_type=mime_type,
        encoding=encoding,
    )
    decoded = decode_json_response(entry)
    assert decoded.failure is None
    assert decoded.value == value
    candidate = Workflow.model_validate(_normalize(entry, required=[0]))
    assert candidate.steps[0].extract == []


def test_valid_base64_utf8_json_passes() -> None:
    text = base64.b64encode(json.dumps({"item_id": "synthetic-item-0001"}).encode()).decode()
    entry = _entry(text=text, encoding="base64")
    decoded = decode_json_response(entry)
    assert decoded.failure is None
    assert decoded.value == {"item_id": "synthetic-item-0001"}
    Workflow.model_validate(_normalize(entry, required=[0]))


def test_truncated_false_and_non_2xx_valid_json_pass() -> None:
    entry = _entry(
        text="{}",
        status=500,
        response_flags={"_truncated": False},
        content_flags={"truncated": False},
    )
    assert decode_json_response(entry).failure is None
    Workflow.model_validate(_normalize(entry, required=[0]))


def test_required_validation_runs_when_inference_is_disabled() -> None:
    _required_error(
        _entry(text=_MISSING),
        r"response\.content\.text is missing",
        options={"infer_response_variables": False},
    )
    candidate = Workflow.model_validate(
        _normalize(
            _entry(text='{"item_id":"synthetic-item-0001"}'),
            required=[0],
            infer_response_variables=False,
        )
    )
    assert candidate.steps[0].extract == []


def test_required_validation_does_not_require_candidate_or_consumer() -> None:
    candidate = Workflow.model_validate(
        _normalize(_entry(text='{"ok":true}'), required=[0])
    )
    assert candidate.steps[0].extract == []


def test_required_validation_is_non_mutating_and_deterministic() -> None:
    document = _document(_entry(text='{"item_id":"synthetic-item-0001"}'))
    original = deepcopy(document)
    options = HarCaptureOptions(required_response_body_entry_indices=[0])
    first = normalize_har(document, options)
    second = normalize_har(document, options)
    assert document == original
    assert first == second


@pytest.mark.asyncio
async def test_noisy_missing_create_body_fails_without_disclosure() -> None:
    with pytest.raises(
        HarCaptureError,
        match=r"entry 3: response\.content\.text is missing",
    ) as error:
        await HarCapturePlugin().capture(
            NOISY_FIXTURE,
            {"required_response_body_entry_indices": [3]},
        )
    rendered = str(error.value)
    raw = NOISY_FIXTURE.read_text(encoding="utf-8")
    recorded_id = json.loads(raw)["log"]["entries"][4]["request"]["url"].split(
        "/api/runs/", maxsplit=1
    )[1].split("/", maxsplit=1)[0]
    assert recorded_id not in rendered
    assert "https://" not in rendered
    assert "Authorization" not in rendered
    assert "Cookie" not in rendered


def test_noisy_complete_create_required_keeps_workflow_identical() -> None:
    document = parse_har(NOISY_FIXTURE)
    original = deepcopy(document)
    baseline = Workflow.model_validate(
        normalize_har(document, HarCaptureOptions.model_validate(NOISY_OPTIONS))
    )
    required = Workflow.model_validate(
        normalize_har(
            document,
            HarCaptureOptions.model_validate(
                {
                    **NOISY_OPTIONS,
                    "required_response_body_entry_indices": [6],
                }
            ),
        )
    )
    raw_document = json.loads(NOISY_FIXTURE.read_text(encoding="utf-8"))
    recorded_ids = {
        raw_document["log"]["entries"][index]["request"]["url"].split(
            "/api/runs/", maxsplit=1
        )[1].split("/", maxsplit=1)[0]
        for index in (4, 7)
    }
    serialized = required.model_dump_json()

    assert document == original
    assert required == baseline
    assert [step.role for step in required.steps] == [
        "setup",
        "probe",
        "action",
        "probe",
    ]
    assert len(required.steps) == 4
    assert sum(len(step.extract) for step in required.steps) == 1
    assert required.steps[0].extract[0].expression == "$.run_id"
    assert all(_TEMPLATE in step.request.path for step in required.steps[1:])
    assert required.state_probe_steps == [
        required.steps[1].id,
        required.steps[3].id,
    ]
    assert all(recorded_id not in serialized for recorded_id in recorded_ids)


def test_noisy_required_validation_preserves_credential_stripping() -> None:
    document = parse_har(NOISY_FIXTURE)
    options = {**NOISY_OPTIONS, "strip_credentials": True}
    baseline = Workflow.model_validate(
        normalize_har(document, HarCaptureOptions.model_validate(options))
    )
    required = Workflow.model_validate(
        normalize_har(
            document,
            HarCaptureOptions.model_validate(
                {
                    **options,
                    "required_response_body_entry_indices": [6],
                }
            ),
        )
    )
    assert required == baseline
    assert all("authorization" not in step.request.headers for step in required.steps)
    assert all("cookie" not in step.request.headers for step in required.steps)



def test_cli_required_response_success_and_validate(tmp_path: Path) -> None:
    output = tmp_path / "workflow.json"
    options = tmp_path / "capture-options.json"
    options.write_text(
        json.dumps(
            {
                **NOISY_OPTIONS,
                "required_response_body_entry_indices": [6],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflow",
            "import",
            str(NOISY_FIXTURE),
            "--plugin",
            "har.capture",
            "--options",
            str(options),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    workflow = Workflow.model_validate(json.loads(output.read_text(encoding="utf-8")))
    baseline = Workflow.model_validate(
        normalize_har(
            parse_har(NOISY_FIXTURE),
            HarCaptureOptions.model_validate(NOISY_OPTIONS),
        )
    )
    assert workflow == baseline
    validate = runner.invoke(app, ["workflow", "validate", str(output)])
    assert validate.exit_code == 0, validate.output


def test_cli_required_response_failure_is_safe_and_atomic(tmp_path: Path) -> None:
    output = tmp_path / "workflow.json"
    options = tmp_path / "capture-options.yaml"
    options.write_text(
        "required_response_body_entry_indices: [3]\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "import",
            str(NOISY_FIXTURE),
            "--plugin",
            "har.capture",
            "--options",
            str(options),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 3
    assert "HAR required response body error at entry 3" in result.stderr
    assert "response.content.text is missing" in result.stderr
    assert "https://" not in result.stderr
    assert "/api/" not in result.stderr
    assert "Authorization" not in result.stderr
    assert "Cookie" not in result.stderr
    assert "Bearer" not in result.stderr
    assert not output.exists()
