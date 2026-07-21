"""Postman Collection v2.1 adapter: normalize into a captured trace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from statebreaker.errors import CaptureError
from statebreaker.models.capture import BodyEncoding, CapturedTrace, HttpExchange


def parse_postman(
    data: dict[str, Any], *, capture_id: str, project: str = "default"
) -> CapturedTrace:
    """Normalize an in-memory Postman collection into a :class:`CapturedTrace`."""
    items = data.get("item")
    if not isinstance(items, list):
        raise CaptureError("Postman collection must contain an 'item' list")
    exchanges: list[HttpExchange] = []
    for entry in _walk_items(items):
        exchanges.append(_parse_entry(len(exchanges) + 1, entry))
    return CapturedTrace(
        capture_id=capture_id,
        source="postman",
        project=project,
        sessions=[],
        exchanges=exchanges,
    )


def load_postman(
    path: str | Path, *, capture_id: str | None = None, project: str = "default"
) -> CapturedTrace:
    """Load a Postman collection file and normalize it."""
    collection_path = Path(path)
    if not collection_path.exists():
        raise CaptureError(f"Postman collection not found: {collection_path}")
    try:
        data = json.loads(collection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"invalid JSON in {collection_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptureError(f"Postman collection must be a JSON object: {collection_path}")
    return parse_postman(data, capture_id=capture_id or collection_path.stem, project=project)


def _walk_items(items: list[Any]) -> list[dict[str, Any]]:
    """Flatten nested item groups, preserving document order."""
    entries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("request"), (dict, str)):
            entries.append(item)
        nested = item.get("item")
        if isinstance(nested, list):
            entries.extend(_walk_items(nested))
    return entries


def _parse_entry(index: int, item: dict[str, Any]) -> HttpExchange:
    request = item.get("request")
    if isinstance(request, str):
        return HttpExchange(
            exchange_id=f"pm-{index}", method="GET", url=request, response_status=0
        )
    if not isinstance(request, dict):
        raise CaptureError(f"Postman item #{index} has an invalid request")
    method = str(request.get("method", "GET")).upper()
    url, query_headers = _parse_url(request.get("url"))
    headers = _parse_headers(request.get("header"))
    headers.update(query_headers)
    body, encoding = _parse_body(request.get("body"))
    return HttpExchange(
        exchange_id=f"pm-{index}",
        method=method,
        url=url,
        request_headers=headers,
        request_body=body,
        request_body_encoding=encoding,
        response_status=0,
    )


def _parse_url(url: Any) -> tuple[str, dict[str, str]]:
    """Return (url, extra query headers-as-dict placeholder) — query stays in URL."""
    if isinstance(url, str):
        return url, {}
    if not isinstance(url, dict):
        return "", {}
    raw = url.get("raw")
    if isinstance(raw, str) and raw:
        return raw, {}
    protocol = str(url.get("protocol", "http"))
    host_parts = url.get("host") or []
    host = ".".join(str(part) for part in host_parts) if isinstance(host_parts, list) else ""
    path_parts = url.get("path") or []
    path = "/".join(str(part) for part in path_parts) if isinstance(path_parts, list) else ""
    query_items = url.get("query") or []
    query = (
        "&".join(
            f"{entry.get('key', '')}={entry.get('value', '')}"
            for entry in query_items
            if isinstance(entry, dict) and not entry.get("disabled")
        )
        if isinstance(query_items, list)
        else ""
    )
    built = f"{protocol}://{host}/{path}"
    if query:
        built += f"?{query}"
    return built, {}


def _parse_headers(headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(headers, list):
        return result
    for header in headers:
        if not isinstance(header, dict) or header.get("disabled"):
            continue
        key = header.get("key")
        if key is not None:
            result[str(key).lower()] = str(header.get("value", ""))
    return result


def _parse_body(body: Any) -> tuple[Any | None, BodyEncoding]:
    if not isinstance(body, dict):
        return None, "none"
    mode = body.get("mode")
    if mode == "raw":
        text = body.get("raw")
        if not isinstance(text, str):
            return None, "none"
        options = body.get("options") or {}
        language = str((options.get("raw") or {}).get("language", "")).lower()
        if language == "json":
            try:
                return json.loads(text), "json"
            except json.JSONDecodeError:
                return text, "raw"
        return text, "raw"
    if mode == "urlencoded":
        entries = body.get("urlencoded") or []
        return {
            str(entry.get("key", "")): str(entry.get("value", ""))
            for entry in entries
            if isinstance(entry, dict) and not entry.get("disabled")
        }, "form"
    if mode == "formdata":
        entries = body.get("formdata") or []
        return {
            str(entry.get("key", "")): str(entry.get("value", ""))
            for entry in entries
            if isinstance(entry, dict) and not entry.get("disabled")
        }, "form"
    if mode == "graphql":
        graphql = body.get("graphql") or {}
        return dict(graphql) if isinstance(graphql, dict) else None, "json" if graphql else "none"
    return None, "none"
