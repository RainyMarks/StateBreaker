from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from statebreaker.models import Workflow

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.har_parser import parse_har
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions

FIXTURES = Path(__file__).parent / "fixtures"
STATIC_FIXTURE = FIXTURES / "static-resources.har"
COUPON_RACE_FIXTURE = FIXTURES / "coupon-race-normal.har"
RECORDED_RUN_ID = "recordedrunid000000000000000001"


def test_setup_entry_indices_default_to_empty_and_accept_multiple_indices() -> None:
    assert HarCaptureOptions().setup_entry_indices == []
    assert HarCaptureOptions.model_validate(
        {"setup_entry_indices": [0, 2]}
    ).setup_entry_indices == [0, 2]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([-1], "non-negative"),
        ([0, 0], "must not contain duplicates"),
        (["0"], "valid integer"),
        ([True], "valid integer"),
        ((0,), "valid list"),
    ],
)
def test_setup_entry_indices_are_strict(
    value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        HarCaptureOptions.model_validate({"setup_entry_indices": value})


def test_setup_and_probe_indices_must_not_overlap() -> None:
    with pytest.raises(
        ValidationError,
        match=r"role index conflict.*original entry indices \[0\]",
    ):
        HarCaptureOptions.model_validate(
            {
                "setup_entry_indices": [0],
                "state_probe_entry_indices": [0, 1],
            }
        )


def test_single_setup_and_probe_roles_are_explicit() -> None:
    candidate = normalize_har(
        parse_har(FIXTURES / "minimal.har"),
        HarCaptureOptions(
            setup_entry_indices=[0],
            state_probe_entry_indices=[1],
        ),
    )

    assert [step["role"] for step in candidate["steps"]] == ["setup", "probe"]
    assert candidate["state_probe_steps"] == [candidate["steps"][1]["id"]]
    assert candidate["steps"][0]["id"] not in candidate["state_probe_steps"]


def test_multiple_setup_indices_do_not_create_state_probes() -> None:
    candidate = normalize_har(
        parse_har(FIXTURES / "minimal.har"),
        HarCaptureOptions(setup_entry_indices=[0, 1]),
    )

    assert [step["role"] for step in candidate["steps"]] == ["setup", "setup"]
    assert candidate["state_probe_steps"] == []


def test_setup_index_out_of_range_fails_safely() -> None:
    with pytest.raises(
        HarCaptureError,
        match=r"HAR setup role error at entry 9: index is out of range for 2 entries",
    ):
        normalize_har(
            parse_har(FIXTURES / "minimal.har"),
            HarCaptureOptions(setup_entry_indices=[9]),
        )


def test_filtered_setup_index_fails_with_safe_original_index_and_reason() -> None:
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
            r"HAR setup role error at entry 0: selected entry was filtered "
            r"as a static resource \(image MIME\)"
        ),
    ) as error:
        normalize_har(document, HarCaptureOptions(setup_entry_indices=[0]))

    assert all(fragment not in str(error.value) for fragment in sensitive_fragments)


def test_retained_setup_role_uses_original_index_after_filtering() -> None:
    candidate = normalize_har(
        parse_har(STATIC_FIXTURE),
        HarCaptureOptions(setup_entry_indices=[3]),
    )

    assert [step["role"] for step in candidate["steps"]] == ["action", "setup"]
    assert candidate["steps"][1]["id"].startswith(
        "step-0003-post-api-orders-fictional-42-js-"
    )
    assert candidate["state_probe_steps"] == []


def test_filter_disabled_preserves_multiple_setup_roles_and_probe_role() -> None:
    candidate = normalize_har(
        parse_har(STATIC_FIXTURE),
        HarCaptureOptions(
            filter_static_resources=False,
            setup_entry_indices=[0, 2],
            state_probe_entry_indices=[4],
        ),
    )

    assert [step["role"] for step in candidate["steps"]] == [
        "setup",
        "action",
        "setup",
        "action",
        "probe",
    ]
    assert candidate["state_probe_steps"] == [candidate["steps"][4]["id"]]


def test_roles_are_preserved_when_response_inference_is_disabled() -> None:
    candidate = normalize_har(
        parse_har(COUPON_RACE_FIXTURE),
        HarCaptureOptions(
            infer_response_variables=False,
            setup_entry_indices=[0],
            state_probe_entry_indices=[1, 3],
        ),
    )

    assert [step["role"] for step in candidate["steps"]] == [
        "setup",
        "probe",
        "action",
        "probe",
    ]
    assert all(step["extract"] == [] for step in candidate["steps"])


def test_roles_survive_response_inference_and_step_id_stabilization() -> None:
    candidate = normalize_har(
        parse_har(COUPON_RACE_FIXTURE),
        HarCaptureOptions(
            setup_entry_indices=[0],
            state_probe_entry_indices=[1, 3],
        ),
    )

    assert [step["role"] for step in candidate["steps"]] == [
        "setup",
        "probe",
        "action",
        "probe",
    ]
    assert candidate["steps"][0]["extract"] == [
        {
            "name": "run_id",
            "kind": "jsonpath",
            "expression": "$.run_id",
            "required": True,
        }
    ]
    assert [step["request"]["path"] for step in candidate["steps"][1:]] == [
        "/api/runs/${run_id}/state",
        "/api/runs/${run_id}/redeem",
        "/api/runs/${run_id}/state",
    ]
    assert all(RECORDED_RUN_ID not in step["id"] for step in candidate["steps"])
    Workflow.model_validate(candidate)


def test_first_post_with_extractor_remains_action_without_setup_option() -> None:
    candidate = normalize_har(
        parse_har(COUPON_RACE_FIXTURE),
        HarCaptureOptions(state_probe_entry_indices=[1, 3]),
    )

    assert candidate["steps"][0]["request"]["method"] == "POST"
    assert candidate["steps"][0]["extract"]
    assert candidate["steps"][0]["role"] == "action"


def test_explicit_roles_are_deterministic_and_do_not_mutate_har() -> None:
    document = parse_har(COUPON_RACE_FIXTURE)
    original = deepcopy(document)
    options = HarCaptureOptions(
        setup_entry_indices=[0],
        state_probe_entry_indices=[1, 3],
    )

    first = normalize_har(document, options)
    second = normalize_har(document, options)

    assert first == second
    assert document == original
