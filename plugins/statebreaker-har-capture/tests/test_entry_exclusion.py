from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from statebreaker.cli import app
from statebreaker.models import Workflow
from typer.testing import CliRunner

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.har_parser import parse_har
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions

FIXTURES = Path(__file__).parent / "fixtures"
MINIMAL_FIXTURE = FIXTURES / "minimal.har"
STATIC_FIXTURE = FIXTURES / "static-resources.har"
DYNAMIC_FIXTURE = FIXTURES / "dynamic-response-values.har"
NOISY_FIXTURE = FIXTURES / "coupon-race-browser-noisy.har"
OLD_RECORDED_ID = "chrome-old-run-00000001"
TARGET_RECORDED_ID = "chrome-target-run-000001"
NOISY_EXCLUDED_INDICES = [0, 1, 2, 3, 4, 5]
NOISY_OPTIONS = {
    "exclude_entry_indices": NOISY_EXCLUDED_INDICES,
    "setup_entry_indices": [6],
    "state_probe_entry_indices": [7, 9],
}


def _entry_index(step_id: str) -> int:
    match = re.match(r"step-(\d{4})-", step_id)
    assert match is not None
    return int(match.group(1))


def _assert_references_are_complete(workflow: Workflow) -> None:
    steps_by_id = {step.id: step for step in workflow.steps}
    assert len(steps_by_id) == len(workflow.steps)
    for step in workflow.steps:
        assert step.id not in step.depends_on
        assert all(dependency in steps_by_id for dependency in step.depends_on)
    for probe_id in workflow.state_probe_steps:
        assert probe_id in steps_by_id
        assert steps_by_id[probe_id].role == "probe"


def test_exclude_entry_indices_default_to_empty_and_accept_multiple() -> None:
    assert HarCaptureOptions().exclude_entry_indices == []
    assert HarCaptureOptions.model_validate(
        {"exclude_entry_indices": [3, 1]}
    ).exclude_entry_indices == [3, 1]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([-1], "non-negative"),
        ([0, 0], "must not contain duplicates"),
        (["0"], "valid integer"),
        ([True], "valid integer"),
        ([0.0], "valid integer"),
    ],
)
def test_exclude_entry_indices_are_strict(value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        HarCaptureOptions.model_validate({"exclude_entry_indices": value})


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (
            {"exclude_entry_indices": [1], "setup_entry_indices": [1]},
            r"exclude_entry_indices and setup_entry_indices.*\[1\]",
        ),
        (
            {"exclude_entry_indices": [1], "state_probe_entry_indices": [1]},
            r"exclude_entry_indices and state_probe_entry_indices.*\[1\]",
        ),
    ],
)
def test_exclusion_cannot_overlap_explicit_roles(
    options: dict[str, list[int]], message: str
) -> None:
    with pytest.raises(ValidationError, match=message) as error:
        HarCaptureOptions.model_validate(options)

    rendered = str(error.value)
    assert "example.test" not in rendered
    assert "Authorization" not in rendered
    assert "Cookie" not in rendered


def test_exclusion_out_of_range_fails_with_safe_original_count() -> None:
    document = parse_har(MINIMAL_FIXTURE)

    with pytest.raises(
        HarCaptureError,
        match=r"HAR entry exclusion error at entry 2.*out of range for 2 entries",
    ) as error:
        normalize_har(document, HarCaptureOptions(exclude_entry_indices=[2]))

    rendered = str(error.value)
    assert "https://" not in rendered
    assert "Authorization" not in rendered
    assert "Cookie" not in rendered


def test_single_exclusion_preserves_original_entry_index() -> None:
    candidate = normalize_har(
        parse_har(MINIMAL_FIXTURE),
        HarCaptureOptions(exclude_entry_indices=[0]),
    )

    assert len(candidate["steps"]) == 1
    assert _entry_index(candidate["steps"][0]["id"]) == 1


