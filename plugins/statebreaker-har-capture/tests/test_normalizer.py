from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from statebreaker.models import Workflow

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.har_parser import parse_har
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions

FIXTURES = Path(__file__).parent / "fixtures"


def _candidate(options: HarCaptureOptions | None = None) -> dict:
    document = parse_har(FIXTURES / "minimal.har")
    return normalize_har(document, options or HarCaptureOptions())


def test_minimal_requests_preserve_order_and_form_linear_chain() -> None:
    candidate = _candidate()

    assert candidate["base_url"] == "https://example.test/"
    assert [step["request"]["method"] for step in candidate["steps"]] == ["GET", "DELETE"]
    assert candidate["steps"][0]["request"]["path"] == "/api/runs"
    assert candidate["steps"][0]["request"]["query"] == {
        "status": ["open", "pending"],
        "empty": "",
    }
    assert candidate["steps"][1]["request"]["query"] == {"confirm": "yes"}
    assert candidate["steps"][0]["depends_on"] == []
    assert candidate["steps"][1]["depends_on"] == [candidate["steps"][0]["id"]]


def test_normalization_is_deterministic_and_non_mutating() -> None:
    document = parse_har(FIXTURES / "minimal.har")
    original = deepcopy(document)

    first = normalize_har(document, HarCaptureOptions())
    second = normalize_har(document, HarCaptureOptions())

    assert first == second
    assert document == original
    assert first["steps"][0]["id"].startswith("step-0000-get-api-runs-")


def test_credentials_are_preserved_for_replay_and_headers_are_lowercase() -> None:
    headers = _candidate()["steps"][0]["request"]["headers"]

    assert headers == {
        "authorization": "Bearer TEST-SECRET-DO-NOT-USE",
        "cookie": "session=TEST-COOKIE-DO-NOT-USE",
        "x-trace": "fixture",
    }


def test_credentials_can_be_explicitly_stripped() -> None:
    candidate = _candidate(HarCaptureOptions(strip_credentials=True))
    headers = candidate["steps"][0]["request"]["headers"]

    assert headers == {"x-trace": "fixture"}
    assert "TEST-SECRET-DO-NOT-USE" not in repr(candidate)
    assert "TEST-COOKIE-DO-NOT-USE" not in repr(candidate)


def test_duplicate_retained_header_is_rejected() -> None:
    document = parse_har(FIXTURES / "minimal.har")
    document["log"]["entries"][0]["request"]["headers"].append(
        {"name": "x-TRACE", "value": "duplicate"}
    )

    with pytest.raises(HarCaptureError, match=r"header error at entry 0.*duplicate"):
        normalize_har(document, HarCaptureOptions())


@pytest.mark.parametrize(
    ("query_item", "sensitive_value"),
    [
        ({"name": 123, "value": "TEST-INVALID-NAME-VALUE"}, "TEST-INVALID-NAME-VALUE"),
        (
            {"name": "token", "value": ["TEST-INVALID-QUERY-VALUE"]},
            "TEST-INVALID-QUERY-VALUE",
        ),
    ],
)
def test_query_string_requires_string_fields_without_leaking_values(
    query_item: dict, sensitive_value: str
) -> None:
    document = parse_har(FIXTURES / "minimal.har")
    document["log"]["entries"][0]["request"]["queryString"] = [query_item]

    with pytest.raises(
        HarCaptureError, match=r"query error at entry 0.*requires string name and value"
    ) as error:
        normalize_har(document, HarCaptureOptions())

    assert sensitive_value not in str(error.value)


def test_json_request_body_is_normalized() -> None:
    document = parse_har(FIXTURES / "minimal.har")
    document["log"]["entries"][0]["request"]["method"] = "POST"
    document["log"]["entries"][0]["request"]["postData"] = {
        "mimeType": "application/json; charset=utf-8",
        "text": '{"coupon_code":"BUG50","quantity":2}',
    }

    candidate = normalize_har(document, HarCaptureOptions())

    assert candidate["steps"][0]["request"]["json_body"] == {
        "coupon_code": "BUG50",
        "quantity": 2,
    }
    assert "form_body" not in candidate["steps"][0]["request"]
    Workflow.model_validate(candidate)


def test_form_request_body_preserves_repeated_fields() -> None:
    document = parse_har(FIXTURES / "minimal.har")
    document["log"]["entries"][0]["request"]["method"] = "POST"
    document["log"]["entries"][0]["request"]["postData"] = {
        "mimeType": "application/x-www-form-urlencoded",
        "params": [
            {"name": "scope", "value": "read"},
            {"name": "scope", "value": "write"},
            {"name": "empty", "value": ""},
        ],
    }

    candidate = normalize_har(document, HarCaptureOptions())

    assert candidate["steps"][0]["request"]["form_body"] == {
        "scope": ["read", "write"],
        "empty": "",
    }
    assert "json_body" not in candidate["steps"][0]["request"]
    Workflow.model_validate(candidate)


def test_invalid_json_body_is_rejected_without_leaking_body() -> None:
    document = parse_har(FIXTURES / "minimal.har")
    secret_body = '{"token":"TEST-SECRET-BODY"'
    document["log"]["entries"][0]["request"]["postData"] = {
        "mimeType": "application/json",
        "text": secret_body,
    }

    with pytest.raises(HarCaptureError, match="contains invalid JSON") as error:
        normalize_har(document, HarCaptureOptions())

    assert secret_body not in str(error.value)


def test_unsupported_raw_request_body_is_explicitly_rejected() -> None:
    document = parse_har(FIXTURES / "minimal.har")
    document["log"]["entries"][0]["request"]["postData"] = {
        "mimeType": "text/plain",
        "text": "not emitted",
    }

    with pytest.raises(HarCaptureError, match="unsupported request body content type"):
        normalize_har(document, HarCaptureOptions())


def test_cross_origin_request_is_rejected() -> None:
    document = parse_har(FIXTURES / "minimal.har")
    document["log"]["entries"][1]["request"]["url"] = "https://other.test/path"

    with pytest.raises(HarCaptureError, match=r"origin error at entry 1"):
        normalize_har(document, HarCaptureOptions())
