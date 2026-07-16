from __future__ import annotations

import pytest
from pydantic import ValidationError

from statebreaker.models import Workflow


def workflow_data() -> dict:
    return {
        "schema_version": "0.1",
        "name": "test-flow",
        "base_url": "http://example.test",
        "sessions": {"alice": {"schema_version": "0.1"}},
        "variables": {"coupon": "BUG50"},
        "steps": [
            {
                "schema_version": "0.1",
                "id": "create",
                "role": "setup",
                "session": "alice",
                "request": {
                    "schema_version": "0.1",
                    "method": "POST",
                    "path": "/runs",
                },
                "extract": [
                    {
                        "schema_version": "0.1",
                        "name": "run_id",
                        "kind": "jsonpath",
                        "expression": "$.run_id",
                    }
                ],
            },
            {
                "schema_version": "0.1",
                "id": "probe",
                "role": "probe",
                "session": "alice",
                "depends_on": ["create"],
                "request": {
                    "schema_version": "0.1",
                    "method": "GET",
                    "path": "/runs/${run_id}/state?coupon=${coupon}",
                },
            },
        ],
        "state_probe_steps": ["probe"],
    }


def test_workflow_round_trip_and_defaults() -> None:
    workflow = Workflow.model_validate(workflow_data())
    restored = Workflow.model_validate_json(workflow.model_dump_json())

    assert restored == workflow
    assert restored.steps[0].request.timeout_seconds is None
    assert str(restored.base_url).startswith("http://example.test")


def test_unknown_template_variable_is_rejected() -> None:
    data = workflow_data()
    data["steps"][1]["request"]["path"] = "/runs/${missing}/state"

    with pytest.raises(ValidationError, match="not yet available"):
        Workflow.model_validate(data)


def test_dependency_must_appear_earlier() -> None:
    data = workflow_data()
    data["steps"][0]["depends_on"] = ["probe"]

    with pytest.raises(ValidationError, match="must appear earlier"):
        Workflow.model_validate(data)


def test_unknown_schema_version_is_rejected() -> None:
    data = workflow_data()
    data["schema_version"] = "9.9"

    with pytest.raises(ValidationError):
        Workflow.model_validate(data)