def test_multiple_exclusions_preserve_relative_order_and_original_indices() -> None:
    candidate = normalize_har(
        parse_har(NOISY_FIXTURE),
        HarCaptureOptions(
            exclude_entry_indices=[0, 3, 5],
            filter_static_resources=False,
        ),
    )

    assert [_entry_index(step["id"]) for step in candidate["steps"]] == [
        1,
        2,
        4,
        6,
        7,
        8,
        9,
    ]


def test_excluding_static_entry_is_valid_and_precedes_static_filtering() -> None:
    candidate = normalize_har(
        parse_har(STATIC_FIXTURE),
        HarCaptureOptions(exclude_entry_indices=[0]),
    )

    assert [_entry_index(step["id"]) for step in candidate["steps"]] == [1, 3]


def test_exclusion_remains_active_when_static_filter_is_disabled() -> None:
    candidate = normalize_har(
        parse_har(STATIC_FIXTURE),
        HarCaptureOptions(
            exclude_entry_indices=[0, 2],
            filter_static_resources=False,
        ),
    )

    assert [_entry_index(step["id"]) for step in candidate["steps"]] == [1, 3, 4]


@pytest.mark.parametrize(
    "excluded_url",
    [
        "https://excluded-origin.test/private",
        "data:text/plain,excluded-non-http-entry",
    ],
)
def test_excluded_invalid_or_cross_origin_entry_is_never_normalized(
    excluded_url: str,
) -> None:
    document = parse_har(MINIMAL_FIXTURE)
    document["log"]["entries"][0]["request"]["url"] = excluded_url

    candidate = normalize_har(
        document,
        HarCaptureOptions(exclude_entry_indices=[0]),
    )

    assert candidate["base_url"] == "https://example.test/"
    assert [_entry_index(step["id"]) for step in candidate["steps"]] == [1]


def test_excluded_producer_does_not_participate_in_response_inference() -> None:
    candidate = normalize_har(
        parse_har(DYNAMIC_FIXTURE),
        HarCaptureOptions(exclude_entry_indices=[0]),
    )

    assert [_entry_index(step["id"]) for step in candidate["steps"]] == [1, 2]
    assert all(
        extractor["expression"] != "$.run_id"
        for step in candidate["steps"]
        for extractor in step["extract"]
    )
    assert candidate["steps"][0]["request"]["path"].endswith(
        "/run-fictional-abc12345/state"
    )


def test_excluding_every_entry_fails_without_request_disclosure() -> None:
    document = parse_har(MINIMAL_FIXTURE)

    with pytest.raises(
        HarCaptureError,
        match=r"HAR entry exclusion error: all entries were excluded",
    ) as error:
        normalize_har(document, HarCaptureOptions(exclude_entry_indices=[0, 1]))

    assert "https://" not in str(error.value)
    assert "TEST-SECRET" not in str(error.value)


def test_exclusion_plus_static_filtering_cannot_create_empty_workflow() -> None:
    document = parse_har(STATIC_FIXTURE)

    with pytest.raises(
        HarCaptureError,
        match=r"all entries were filtered.*no business requests",
    ) as error:
        normalize_har(
            document,
            HarCaptureOptions(exclude_entry_indices=[1, 3]),
        )

    assert "https://" not in str(error.value)
    assert "FICTIONAL-" not in str(error.value)


def test_noisy_chrome_fixture_is_small_sanitized_and_browser_shaped() -> None:
    raw = NOISY_FIXTURE.read_bytes()
    document = json.loads(raw)
    entries = document["log"]["entries"]

    assert len(raw) < 20_000
    assert document["log"]["version"] == "1.2"
    assert len(entries) == 10
    assert {entry.get("_resourceType") for entry in entries} == {
        "document",
        "stylesheet",
        "script",
        "fetch",
    }
    assert any("timings" in entry for entry in entries)
    assert any(entry.get("cache") == {} for entry in entries)
    assert any("_initiator" in entry for entry in entries)
    assert "text" not in entries[3]["response"]["content"]
    assert all(entry["request"].get("cookies", []) == [] for entry in entries)
    assert all(entry["response"].get("cookies", []) == [] for entry in entries)
    assert all(
        header.get("name", "").casefold()
        not in {"authorization", "cookie", "set-cookie"}
        for entry in entries
        for header in [
            *entry["request"].get("headers", []),
            *entry["response"].get("headers", []),
        ]
    )


