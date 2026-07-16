from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from statebreaker.errors import TemplateError
from statebreaker.models import Workflow
from statebreaker.runtime import REDACTED, ExecutionRuntime, redact, render_template


def test_template_renderer_preserves_full_value_types() -> None:
    variables = {"count": 2, "user": {"id": "alice"}}

    assert render_template("${count}", variables) == 2
    assert render_template("/users/${user.id}", variables) == "/users/alice"
    assert render_template({"n": "${count}"}, variables) == {"n": 2}
    with pytest.raises(TemplateError, match="missing"):
        render_template("${missing}", variables)


def test_redaction_is_recursive_and_case_insensitive() -> None:
    result = redact(
        {
            "Authorization": "Bearer abc",
            "nested": {"password": "guess", "safe": "visible"},
            "Cookie": "sid=123",
        }
    )

    assert result["Authorization"] == REDACTED
    assert result["nested"]["password"] == REDACTED
    assert result["nested"]["safe"] == "visible"
    assert result["Cookie"] == REDACTED


@pytest.mark.asyncio
async def test_runtime_extracts_variables_logs_events_and_redacts(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/create":
            return httpx.Response(
                201,
                json={"run_id": "run-42"},
                headers={"X-Trace": "trace-7"},
            )
        if request.url.path == "/receipt/run-42":
            return httpx.Response(200, text="receipt=ABC123")
        if request.url.path == "/final/ABC123/trace-7":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    workflow = Workflow.model_validate(
        {
            "name": "extract-flow",
            "base_url": "http://example.test",
            "variables": {},
            "steps": [
                {
                    "id": "create",
                    "request": {
                        "method": "POST",
                        "path": "/create",
                        "headers": {"Authorization": "Bearer very-secret"},
                        "json_body": {"password": "also-secret"},
                    },
                    "extract": [
                        {"name": "run_id", "kind": "jsonpath", "expression": "$.run_id"},
                        {"name": "trace", "kind": "header", "expression": "X-Trace"},
                    ],
                },
                {
                    "id": "receipt",
                    "depends_on": ["create"],
                    "request": {"method": "GET", "path": "/receipt/${run_id}"},
                    "extract": [
                        {
                            "name": "receipt",
                            "kind": "regex",
                            "expression": "receipt=(?P<value>[A-Z0-9]+)",
                        }
                    ],
                },
                {
                    "id": "final",
                    "depends_on": ["receipt"],
                    "request": {
                        "method": "GET",
                        "path": "/final/${receipt}/${trace}",
                    },
                },
            ],
        }
    )

    async with ExecutionRuntime(
        workflow,
        output_root=tmp_path,
        transport=httpx.MockTransport(handler),
    ) as runtime:
        responses = await runtime.execute_workflow()
        assert [record.status_code for record in responses] == [201, 200, 200]
        assert runtime.variables == {
            "run_id": "run-42",
            "trace": "trace-7",
            "receipt": "ABC123",
        }
        assert [event.kind for event in runtime.events] == [
            "request.started",
            "request.completed",
            "request.started",
            "request.completed",
            "request.started",
            "request.completed",
        ]
        first_request = runtime.events[0].request
        assert first_request is not None
        assert first_request["headers"]["Authorization"] == REDACTED
        assert first_request["json_body"]["password"] == REDACTED

        lines = (runtime.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 6
        assert json.loads(lines[0])["request"]["headers"]["Authorization"] == REDACTED


@pytest.mark.asyncio
async def test_named_sessions_keep_cookies_isolated(tmp_path: Path) -> None:
    seen_cookies: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login/alice":
            return httpx.Response(200, headers={"Set-Cookie": "sid=alice; Path=/"})
        if path == "/login/bob":
            return httpx.Response(200, headers={"Set-Cookie": "sid=bob; Path=/"})
        if path.startswith("/who/"):
            seen_cookies[path] = request.headers.get("Cookie", "")
            return httpx.Response(200)
        return httpx.Response(404)

    workflow = Workflow.model_validate(
        {
            "name": "sessions",
            "base_url": "http://example.test",
            "sessions": {"alice": {}, "bob": {}},
            "steps": [
                {
                    "id": "login-a",
                    "session": "alice",
                    "request": {"method": "GET", "path": "/login/alice"},
                },
                {
                    "id": "login-b",
                    "session": "bob",
                    "request": {"method": "GET", "path": "/login/bob"},
                },
                {
                    "id": "who-a",
                    "session": "alice",
                    "depends_on": ["login-a"],
                    "request": {"method": "GET", "path": "/who/alice"},
                },
                {
                    "id": "who-b",
                    "session": "bob",
                    "depends_on": ["login-b"],
                    "request": {"method": "GET", "path": "/who/bob"},
                },
            ],
        }
    )

    async with ExecutionRuntime(
        workflow,
        output_root=tmp_path,
        transport=httpx.MockTransport(handler),
    ) as runtime:
        await runtime.execute_workflow()

    assert seen_cookies["/who/alice"] == "sid=alice"
    assert seen_cookies["/who/bob"] == "sid=bob"
