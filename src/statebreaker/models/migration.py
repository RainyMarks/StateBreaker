"""Best-effort migration of v0.1 documents into v0.2 models.

Only structural migration is provided: a v0.1 ``Workflow`` document becomes a
``manual``-source :class:`CapturedTrace` plus one :class:`RequestTemplate` per
step. Semantics (dependencies, probes) are re-derived by the v0.2 intelligence
engine, not carried over.
"""

from __future__ import annotations

from typing import Any

from statebreaker.errors import DocumentError
from statebreaker.models.capture import BodyEncoding, CapturedTrace, HttpExchange, RequestTemplate


def _v01_body_encoding(step_request: dict[str, Any]) -> tuple[Any, BodyEncoding]:
    if step_request.get("json_body") is not None:
        return step_request["json_body"], "json"
    if step_request.get("form_body") is not None:
        return step_request["form_body"], "form"
    return None, "none"


def migrate_v01_workflow(data: dict[str, Any]) -> tuple[CapturedTrace, list[RequestTemplate]]:
    """Convert a parsed v0.1 workflow mapping into v0.2 capture artifacts."""
    if not isinstance(data, dict) or "steps" not in data or "name" not in data:
        raise DocumentError("not a recognizable v0.1 workflow document")
    name = str(data["name"])
    base_url = str(data.get("base_url", ""))
    sessions = sorted({str(s) for s in data.get("sessions", {"default": {}})})

    templates: list[RequestTemplate] = []
    exchanges: list[HttpExchange] = []
    for step in data.get("steps", []):
        if not isinstance(step, dict) or "request" not in step:
            raise DocumentError(f"v0.1 step is missing a request: {step!r}")
        request = step["request"]
        body, encoding = _v01_body_encoding(request)
        step_id = str(step.get("id", f"step-{len(templates) + 1}"))
        query = {str(k): str(v) for k, v in (request.get("query") or {}).items()}
        headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
        templates.append(
            RequestTemplate(
                template_id=step_id,
                method=str(request.get("method", "GET")).upper(),
                path_template=str(request.get("path", "/")),
                query=query,
                headers=headers,
                body=body,
                body_encoding=encoding,
            )
        )
        exchanges.append(
            HttpExchange(
                exchange_id=f"v01-{step_id}",
                session_id=str(step.get("session", "default")),
                method=str(request.get("method", "GET")).upper(),
                url=base_url.rstrip("/") + str(request.get("path", "/")),
                request_headers=headers,
                request_body=body,
                request_body_encoding=encoding,
            )
        )

    trace = CapturedTrace(
        capture_id=f"migrated-{name}",
        source="manual",
        base_url=base_url or None,
        sessions=sessions,
        exchanges=exchanges,
    )
    return trace, templates