def test_noisy_chrome_exclusion_produces_exact_replayable_four_step_workflow() -> None:
    document = parse_har(NOISY_FIXTURE)
    original = deepcopy(document)
    options = HarCaptureOptions.model_validate(NOISY_OPTIONS)

    first = normalize_har(document, options)
    reordered = normalize_har(
        document,
        HarCaptureOptions.model_validate(
            {**NOISY_OPTIONS, "exclude_entry_indices": [5, 3, 1, 4, 0, 2]}
        ),
    )
    workflow = Workflow.model_validate(first)

    assert document == original
    assert first == reordered
    assert [_entry_index(step.id) for step in workflow.steps] == [6, 7, 8, 9]
    assert [step.role for step in workflow.steps] == [
        "setup",
        "probe",
        "action",
        "probe",
    ]
    assert [step.request.path for step in workflow.steps] == [
        "/api/runs",
        "/api/runs/${run_id}/state",
        "/api/runs/${run_id}/redeem",
        "/api/runs/${run_id}/state",
    ]

    create, before_probe, redeem, after_probe = workflow.steps
    assert create.extract[0].name == "run_id"
    assert create.extract[0].expression == "$.run_id"
    assert sum(len(step.extract) for step in workflow.steps) == 1
    assert workflow.state_probe_steps == [before_probe.id, after_probe.id]
    assert create.id not in workflow.state_probe_steps
    assert redeem.role == "action"
    assert before_probe.depends_on == [create.id]
    assert redeem.depends_on == [before_probe.id, create.id]
    assert after_probe.depends_on == [redeem.id, create.id]
    _assert_references_are_complete(workflow)
    assert Workflow.model_validate(workflow.model_dump(mode="json")) == workflow

    serialized = workflow.model_dump_json()
    for recorded_id in (OLD_RECORDED_ID, TARGET_RECORDED_ID):
        assert recorded_id not in serialized
        assert all(recorded_id not in step.request.path for step in workflow.steps)
        assert all(recorded_id not in step.id for step in workflow.steps)
        assert all(
            recorded_id not in dependency
            for step in workflow.steps
            for dependency in step.depends_on
        )
        assert all(recorded_id not in probe for probe in workflow.state_probe_steps)


def test_cli_import_excludes_noisy_entries_from_options_file(tmp_path: Path) -> None:
    options_path = tmp_path / "capture-options.json"
    output_path = tmp_path / "workflow.json"
    options_path.write_text(json.dumps(NOISY_OPTIONS), encoding="utf-8")

    import_result = CliRunner().invoke(
        app,
        [
            "workflow",
            "import",
            str(NOISY_FIXTURE),
            "--plugin",
            "har.capture",
            "--options",
            str(options_path),
            "--output",
            str(output_path),
        ],
    )

    assert import_result.exit_code == 0, import_result.output
    workflow = Workflow.model_validate_json(output_path.read_text(encoding="utf-8"))
    assert len(workflow.steps) == 4
    assert [step.role for step in workflow.steps] == [
        "setup",
        "probe",
        "action",
        "probe",
    ]
    assert [item.name for step in workflow.steps for item in step.extract] == ["run_id"]
    assert OLD_RECORDED_ID not in workflow.model_dump_json()
    assert TARGET_RECORDED_ID not in workflow.model_dump_json()

    validate_result = CliRunner().invoke(
        app,
        ["workflow", "validate", str(output_path)],
    )
    assert validate_result.exit_code == 0, validate_result.output


def test_unknown_option_remains_forbidden() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HarCaptureOptions.model_validate({"exclude_entries": [0]})
