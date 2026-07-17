"""Shared sequential HTTP runtime; attack scheduling intentionally lives in plugins."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from jsonpath_ng.ext import parse as parse_jsonpath  # type: ignore[import-untyped]

from statebreaker.errors import ExtractionError, RuntimeRequestError, TemplateError
from statebreaker.models import (
    TEMPLATE_PATTERN,
    Extractor,
    ExtractorKind,
    RequestStep,
    ResponseRecord,
    RunEvent,
    Workflow,
)

REDACTED = "<redacted>"
SECRET_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "apikey",
}


def _lookup_variable(name: str, variables: Mapping[str, Any]) -> Any:
    if name in variables:
        return variables[name]
    current: Any = variables
    for part in name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise TemplateError(f"missing template variable: {name}")
        current = current[part]
    return current


def render_template(value: Any, variables: Mapping[str, Any]) -> Any:
    """Render ${name} placeholders recursively, preserving full-value types."""

    if isinstance(value, str):
        full_match = TEMPLATE_PATTERN.fullmatch(value)
        if full_match:
            return _lookup_variable(full_match.group(1), variables)

        def replace(match: re.Match[str]) -> str:
            return str(_lookup_variable(match.group(1), variables))

        return TEMPLATE_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [render_template(item, variables) for item in value]
    if isinstance(value, tuple):
        return tuple(render_template(item, variables) for item in value)
    if isinstance(value, dict):
        return {
            str(render_template(key, variables)): render_template(item, variables)
            for key, item in value.items()
        }
    return value


def redact(value: Any, key_hint: str | None = None) -> Any:
    """Recursively remove common credentials while preserving useful evidence shape."""

    if key_hint and key_hint.lower() in SECRET_KEYS:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(key): redact(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.events: list[RunEvent] = []

    def append(self, event: RunEvent) -> None:
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")


class ExecutionRuntime:
    """Session-isolated, sequential workflow plumbing shared by all plugins."""

    def __init__(
        self,
        workflow: Workflow,
        *,
        output_root: Path = Path(".statebreaker/runs"),
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.workflow = workflow
        self.run_id = uuid.uuid4().hex
        self.run_dir = output_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.variables: dict[str, Any] = dict(workflow.variables)
        self.responses: list[ResponseRecord] = []
        self.event_log = EventLog(self.run_dir / "events.jsonl")
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._closed = False

    @property
    def events(self) -> list[RunEvent]:
        return list(self.event_log.events)

    async def __aenter__(self) -> ExecutionRuntime:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _client(self, session_name: str) -> httpx.AsyncClient:
        if self._closed:
            raise RuntimeRequestError("runtime is already closed")
        if session_name in self._clients:
            return self._clients[session_name]
        session = self.workflow.sessions[session_name]
        client = httpx.AsyncClient(
            base_url=str(self.workflow.base_url),
            headers=render_template(session.headers, self.variables),
            cookies=render_template(session.cookies, self.variables),
            follow_redirects=session.follow_redirects,
            timeout=self._timeout_seconds,
            transport=self._transport,
        )
        self._clients[session_name] = client
        return client

    def emit(
        self,
        *,
        kind: str,
        correlation_id: str,
        step_id: str | None = None,
        request_ordinal: int | None = None,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> RunEvent:
        event = RunEvent(
            event_id=uuid.uuid4().hex,
            run_id=self.run_id,
            kind=kind,
            monotonic_ns=time.perf_counter_ns(),
            correlation_id=correlation_id,
            step_id=step_id,
            request_ordinal=request_ordinal,
            request=redact(request),
            response=redact(response),
            message=message,
        )
        self.event_log.append(event)
        return event

    async def execute_step(self, step: RequestStep, *, request_ordinal: int = 0) -> ResponseRecord:
        spec = step.request
        path = str(render_template(spec.path, self.variables))
        headers = render_template(spec.headers, self.variables)
        query = render_template(spec.query, self.variables)
        json_body = render_template(spec.json_body, self.variables)
        form_body = render_template(spec.form_body, self.variables)
        correlation_id = uuid.uuid4().hex
        request_summary = {
            "method": spec.method,
            "path": path,
            "headers": headers,
            "query": query,
            "json_body": json_body,
            "form_body": form_body,
            "session": step.session,
        }
        self.emit(
            kind="request.started",
            correlation_id=correlation_id,
            step_id=step.id,
            request_ordinal=request_ordinal,
            request=request_summary,
        )
        started = time.perf_counter()
        try:
            response = await self._client(step.session).request(
                spec.method,
                path,
                headers=headers,
                params=query,
                json=json_body,
                data=form_body,
                timeout=spec.timeout_seconds or self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            self.emit(
                kind="request.failed",
                correlation_id=correlation_id,
                step_id=step.id,
                request_ordinal=request_ordinal,
                message=f"{type(exc).__name__}: {exc}",
            )
            raise RuntimeRequestError(f"request step {step.id!r} failed: {exc}") from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        self._apply_extractors(step.extract, response)
        record = ResponseRecord(
            correlation_id=correlation_id,
            step_id=step.id,
            request_ordinal=request_ordinal,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            headers={key: value for key, value in response.headers.items()},
            body_preview=response.text[:4096],
        )
        self.responses.append(record)
        self.emit(
            kind="request.completed",
            correlation_id=correlation_id,
            step_id=step.id,
            request_ordinal=request_ordinal,
            response={
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body_preview": response.text[:4096],
                "elapsed_ms": round(elapsed_ms, 3),
            },
        )
        return record

    async def execute_workflow(self) -> list[ResponseRecord]:
        for step in self.workflow.steps:
            await self.execute_step(step)
        return list(self.responses)

    def _apply_extractors(self, extractors: list[Extractor], response: httpx.Response) -> None:
        for extractor in extractors:
            try:
                value = self._extract(extractor, response)
            except (ValueError, json.JSONDecodeError) as exc:
                if extractor.required:
                    raise ExtractionError(
                        f"extractor {extractor.name!r} failed: {exc}"
                    ) from exc
                continue
            if value is None:
                if extractor.required:
                    raise ExtractionError(f"extractor {extractor.name!r} produced no value")
                continue
            self.variables[extractor.name] = value

    @staticmethod
    def _extract(extractor: Extractor, response: httpx.Response) -> Any:
        if extractor.kind == ExtractorKind.JSONPATH:
            matches = parse_jsonpath(extractor.expression).find(response.json())
            return matches[0].value if matches else None
        if extractor.kind == ExtractorKind.HEADER:
            return response.headers.get(extractor.expression)
        if extractor.kind == ExtractorKind.REGEX:
            match = re.search(extractor.expression, response.text)
            if not match:
                return None
            if "value" in match.groupdict():
                return match.group("value")
            if match.groups():
                return match.group(1)
            return match.group(0)
        raise ExtractionError(f"unsupported extractor kind: {extractor.kind}")

    async def close(self) -> None:
        if self._closed:
            return
        for client in self._clients.values():
            await client.aclose()
        self._closed = True
