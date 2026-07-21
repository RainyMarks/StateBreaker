"""v0.1 workflow documents must migrate into v0.2 capture artifacts."""

from __future__ import annotations

from statebreaker.models.capture import CapturedTrace
from statebreaker.models.migration import migrate_v01_workflow

V01_WORKFLOW = {
    "schema_version": "0.1",
    "name": "legacy-flow",
    "base_url": "http://127.0.0.1:9000",
    "sessions": {"alice": {"headers": {"X-User": "alice"}}},
    "steps": [
        {
            "id": "create-thing",
            "role": "setup",
            "session": "alice",
            "request": {
                "method": "POST",
                "path": "/api/things",
                "json_body": {"name": "demo"},
                "headers": {"X-Trace": "1"},
                "query": {"verbose": "true"},
            },
            "extract": [{"name": "thing_id", "kind": "jsonpath", "expression": "$.thing.id"}],
        },
        {
            "id": "act-on-thing",
            "role": "action",
            "session": "alice",
            "request": {"method": "POST", "path": "/api/things/${thing_id}/act"},
            "depends_on": ["create-thing"],
        },
    ],
}


def test_migrate_v01_workflow_produces_trace_and_templates() -> None:
    trace, templates = migrate_v01_workflow(V01_WORKFLOW)
    assert isinstance(trace, CapturedTrace)
    assert trace.source == "manual"
    assert trace.base_url == "http://127.0.0.1:9000"
    assert trace.sessions == ["alice"]
    assert len(templates) == 2
    assert templates[0].body_encoding == "json"
    assert templates[0].query == {"verbose": "true"}
    assert templates[1].path_template == "/api/things/${thing_id}/act"
    assert templates[1].body_encoding == "none"
    assert {e.exchange_id for e in trace.exchanges} == {"v01-create-thing", "v01-act-on-thing"}
    # migrated artifacts remain valid v0.2 documents
    assert trace.to_json_dict()["schema_version"] == "0.2"
