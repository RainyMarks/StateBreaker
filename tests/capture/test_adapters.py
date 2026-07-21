"""Capture adapter contracts: HAR, Postman, OpenAPI (normal + error cases)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from statebreaker.capture import (
    load_har,
    load_openapi,
    load_postman,
    parse_har,
    parse_openapi,
    parse_postman,
)
from statebreaker.errors import CaptureError

# --- HAR --------------------------------------------------------------------


def _har_document() -> dict[str, object]:
    return {
        "log": {
            "version": "1.2",
            "entries": [
                {
                    "startedDateTime": "2026-01-01T00:00:00.000Z",
                    "timings": {"wait": 12, "receive": 3},
                    "request": {
                        "method": "GET",
                        "url": "http://127.0.0.1:8080/api/items?page=2",
                        "headers": [{"name": "Accept", "value": "application/json"}],
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Content-Type", "value": "application/json"}],
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps({"items": [1, 2], "page": 2}),
                        },
                    },
                },
                {
                    "startedDateTime": "2026-01-01T00:00:01.000Z",
                    "request": {
                        "method": "POST",
                        "url": "http://127.0.0.1:8080/api/items",
                        "headers": [{"name": "Content-Type", "value": "application/json"}],
                        "postData": {
                            "mimeType": "application/json",
                            "text": json.dumps({"name": "widget"}),
                        },
                    },
                    "response": {
                        "status": 201,
                        "headers": [],
                        "content": {
                            "mimeType": "application/json",
                            "encoding": "base64",
                            "text": base64.b64encode(
                                json.dumps({"item": {"id": "abc-123"}}).encode()
                            ).decode(),
                        },
                    },
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "http://127.0.0.1:8080/api/login",
                        "headers": [],
                        "postData": {
                            "mimeType": "application/x-www-form-urlencoded",
                            "text": "user=alice&pass=secret",
                        },
                    },
                    "response": {"status": 200, "headers": [], "content": {}},
                },
            ],
        }
    }


def test_parse_har_entries() -> None:
    trace = parse_har(_har_document(), capture_id="cap-har")
    assert trace.source == "har"
    assert len(trace.exchanges) == 3

    get, post, form = trace.exchanges
    assert get.exchange_id == "har-1"
    assert get.method == "GET"
    assert get.response_body == {"items": [1, 2], "page": 2}
    assert get.response_body_encoding == "json"
    assert get.completed_at_ns - get.started_at_ns == 15_000_000

    assert post.request_body == {"name": "widget"}
    assert post.request_body_encoding == "json"
    assert post.response_body == {"item": {"id": "abc-123"}}

    assert form.request_body == {"user": "alice", "pass": "secret"}
    assert form.request_body_encoding == "form"
    assert form.started_at_ns == 0


def test_load_har_file_and_errors(tmp_path: Path) -> None:
    har_file = tmp_path / "flow.har"
    har_file.write_text(json.dumps(_har_document()), encoding="utf-8")
    trace = load_har(har_file)
    assert trace.capture_id == "flow"

    bad = tmp_path / "bad.har"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(CaptureError):
        load_har(bad)
    with pytest.raises(CaptureError):
        load_har(tmp_path / "missing.har")
    with pytest.raises(CaptureError):
        parse_har({"nope": True}, capture_id="x")


# --- Postman ----------------------------------------------------------------


def _postman_collection() -> dict[str, object]:
    return {
        "info": {"name": "demo"},
        "item": [
            {
                "name": "group",
                "item": [
                    {
                        "name": "create",
                        "request": {
                            "method": "POST",
                            "url": {
                                "protocol": "http",
                                "host": ["127", "0", "0", "1"],
                                "path": ["api", "things"],
                                "query": [
                                    {"key": "verbose", "value": "1"},
                                    {"key": "skip", "value": "x", "disabled": True},
                                ],
                            },
                            "header": [
                                {"key": "X-Trace", "value": "1"},
                                {"key": "X-Skip", "value": "0", "disabled": True},
                            ],
                            "body": {
                                "mode": "raw",
                                "raw": json.dumps({"name": "thing"}),
                                "options": {"raw": {"language": "json"}},
                            },
                        },
                    }
                ],
            },
            {
                "name": "form",
                "request": {
                    "method": "POST",
                    "url": "http://127.0.0.1:8080/api/form",
                    "body": {
                        "mode": "urlencoded",
                        "urlencoded": [
                            {"key": "a", "value": "1"},
                            {"key": "b", "value": "2", "disabled": True},
                        ],
                    },
                },
            },
        ],
    }


def test_parse_postman_nested_items() -> None:
    trace = parse_postman(_postman_collection(), capture_id="cap-pm")
    assert trace.source == "postman"
    assert len(trace.exchanges) == 2

    create, form = trace.exchanges
    assert create.exchange_id == "pm-1"
    assert create.url == "http://127.0.0.1/api/things?verbose=1"
    assert create.request_headers == {"x-trace": "1"}
    assert create.request_body == {"name": "thing"}
    assert create.request_body_encoding == "json"
    assert create.response_status == 0

    assert form.request_body == {"a": "1"}
    assert form.request_body_encoding == "form"


def test_load_postman_errors(tmp_path: Path) -> None:
    good = tmp_path / "collection.json"
    good.write_text(json.dumps(_postman_collection()), encoding="utf-8")
    assert load_postman(good).capture_id == "collection"

    with pytest.raises(CaptureError):
        load_postman(tmp_path / "missing.json")
    with pytest.raises(CaptureError):
        parse_postman({"item": "not-a-list"}, capture_id="x")


# --- OpenAPI ----------------------------------------------------------------


def _openapi_document() -> dict[str, object]:
    return {
        "openapi": "3.0.3",
        "paths": {
            "/things/{thingId}": {
                "parameters": [
                    {
                        "name": "thingId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {
                    "operationId": "readThing",
                    "parameters": [
                        {
                            "name": "verbose",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ThingInput"}
                            }
                        }
                    },
                    "responses": {"200": {"description": "ok"}},
                },
            }
        },
        "components": {
            "schemas": {
                "ThingInput": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "count": {"type": "integer"},
                        "flag": {"type": "boolean"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                }
            }
        },
    }


def test_parse_openapi_templates() -> None:
    templates = parse_openapi(_openapi_document())
    assert len(templates) == 2

    read = next(t for t in templates if t.template_id == "readThing")
    assert read.method == "GET"
    assert read.path_template == "/things/${thingId}"
    assert read.query == {"verbose": "${verbose}"}

    create = next(t for t in templates if t.method == "POST")
    assert create.template_id == "post-things-thingId"
    assert create.body_encoding == "json"
    assert create.body == {"name": "", "count": 0, "flag": False, "tags": [""]}


def test_load_openapi_and_errors(tmp_path: Path) -> None:
    spec = tmp_path / "api.yaml"
    spec.write_text(json.dumps(_openapi_document()), encoding="utf-8")
    templates = load_openapi(spec)
    assert len(templates) == 2

    empty = tmp_path / "empty.yaml"
    empty.write_text("openapi: 3.0.3\npaths: {}\n", encoding="utf-8")
    with pytest.raises(CaptureError):
        load_openapi(empty)
    with pytest.raises(CaptureError):
        load_openapi(tmp_path / "missing.yaml")
