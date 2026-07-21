"""Capture-layer models: traces, browser actions, HTTP exchanges, templates."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from statebreaker.models.base import ContractModel, utc_now

CaptureSource = Literal["browser", "har", "openapi", "postman", "manual", "proxy"]

BodyEncoding = Literal["json", "form", "raw", "none"]


class DomEvent(ContractModel):
    """A user-interface interaction observed during browser capture."""

    type: str
    selector: str | None = None
    visible_text: str | None = None


class BrowserAction(ContractModel):
    """A semantic user action correlated with the exchanges it triggered."""

    action_id: str
    session_id: str = "default"
    dom_event: DomEvent | None = None
    page_url: str | None = None
    triggered_exchange_ids: list[str] = Field(default_factory=list)
    started_at_ns: int | None = None


class HttpExchange(ContractModel):
    """One normalized request/response pair, independent of capture source."""

    exchange_id: str
    action_id: str | None = None
    session_id: str = "default"
    method: str
    url: str
    path_template: str | None = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: Any | None = None
    request_body_encoding: BodyEncoding = "none"
    response_status: int = 0
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: Any | None = None
    response_body_encoding: BodyEncoding = "none"
    started_at_ns: int = 0
    completed_at_ns: int = 0


class CapturedTrace(ContractModel):
    """The raw material for all downstream intelligence: a recorded normal flow."""

    capture_id: str
    source: CaptureSource
    project: str = "default"
    created_at: datetime = Field(default_factory=utc_now)
    base_url: str | None = None
    sessions: list[str] = Field(default_factory=list)
    actions: list[BrowserAction] = Field(default_factory=list)
    exchanges: list[HttpExchange] = Field(default_factory=list)


class RequestTemplate(ContractModel):
    """A replayable request with optional ``${variable}`` placeholders.

    Produced by the intelligence engine from observed exchanges; never
    hand-authored in the normal workflow.
    """

    template_id: str
    method: str
    path_template: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    body_encoding: BodyEncoding = "none"
    source_exchange_id: str | None = None
