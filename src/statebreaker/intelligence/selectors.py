"""Value extraction from exchanges via compact selector strings.

Selector forms:
- ``json:$.path.to.field`` — jsonpath into a JSON body
- ``header:Name`` — a response/request header (case-insensitive)
- ``status`` — the HTTP status code
"""

from __future__ import annotations

from typing import Any

from jsonpath_ng import parse as parse_jsonpath

from statebreaker.errors import ExtractionError
from statebreaker.models.capture import HttpExchange


def _walk_jsonpath(body: Any, expression: str) -> list[Any]:
    try:
        parsed = parse_jsonpath(expression)
    except Exception as exc:  # jsonpath_ng raises several error types
        raise ExtractionError(f"invalid jsonpath {expression!r}: {exc}") from exc
    return [match.value for match in parsed.find(body)]


def extract_from_parts(
    selector: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
    status: int = 0,
) -> Any | None:
    """Extract a value from response parts; ``None`` when absent."""
    if selector == "status":
        return status
    if selector.startswith("header:"):
        wanted = selector[len("header:") :].lower()
        for key, value in (headers or {}).items():
            if key.lower() == wanted:
                return value
        return None
    if selector.startswith("json:"):
        matches = _walk_jsonpath(body, selector[len("json:") :])
        return matches[0] if matches else None
    raise ExtractionError(f"unsupported selector: {selector!r}")


def extract_from_exchange(exchange: HttpExchange, selector: str) -> Any | None:
    return extract_from_parts(
        selector,
        body=exchange.response_body,
        headers=exchange.response_headers,
        status=exchange.response_status,
    )


def require(exchange: HttpExchange, selector: str) -> Any:
    value = extract_from_exchange(exchange, selector)
    if value is None:
        raise ExtractionError(
            f"selector {selector!r} found nothing in exchange {exchange.exchange_id!r}"
        )
    return value
