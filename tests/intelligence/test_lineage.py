"""Offline unit tests for lineage, value classification, and templating."""

from __future__ import annotations

import base64

from statebreaker.intelligence.lineage import infer_bindings, iter_json_leaves
from statebreaker.intelligence.templates import build_templates
from statebreaker.intelligence.value_types import classify_value
from statebreaker.models.capture import HttpExchange


def _exchange(
    exchange_id: str,
    method: str,
    url: str,
    *,
    request_body: object = None,
    request_headers: dict[str, str] | None = None,
    response_body: object = None,
    response_status: int = 200,
) -> HttpExchange:
    return HttpExchange(
        exchange_id=exchange_id,
        method=method,
        url=url,
        request_headers=request_headers or {},
        request_body=request_body,
        request_body_encoding="json" if request_body is not None else "none",
        response_status=response_status,
        response_body=response_body,
        response_body_encoding="json" if response_body is not None else "none",
    )


def test_classify_value() -> None:
    assert classify_value("3f2c9e1a-1234-4abc-9abc-1234567890ab") == "uuid"
    assert classify_value("12345") == "numeric_id"
    assert classify_value("a" * 32) == "token"
    assert classify_value("2026-01-01T10:00:00Z") == "timestamp"
    assert classify_value("user@example.com") == "email"
    assert classify_value("active") == "enum"
    assert classify_value(50) == "small_number"
    assert classify_value(1000) == "numeric_id"


def test_lineage_finds_forward_flow_only() -> None:
    exchanges = [
        _exchange(
            "e1", "POST", "http://h/api/things", response_body={"thing": {"uid": "abc12345"}}
        ),
        _exchange("e2", "POST", "http://h/api/things/abc12345/act"),
        _exchange("e3", "GET", "http://h/api/things/abc12345"),
    ]
    bindings = infer_bindings(exchanges)
    assert {(b.consumer_exchange_id) for b in bindings} == {"e2", "e3"}
    assert all(b.producer_exchange_id == "e1" for b in bindings)
    assert all(b.consumer_location == "path" for b in bindings)


def test_lineage_tracks_short_machine_identifiers() -> None:
    exchanges = [
        _exchange("e1", "POST", "http://h/api/items", response_body={"id": "o-1"}),
        _exchange("e2", "POST", "http://h/api/items/o-1/confirm"),
    ]

    bindings = infer_bindings(exchanges)

    assert len(bindings) == 1
    assert bindings[0].producer_exchange_id == "e1"
    assert bindings[0].consumer_location == "path"


def test_lineage_ignores_backward_and_noise() -> None:
    exchanges = [
        _exchange("e1", "GET", "http://h/api/things/zzz999"),  # consumes before produced
        _exchange("e2", "POST", "http://h/api/things", response_body={"uid": "zzz999"}),
        _exchange("e3", "GET", "http://h/api/state", response_body={"mode": "active"}),
        _exchange("e4", "POST", "http://h/api/other", request_body={"mode": "active"}),
    ]
    bindings = infer_bindings(exchanges)
    # e1 predates the producer: no binding. "active" is an enum: not tracked.
    assert bindings == []


def test_lineage_matches_base64_wrapped_value() -> None:
    token = "T0k3n-abc123"
    wrapped = base64.b64encode(token.encode()).decode()
    exchanges = [
        _exchange("e1", "POST", "http://h/api/mint", response_body={"ticket": token}),
        _exchange(
            "e2",
            "POST",
            "http://h/api/use",
            request_headers={"X-Ticket": wrapped},
        ),
    ]
    bindings = infer_bindings(exchanges)
    assert len(bindings) == 1
    assert bindings[0].consumer_location == "header:x-ticket"


def test_templates_substitute_variables() -> None:
    exchanges = [
        _exchange(
            "e1", "POST", "http://h/api/things", response_body={"thing": {"uid": "abc12345"}}
        ),
        _exchange(
            "e2",
            "POST",
            "http://h/api/things/abc12345/act?src=abc12345",
            request_body={"ref": "abc12345", "note": "keep"},
        ),
    ]
    bindings = infer_bindings(exchanges)
    templates = build_templates(exchanges, bindings)
    second = templates[1]
    assert second.path_template == "/api/things/${uid}/act"
    assert second.query == {"src": "${uid}"}
    assert second.body == {"ref": "${uid}", "note": "keep"}


def test_templates_harvest_form_variant_hints_from_html() -> None:
    exchanges = [
        _exchange(
            "page",
            "GET",
            "http://h/form",
            response_body=(
                '<form><select name="target">'
                '<option value="first">First</option>'
                '<option value="second">Second</option>'
                "</select></form>"
            ),
        ),
        _exchange(
            "submit",
            "POST",
            "http://h/run",
            request_body={"payload": '{"target":"first","amount":"100"}'},
        ),
    ]

    templates = build_templates(exchanges, [])

    assert templates[1].variant_hints == {"body.payload.target": ["first", "second"]}


def test_iter_json_leaves_paths() -> None:
    leaves = dict(iter_json_leaves({"a": {"b": [1, {"c": "x"}]}}))
    assert leaves == {"$.a.b[0]": 1, "$.a.b[1].c": "x"}
