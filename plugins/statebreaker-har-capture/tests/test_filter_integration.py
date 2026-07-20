from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.har_parser import parse_har
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions
from statebreaker_har_capture.plugin import HarCapturePlugin

FIXTURES = Path(__file__).parent / "fixtures"
STATIC_FIXTURE = FIXTURES / "static-resources.har"


def test_filtering_preserves_relative_order_and_original_entry_step_ids() -> None:
    candidate = normalize_har(parse_har(STATIC_FIXTURE), HarCaptureOptions())

    assert [step["request"]["path"] for step in candidate["steps"]] == [
        "/api/orders",
        "/api/orders/fictional-42.js",
    ]
    assert candidate["steps"][0]["id"].startswith("step-0001-get-api-orders-")
    assert candidate["steps"][1]["id"].startswith(
        "step-0003-post-api-orders-fictional-42-js-"
    )
    assert candidate["steps"][1]["depends_on"] == [candidate["steps"][0]["id"]]
    assert all(step["extract"] == [] for step in candidate["steps"])


def test_retained_state_probe_uses_original_entry_to_step_mapping() -> None:
    candidate = normalize_har(
        parse_har(STATIC_FIXTURE),
        HarCaptureOptions(state_probe_entry_indices=[3]),
    )

    assert candidate["steps"][0]["role"] == "action"
    assert candidate["steps"][1]["role"] == "probe"
    assert candidate["state_probe_steps"] == [candidate["steps"][1]["id"]]
    assert candidate["state_probe_steps"][0].startswith(
        "step-0003-post-api-orders-fictional-42-js-"
    )


def test_filtered_state_probe_fails_with_safe_original_index_and_reason() -> None:
    document = parse_har(STATIC_FIXTURE)
    sensitive_fragments = [
        "https://capture.example.test/assets/fictional-banner.png",
        "FICTIONAL-QUERY",
        "Authorization",
        "Cookie",
        "FICTIONAL-STATIC-TOKEN",
        "FICTIONAL-STATIC-COOKIE",
    ]

    with pytest.raises(
        HarCaptureError,
        match=(
            r"HAR state probe error at entry 0: selected entry was filtered "
            r"as a static resource \(image MIME\)"
        ),
    ) as error:
        normalize_har(document, HarCaptureOptions(state_probe_entry_indices=[0]))

    rendered = str(error.value)
    assert all(fragment not in rendered for fragment in sensitive_fragments)


def test_all_filtered_entries_fail_without_request_disclosure() -> None:
    document = parse_har(STATIC_FIXTURE)
    entries = document["log"]["entries"]
    document["log"]["entries"] = [entries[0], entries[2], entries[4]]

    with pytest.raises(
        HarCaptureError,
        match=r"all entries were filtered.*no business requests",
    ) as error:
        normalize_har(document, HarCaptureOptions())

    rendered = str(error.value)
    assert "https://" not in rendered
    assert "/assets/" not in rendered
    assert "Authorization" not in rendered
    assert "Cookie" not in rendered
    assert "FICTIONAL-" not in rendered


def test_filter_can_be_disabled_without_changing_original_indices() -> None:
    candidate = normalize_har(
        parse_har(STATIC_FIXTURE),
        HarCaptureOptions(filter_static_resources=False),
    )

    assert len(candidate["steps"]) == 5
    assert [
        step["id"].split("-", maxsplit=2)[1] for step in candidate["steps"]
    ] == ["0000", "0001", "0002", "0003", "0004"]


def test_filter_option_is_strict_enabled_by_default_and_can_be_disabled() -> None:
    assert HarCaptureOptions().filter_static_resources is True
    assert (
        HarCaptureOptions.model_validate({"filter_static_resources": False})
        .filter_static_resources
        is False
    )
    with pytest.raises(ValidationError, match="Input should be a valid boolean"):
        HarCaptureOptions.model_validate({"filter_static_resources": 0})


def test_normalizer_filtering_is_deterministic_and_does_not_mutate_document() -> None:
    document = parse_har(STATIC_FIXTURE)
    original = deepcopy(document)

    first = normalize_har(document, HarCaptureOptions())
    second = normalize_har(document, HarCaptureOptions())

    assert first == second
    assert document == original


@pytest.mark.asyncio
async def test_plugin_default_filters_and_does_not_modify_har_file() -> None:
    before = STATIC_FIXTURE.read_bytes()

    workflow = await HarCapturePlugin().capture(STATIC_FIXTURE, {})

    assert [step.request.path for step in workflow.steps] == [
        "/api/orders",
        "/api/orders/fictional-42.js",
    ]
    assert STATIC_FIXTURE.read_bytes() == before


@pytest.mark.asyncio
async def test_direct_plugin_api_can_disable_filtering() -> None:
    workflow = await HarCapturePlugin().capture(
        STATIC_FIXTURE, {"filter_static_resources": False}
    )

    assert len(workflow.steps) == 5
