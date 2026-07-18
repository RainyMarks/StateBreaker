from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonpath_ng.ext import parse as parse_jsonpath
from pydantic import ValidationError
from statebreaker.models import Workflow
from statebreaker.runtime import render_template

from statebreaker_har_capture.har_parser import parse_har
from statebreaker_har_capture.inference import (
    _looks_like_cookie_value,
    collect_response_candidates,
    infer_response_variables,
)
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions
from statebreaker_har_capture.plugin import HarCapturePlugin

FIXTURES = Path(__file__).parent / "fixtures"
DYNAMIC_FIXTURE = FIXTURES / "dynamic-response-values.har"
_MISSING = object()


def _entry(
    path: str,
    *,
    response_payload: Any = _MISSING,
    response_text: str | None = None,
    response_mime: str = "application/json",
    response_encoding: str | None = None,
    query: list[tuple[str, str]] | None = None,
    json_body: Any = _MISSING,
    form_body: list[tuple[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "method": "POST" if json_body is not _MISSING or form_body else "GET",
        "url": f"https://capture.example.test{path}",
        "headers": [{"name": name, "value": value} for name, value in (headers or {}).items()],
    }
    if query is not None:
        request["queryString"] = [{"name": name, "value": value} for name, value in query]
    if json_body is not _MISSING:
        request["postData"] = {
            "mimeType": "application/json",
            "text": json.dumps(json_body),
        }

    elif form_body is not None:
        request["postData"] = {
            "mimeType": "application/x-www-form-urlencoded",
            "params": [{"name": name, "value": value} for name, value in form_body],
        }

    entry: dict[str, Any] = {"_resourceType": "xhr", "request": request}
    if response_payload is not _MISSING or response_text is not None:
        content: dict[str, Any] = {"mimeType": response_mime}
        content["text"] = (
            json.dumps(response_payload) if response_payload is not _MISSING else response_text
        )
        if response_encoding is not None:
            content["encoding"] = response_encoding
        entry["response"] = {"content": content}
    return entry


def _document(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"log": {"version": "1.2", "entries": list(entries)}}


def _normalize(
    *entries: dict[str, Any],
    options: HarCaptureOptions | None = None,
) -> dict[str, Any]:
    return normalize_har(_document(*entries), options or HarCaptureOptions())


def test_fixture_infers_extractors_and_replaces_all_supported_locations() -> None:
    document = parse_har(DYNAMIC_FIXTURE)
    candidate = normalize_har(document, HarCaptureOptions())
    producer, json_consumer, form_consumer = candidate["steps"]

    assert {extractor["name"]: extractor["expression"] for extractor in producer["extract"]} == {
        "run_id": "$.run_id",
        "order_id": "$.data.order_id",
        "item_id": "$.items[0].id",
        "special_key": '$["special-key"]',
        "large_count": "$.large_count",
        "form_value": "$.form_value",
        "form_list_id": "$.form_list_id",
    }
    assert all(extractor["kind"] == "jsonpath" for extractor in producer["extract"])
    assert all(extractor["required"] is True for extractor in producer["extract"])

    assert json_consumer["request"]["path"] == "/api/runs/${run_id}/state"
    assert json_consumer["request"]["query"] == {
        "order": "${order_id}",
        "item": ["${item_id}", "fixed"],
    }
    assert json_consumer["request"]["json_body"] == {
        "special_reference": "${special_key}",
        "count": "${large_count}",
        "token": "order-fictional-987654321",
    }
    assert form_consumer["request"]["path"] == "/api/runs/${run_id}/confirm"
    assert form_consumer["request"]["form_body"] == {
        "form_ref": "${form_value}",
        "list_ref": ["${form_list_id}", "fixed"],
    }

    raw_headers = json_consumer["request"]["headers"]
    assert raw_headers["authorization"] == "run-fictional-abc12345"
    assert raw_headers["cookie"] == "run-fictional-abc12345"
    assert raw_headers["x-run-reference"] == "run-fictional-abc12345"

    assert json_consumer["depends_on"] == [producer["id"]]
    assert form_consumer["depends_on"] == [json_consumer["id"], producer["id"]]
    assert producer["role"] == "action"
    assert candidate["variables"] == {}
    Workflow.model_validate(candidate)


def test_generated_jsonpaths_parse_and_select_exactly_one_value() -> None:
    document = parse_har(DYNAMIC_FIXTURE)
    response_text = document["log"]["entries"][0]["response"]["content"]["text"]
    response_json = json.loads(response_text)
    candidate = normalize_har(document, HarCaptureOptions())

    for extractor in candidate["steps"][0]["extract"]:
        matches = parse_jsonpath(extractor["expression"]).find(response_json)
        assert len(matches) == 1


def test_inference_is_deterministic_and_does_not_modify_object_or_file() -> None:
    before_file = DYNAMIC_FIXTURE.read_bytes()
    document = parse_har(DYNAMIC_FIXTURE)
    original = deepcopy(document)

    first = normalize_har(document, HarCaptureOptions())
    second = normalize_har(document, HarCaptureOptions())

    assert first == second
    assert document == original
    assert DYNAMIC_FIXTURE.read_bytes() == before_file


@pytest.mark.asyncio
async def test_plugin_defaults_to_inference_and_direct_api_can_disable_it() -> None:
    plugin = HarCapturePlugin()

    inferred = await plugin.capture(DYNAMIC_FIXTURE, {})
    disabled = await plugin.capture(DYNAMIC_FIXTURE, {"infer_response_variables": False})

    assert inferred.steps[0].extract
    assert "${run_id}" in inferred.steps[1].request.path
    assert all(not step.extract for step in disabled.steps)
    assert disabled.steps[1].request.path.endswith("/run-fictional-abc12345/state")


def test_inference_option_is_strict_and_enabled_by_default() -> None:
    assert HarCaptureOptions().infer_response_variables is True
    assert HarCaptureOptions(infer_response_variables=False).infer_response_variables is False
    with pytest.raises(ValidationError, match="Input should be a valid boolean"):
        HarCaptureOptions.model_validate({"infer_response_variables": 0})


@pytest.mark.parametrize(
    "mime_type",
    [
        "Application/JSON; charset=UTF-8",
        "application/vnd.example+json; version=1",
    ],
)
def test_json_response_mime_variants_produce_candidates(mime_type: str) -> None:
    value = "mime-fictional-12345"
    producer = _entry(
        "/create",
        response_payload={"run_id": value},
        response_mime=mime_type,
    )

    candidates = collect_response_candidates(0, "producer", producer)

    assert [(item.json_path, item.value) for item in candidates] == [("$.run_id", value)]


@pytest.mark.parametrize(
    "response",
    [
        None,
        "not-an-object",
        {"content": {"mimeType": "application/json", "text": 12345}},
        {"content": {"mimeType": "text/plain", "text": "not-json-fictional"}},
        {
            "content": {
                "mimeType": "application/json",
                "text": '{"run_id":"encoding-fictional-12345"}',
                "encoding": [],
            }
        },
        {
            "content": {
                "mimeType": "application/json",
                "text": '{"private":"FICTIONAL-INVALID-BODY"',
            }
        },
        {
            "content": {
                "mimeType": "application/json",
                "text": "%%%INVALID-BASE64%%%",
                "encoding": "base64",
            }
        },
        {
            "content": {
                "mimeType": "application/json",
                "text": '{"run_id":"truncated-fictional-12345"}',
                "_truncated": True,
            }
        },
    ],
)
def test_missing_or_unusable_responses_are_skipped_without_leaking_body(
    response: Any,
) -> None:
    value = "dynamic-fictional-12345"
    producer = _entry("/create")
    if response is not None:
        producer["response"] = response
    consumer = _entry(f"/use/{value}")

    candidate = _normalize(producer, consumer)

    assert all(not step["extract"] for step in candidate["steps"])
    assert candidate["steps"][1]["request"]["path"] == f"/use/{value}"
    assert "FICTIONAL-INVALID-BODY" not in repr(candidate)
    Workflow.model_validate(candidate)


def test_base64_json_is_strictly_decoded_and_unknown_encoding_is_skipped() -> None:
    value = "base64-fictional-12345"
    encoded = base64.b64encode(json.dumps({"run_id": value}).encode()).decode()
    valid = _entry(
        "/valid",
        response_text=encoded,
        response_encoding="base64",
    )
    unknown = _entry(
        "/unknown",
        response_text=json.dumps({"other_id": "unknown-fictional-67890"}),
        response_encoding="rot13",
    )
    consumer = _entry(f"/use/{value}")

    candidate = _normalize(valid, unknown, consumer)

    assert candidate["steps"][0]["extract"][0]["expression"] == "$.run_id"
    assert not candidate["steps"][1]["extract"]
    assert candidate["steps"][2]["request"]["path"] == "/use/${run_id}"


def test_short_common_boolean_float_and_small_integer_values_are_skipped() -> None:
    payload = {
        "short": "ABC123",
        "status": "completed",
        "small": 200,
        "enabled": True,
        "ratio": 1234.5,
        "missing": None,
        "coupon": "COUPON2024",
    }
    producer = _entry("/create", response_payload=payload)
    consumer = _entry(
        "/use/ABC123",
        query=[
            ("status", "completed"),
            ("small", "200"),
            ("enabled", "true"),
            ("ratio", "1234.5"),
            ("coupon", "COUPON2024"),
        ],
    )

    candidate = _normalize(producer, consumer)

    assert not candidate["steps"][0]["extract"]
    assert "${" not in repr(candidate)


def test_duplicate_value_paths_in_one_response_are_ambiguous() -> None:
    value = "duplicate-fictional-12345"
    producer = _entry(
        "/create",
        response_payload={"first": value, "second": value},
    )
    consumer = _entry(f"/use/{value}")

    candidate = _normalize(producer, consumer)

    assert not candidate["steps"][0]["extract"]
    assert candidate["steps"][1]["request"]["path"] == f"/use/{value}"


def test_duplicate_value_from_multiple_prior_responses_is_ambiguous() -> None:
    value = "duplicate-fictional-67890"
    first = _entry("/first", response_payload={"first_id": value})
    second = _entry("/second", response_payload={"second_id": value})
    consumer = _entry(f"/use/{value}")

    candidate = _normalize(first, second, consumer)

    assert all(not step["extract"] for step in candidate["steps"])
    assert candidate["steps"][2]["request"]["path"] == f"/use/{value}"


def test_established_variable_survives_later_response_echo() -> None:
    document = parse_har(DYNAMIC_FIXTURE)
    candidate = normalize_har(document, HarCaptureOptions())

    run_extractors = [
        extractor for extractor in candidate["steps"][0]["extract"] if extractor["name"] == "run_id"
    ]
    assert len(run_extractors) == 1
    assert candidate["steps"][2]["request"]["path"] == ("/api/runs/${run_id}/confirm")


def test_sensitive_paths_values_and_consumer_fields_are_never_inferred() -> None:
    safe_value = "safe-fictional-12345"
    payload = {
        "password": "password-fictional-12345",
        "accessToken": "access-fictional-12345",
        "api-key": "api-key-fictional-12345",
        "clientApiKeyValue": "client-key-fictional-12345",
        "refresh-token": "refresh-fictional-12345",
        "secret": "secret-fictional-12345",
        "sessionId": "session-fictional-12345",
        "csrfValue": "csrf-fictional-12345",
        "safe_bearer": "Bearer FICTIONAL-CREDENTIAL-12345",
        "safe_jwt": "abcdefgh.ijklmnop.qrstuvwx",
        "safe_cookie": "fictional=COOKIE-FICTIONAL-12345",
        "private_material": "-----BEGIN PRIVATE KEY-----FICTIONAL-----END PRIVATE KEY-----",
        "display_value": "fictional composite value",
        "safe_id": safe_value,
    }
    producer = _entry("/create", response_payload=payload)
    candidates = collect_response_candidates(0, "producer", producer)

    assert [(candidate.json_path, candidate.value) for candidate in candidates] == [
        ("$.safe_id", safe_value)
    ]

    consumer = _entry(
        "/use",
        query=[
            ("bearer", payload["safe_bearer"]),
            ("jwt", payload["safe_jwt"]),
            ("cookie_text", payload["safe_cookie"]),
            ("sessionId", safe_value),
        ],
        json_body={"token": safe_value},
        headers={
            "Authorization": safe_value,
            "Cookie": safe_value,
            "X-Fictional": safe_value,
        },
    )
    candidate = _normalize(producer, consumer)
    request = candidate["steps"][1]["request"]

    assert not candidate["steps"][0]["extract"]
    assert request["json_body"]["token"] == safe_value
    assert request["headers"]["authorization"] == safe_value
    assert request["headers"]["cookie"] == safe_value
    assert request["headers"]["x-fictional"] == safe_value
    assert request["query"]["sessionId"] == safe_value


def test_encoded_and_composite_path_values_are_not_replaced() -> None:
    plain = "dynamic-fictional-12345"
    encoded = "dynamic fictional 67890"
    producer = _entry(
        "/create",
        response_payload={"plain_id": plain, "encoded_id": encoded},
    )
    composite_consumer = _entry(f"/use/prefix-{plain}")
    encoded_consumer = _entry("/use/dynamic%20fictional%2067890")

    candidate = _normalize(producer, composite_consumer, encoded_consumer)

    assert not candidate["steps"][0]["extract"]
    assert candidate["steps"][1]["request"]["path"].endswith(f"/prefix-{plain}")
    assert candidate["steps"][2]["request"]["path"].endswith("/dynamic%20fictional%2067890")


def test_future_response_is_never_used_as_a_producer() -> None:
    value = "future-fictional-12345"
    early_consumer = _entry(f"/use/{value}")
    future_producer = _entry("/create", response_payload={"run_id": value})

    candidate = _normalize(early_consumer, future_producer)

    assert all(not step["extract"] for step in candidate["steps"])
    assert candidate["steps"][0]["request"]["path"] == f"/use/{value}"


def test_variable_name_conflicts_are_resolved_stably() -> None:
    first_value = "run-fictional-first-12345"
    second_value = "run-fictional-second-67890"
    producer = _entry(
        "/create",
        response_payload={
            "data": {"run_id": first_value},
            "result": {"run_id": second_value},
        },
    )
    consumer = _entry(
        "/use",
        json_body={
            "first_reference": first_value,
            "second_reference": second_value,
        },
    )

    first = _normalize(producer, consumer)
    second = _normalize(producer, consumer)

    assert [item["name"] for item in first["steps"][0]["extract"]] == [
        "run_id",
        "result_run_id",
    ]
    assert first["steps"][1]["request"]["json_body"] == {
        "first_reference": "${run_id}",
        "second_reference": "${result_run_id}",
    }
    assert first == second
    Workflow.model_validate(first)


def test_existing_variable_name_conflict_uses_stable_parent_path() -> None:
    value = "run-fictional-existing-12345"
    producer = _entry(
        "/create",
        response_payload={"data": {"run_id": value}},
    )
    consumer = _entry(f"/use/{value}")
    baseline = _normalize(
        producer,
        consumer,
        options=HarCaptureOptions(infer_response_variables=False),
    )
    original_steps = deepcopy(baseline["steps"])

    inferred_steps = infer_response_variables(
        [(0, producer), (1, consumer)],
        baseline["steps"],
        existing_variable_names={"run_id"},
    )

    assert inferred_steps[0]["extract"] == [
        {
            "name": "data_run_id",
            "kind": "jsonpath",
            "expression": "$.data.run_id",
            "required": True,
        }
    ]
    assert inferred_steps[1]["request"]["path"] == "/use/${data_run_id}"
    assert baseline["steps"] == original_steps
    baseline["variables"] = {"run_id": "preexisting-fictional"}
    baseline["steps"] = inferred_steps
    Workflow.model_validate(baseline)


def test_consumer_object_order_does_not_change_bindings_or_workflow() -> None:
    first_value = "run-fictional-first-12345"
    second_value = "run-fictional-second-67890"
    producer = _entry(
        "/create",
        response_payload={
            "data": {"run_id": first_value},
            "result": {"run_id": second_value},
        },
    )
    first = _normalize(
        producer,
        _entry(
            "/use",
            json_body={"first_reference": first_value, "second_reference": second_value},
        ),
    )
    second = _normalize(
        producer,
        _entry(
            "/use",
            json_body={"second_reference": second_value, "first_reference": first_value},
        ),
    )

    assert first["steps"][0]["extract"] == second["steps"][0]["extract"]
    assert {item["expression"]: item["name"] for item in first["steps"][0]["extract"]} == {
        "$.data.run_id": "run_id",
        "$.result.run_id": "result_run_id",
    }
    assert first["steps"][1]["request"]["json_body"] == second["steps"][1]["request"]["json_body"]
    assert first["steps"][1]["depends_on"] == second["steps"][1]["depends_on"]
    assert (
        Workflow.model_validate(first).model_dump() == Workflow.model_validate(second).model_dump()
    )


def test_non_adjacent_producer_dependencies_have_stable_workflow_order() -> None:
    first_value = "dependency-first-12345"
    second_value = "dependency-second-67890"
    entries = (
        _entry("/first", response_payload={"first_id": first_value}),
        _entry("/gap-one"),
        _entry("/second", response_payload={"second_id": second_value}),
        _entry("/gap-two"),
    )
    first = _normalize(
        *entries,
        _entry(
            "/consume",
            json_body={"first": first_value, "second": second_value},
        ),
    )
    second = _normalize(
        *entries,
        _entry(
            "/consume",
            json_body={"second": second_value, "first": first_value},
        ),
    )

    expected_dependencies = [
        first["steps"][3]["id"],
        first["steps"][0]["id"],
        first["steps"][2]["id"],
    ]
    assert first["steps"][4]["depends_on"] == expected_dependencies
    assert second["steps"][4]["depends_on"] == expected_dependencies
    assert first["steps"][0]["extract"] == second["steps"][0]["extract"]
    assert first["steps"][2]["extract"] == second["steps"][2]["extract"]
    assert (
        Workflow.model_validate(first).model_dump() == Workflow.model_validate(second).model_dump()
    )


@pytest.mark.parametrize(
    "cookie_value",
    [
        "session=abc12345",
        "foo=bar; Path=/; HttpOnly",
        "id=abc; SameSite=Lax; Secure",
        "id=abc; Partitioned; Priority=High",
        "session=abc12345;HttpOnly",
        "session=abc12345; HttpOnly",
        "foo=bar;Secure",
        "foo=bar; Secure",
        "foo=bar;Path=/;HttpOnly",
        "id=abc12345;SameSite=Lax;Secure",
        "a=b;c=d",
    ],
)
def test_cookie_attribute_forms_never_bind_or_create_extractors(
    cookie_value: str,
) -> None:
    assert _looks_like_cookie_value(cookie_value)
    producer = _entry("/create", response_payload={"display": cookie_value})
    candidate = _normalize(
        producer,
        _entry("/query", query=[("reference", cookie_value)]),
        _entry("/json", json_body={"reference": cookie_value}),
        _entry("/form", form_body=[("reference", cookie_value)]),
    )

    assert all(not step["extract"] for step in candidate["steps"])
    assert "${" not in repr(candidate)
    assert candidate["steps"][1]["request"]["query"]["reference"] == cookie_value
    assert candidate["steps"][2]["request"]["json_body"]["reference"] == cookie_value
    assert candidate["steps"][3]["request"]["form_body"]["reference"] == cookie_value
    assert cookie_value not in repr(candidate["steps"][0]["extract"])


def test_plain_semicolon_text_is_not_misclassified_as_cookie() -> None:
    assert not _looks_like_cookie_value("ordinary;text")
    assert not _looks_like_cookie_value("ordinary text; still ordinary")


def test_jsonpath_special_keys_skip_controls_and_match_exact_type() -> None:
    payload = {
        "single'quote": "single-fictional-12345",
        "back\\slash": "backslash-fictional-12345",
        "integer-key": 12345,
        "control\nkey": "control-fictional-12345",
    }
    producer = _entry("/create", response_payload=payload)

    candidates = collect_response_candidates(0, "producer", producer)

    assert {candidate.field_path for candidate in candidates} == {
        ("single'quote",),
        ("back\\slash",),
        ("integer-key",),
    }
    assert all(candidate.field_path != ("control\nkey",) for candidate in candidates)
    for candidate in candidates:
        matches = parse_jsonpath(candidate.json_path).find(payload)
        assert len(matches) == 1
        assert type(matches[0].value) is type(candidate.value)
        assert matches[0].value == candidate.value


def test_valid_base64_with_non_utf8_bytes_is_safely_skipped() -> None:
    encoded = base64.b64encode(b"\xff\xfe\x80fictional").decode("ascii")
    producer = _entry(
        "/create",
        response_text=encoded,
        response_encoding="base64",
    )

    assert collect_response_candidates(0, "producer", producer) == ()
    candidate = _normalize(producer, _entry("/use/non-utf8-fictional-12345"))
    assert all(not step["extract"] for step in candidate["steps"])
    assert encoded not in repr(candidate)


@pytest.mark.parametrize(
    "constant",
    [
        pytest.param("BUG50", id="bug-code"),
        pytest.param(50, id="coupon-50"),
        pytest.param(0, id="zero"),
        pytest.param(1, id="one"),
        pytest.param(2, id="two"),
        pytest.param(100, id="http-100"),
        pytest.param(200, id="http-200"),
        pytest.param("success", id="success"),
        pytest.param("active", id="active"),
        pytest.param("pending", id="pending"),
        pytest.param("ok", id="ok"),
        pytest.param("true", id="true-text"),
        pytest.param("false", id="false-text"),
        pytest.param(True, id="true-boolean"),
        pytest.param(False, id="false-boolean"),
        pytest.param("short", id="ordinary-short-string"),
    ],
)
def test_business_constants_are_not_response_candidates(constant: Any) -> None:
    producer = _entry("/create", response_payload={"value": constant})

    assert collect_response_candidates(0, "producer", producer) == ()


def test_path_replacement_preserves_empty_segments_and_skips_composites() -> None:
    value = "path-fictional-12345"
    producer = _entry("/create", response_payload={"run_id": value})
    candidate = _normalize(
        producer,
        _entry(f"//runs/{value}//"),
        _entry(f"/prefix-{value}/"),
        _entry("/encoded/path%2Dfictional%2D12345/"),
    )

    assert candidate["steps"][1]["request"]["path"] == "//runs/${run_id}//"
    assert candidate["steps"][2]["request"]["path"] == f"/prefix-{value}/"
    assert candidate["steps"][3]["request"]["path"] == ("/encoded/path%2Dfictional%2D12345/")


def test_runtime_full_value_template_restores_integer_type() -> None:
    rendered = render_template(
        {"coupon_value": "${integer_variable}"},
        {"integer_variable": 1234},
    )

    assert rendered == {"coupon_value": 1234}
    assert type(rendered["coupon_value"]) is int


def test_nested_sensitive_parent_paths_block_json_query_and_form_descendants() -> None:
    value = "sensitive-parent-fictional-12345"
    sensitive_paths = (
        "auth.token",
        "credentials.client_secret",
        "session.data.id",
        "csrf.payload.value",
    )
    producer = _entry("/create", response_payload={"safe_id": value})
    json_consumer = _entry(
        "/json",
        json_body={
            "auth": {"token": value},
            "credentials": {"client_secret": value},
            "session": {"data": {"id": value}},
            "csrf": {"payload": {"value": value}},
        },
    )
    query_consumer = _entry("/query", query=[(field_path, value) for field_path in sensitive_paths])
    form_consumer = _entry(
        "/form", form_body=[(field_path, value) for field_path in sensitive_paths]
    )

    candidate = _normalize(producer, json_consumer, query_consumer, form_consumer)

    assert all(not step["extract"] for step in candidate["steps"])
    assert "${" not in repr(candidate)
    assert candidate["steps"][1]["request"]["json_body"] == {
        "auth": {"token": value},
        "credentials": {"client_secret": value},
        "session": {"data": {"id": value}},
        "csrf": {"payload": {"value": value}},
    }
    assert candidate["steps"][2]["request"]["query"] == {
        field_path: value for field_path in sensitive_paths
    }
    assert candidate["steps"][3]["request"]["form_body"] == {
        field_path: value for field_path in sensitive_paths
    }
