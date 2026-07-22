"""Build replayable request templates from exchanges + inferred bindings."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlparse

from statebreaker.intelligence.selectors import extract_from_exchange
from statebreaker.models.capture import CapturedTrace, HttpExchange, RequestTemplate
from statebreaker.models.workflow import VariableBinding

_IGNORED_TEMPLATE_HEADERS = {
    "content-length",
    "host",
    "connection",
    "accept-encoding",
    "cookie",
}

# Identity headers belong to the session, not to a request template: a plan
# must be able to fire the same template as a different identity (cross-user).
IDENTITY_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-user-id",
    "x-user",
    "x-auth-user",
    "x-actor",
    "x-account-id",
}


def _substitute_leaf(value: Any, replacements: dict[str, str]) -> Any:
    """Replace exact produced values with ``${variable}`` references."""
    if isinstance(value, str):
        for produced, variable in replacements.items():
            if value == produced:
                return "${" + variable + "}"
            if produced and produced in value and len(produced) >= 4:
                return value.replace(produced, "${" + variable + "}")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if str(value) in replacements:
            return "${" + replacements[str(value)] + "}"
        return value
    if isinstance(value, dict):
        return {key: _substitute_leaf(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_leaf(item, replacements) for item in value]
    return value


def harvest_session_headers(trace: CapturedTrace) -> dict[str, dict[str, str]]:
    """Recover per-session identity headers from a captured trace.

    Identity headers are stripped from templates (they belong to sessions), so
    the scanner re-seeds session configs from what the capture observed.
    """
    harvested: dict[str, dict[str, str]] = {}
    for exchange in trace.exchanges:
        headers = harvested.setdefault(exchange.session_id, {})
        for name, value in exchange.request_headers.items():
            lowered = name.lower()
            if lowered in IDENTITY_HEADERS and lowered not in headers:
                headers[lowered] = value
    return harvested


def harvest_session_cookies(trace: CapturedTrace) -> dict[str, dict[str, str]]:
    """Recover per-session cookies from captured Cookie request headers."""
    harvested: dict[str, dict[str, str]] = {}
    for exchange in trace.exchanges:
        cookie_header = _header_value(exchange.request_headers, "cookie")
        if not cookie_header:
            continue
        cookies = harvested.setdefault(exchange.session_id, {})
        cookies.update(_parse_cookie_header(cookie_header))
    return harvested


def _header_value(headers: dict[str, str], wanted: str) -> str | None:
    for name, value in headers.items():
        if name.lower() == wanted:
            return value
    return None


def _parse_cookie_header(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or not name:
            continue
        cookies[name] = value
    return cookies


def build_templates(
    exchanges: list[HttpExchange],
    bindings: list[VariableBinding],
) -> list[RequestTemplate]:
    """One template per exchange, with consumed values parameterized."""
    producer_lookup: dict[str, HttpExchange] = {e.exchange_id: e for e in exchanges}
    variable_for: dict[tuple[str, str], str] = {}
    for binding in bindings:
        variable_for[(binding.producer_exchange_id, binding.producer_selector)] = (
            binding.variable_id
        )

    def produced_value_of(binding: VariableBinding) -> str | None:
        producer = producer_lookup.get(binding.producer_exchange_id)
        if producer is None:
            return None
        value = extract_from_exchange(producer, binding.producer_selector)
        return None if value is None else str(value)

    by_consumer: dict[str, list[VariableBinding]] = {}
    for binding in bindings:
        by_consumer.setdefault(binding.consumer_exchange_id, []).append(binding)

    templates: list[RequestTemplate] = []
    for exchange in exchanges:
        replacements: dict[str, str] = {}
        for binding in by_consumer.get(exchange.exchange_id, []):
            if (binding.producer_exchange_id, binding.producer_selector) not in variable_for:
                continue
            produced = produced_value_of(binding)
            if produced:
                replacements[produced] = binding.variable_id

        parsed = urlparse(exchange.url)
        path = parsed.path
        for produced, variable_id in replacements.items():
            if produced and produced in path:
                path = path.replace(produced, "${" + variable_id + "}")

        query = {
            key: _substitute_leaf(value, replacements)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        }
        headers = {
            name: _substitute_leaf(value, replacements)
            for name, value in exchange.request_headers.items()
            if name.lower() not in _IGNORED_TEMPLATE_HEADERS
            and name.lower() not in IDENTITY_HEADERS
        }
        body = _substitute_leaf(exchange.request_body, replacements)

        templates.append(
            RequestTemplate(
                template_id=exchange.exchange_id,
                method=exchange.method,
                path_template=path,
                query={str(k): str(v) for k, v in query.items()},
                headers={str(k): str(v) for k, v in headers.items()},
                body=body,
                body_encoding=exchange.request_body_encoding,
                source_exchange_id=exchange.exchange_id,
            )
        )
    return templates
