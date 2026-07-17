"""Normalize a minimal HAR 1.2 document into a Workflow candidate."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.options import HarCaptureOptions

ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
REMOVED_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "proxy-authorization",
        "transfer-encoding",
    }
)
CREDENTIAL_HEADERS = frozenset({"authorization", "cookie"})


def _entry_error(index: int, category: str, reason: str) -> HarCaptureError:
    return HarCaptureError(f"HAR {category} error at entry {index}: {reason}")


def _origin_and_base_url(url: str, index: int) -> tuple[tuple[str, str, int], str, str, str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise _entry_error(index, "URL", f"invalid URL ({exc})") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        raise _entry_error(index, "URL", "request URL must use HTTP or HTTPS with a host")
    if parsed.username is not None or parsed.password is not None:
        raise _entry_error(index, "URL", "request URL must not contain username or password")

    hostname = parsed.hostname.lower()
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = rendered_host if port is None else f"{rendered_host}:{port}"
    base_url = f"{scheme}://{authority}/"
    path = parsed.path or "/"
    return (scheme, hostname, effective_port), base_url, path, parsed.query


def _add_query_value(query: dict[str, Any], name: str, value: str) -> None:
    if name not in query:
        query[name] = value
        return
    current = query[name]
    if isinstance(current, list):
        current.append(value)
    else:
        query[name] = [current, value]


def _normalize_query(request: Mapping[str, Any], url_query: str, index: int) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if "queryString" not in request:
        for query_name, query_value in parse_qsl(url_query, keep_blank_values=True):
            _add_query_value(query, query_name, query_value)
        return query

    items = request["queryString"]
    if not isinstance(items, list):
        raise _entry_error(index, "query", "request.queryString must be a list")
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise _entry_error(
                index, "query", f"request.queryString item {position} must be an object"
            )
        raw_name = item.get("name")
        raw_value = item.get("value")
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise _entry_error(
                index,
                "query",
                f"request.queryString item {position} requires string name and value",
            )
        normalized_name = raw_name
        normalized_value = raw_value
        _add_query_value(query, normalized_name, normalized_value)
    return query


def _normalize_headers(
    request: Mapping[str, Any], index: int, *, strip_credentials: bool
) -> dict[str, str]:
    items = request.get("headers", [])
    if not isinstance(items, list):
        raise _entry_error(index, "header", "request.headers must be a list")

    headers: dict[str, str] = {}
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise _entry_error(
                index, "header", f"request.headers item {position} must be an object"
            )
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise _entry_error(
                index,
                "header",
                f"request.headers item {position} requires string name and value",
            )
        normalized_name = name.lower()
        if normalized_name in REMOVED_HEADERS:
            continue
        if strip_credentials and normalized_name in CREDENTIAL_HEADERS:
            continue
        if normalized_name in headers:
            raise _entry_error(
                index, "header", f"duplicate retained header name {normalized_name!r}"
            )
        headers[normalized_name] = value
    return headers


def _normalize_form_items(items: Any, index: int) -> dict[str, Any]:
    if not isinstance(items, list):
        raise _entry_error(index, "body", "request.postData.params must be a list")
    form: dict[str, Any] = {}
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise _entry_error(
                index, "body", f"request.postData.params item {position} must be an object"
            )
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise _entry_error(
                index,
                "body",
                f"request.postData.params item {position} requires string name and value",
            )
        _add_query_value(form, name, value)
    return form


def _normalize_body(
    request: Mapping[str, Any], index: int
) -> tuple[Any | None, dict[str, Any] | None]:
    post_data = request.get("postData")
    if post_data is None:
        return None, None
    if not isinstance(post_data, dict):
        raise _entry_error(index, "body", "request.postData must be an object")

    raw_mime_type = post_data.get("mimeType", "")
    if not isinstance(raw_mime_type, str):
        raise _entry_error(index, "body", "request.postData.mimeType must be a string")
    mime_type = raw_mime_type.split(";", maxsplit=1)[0].strip().lower()

    if mime_type == "application/x-www-form-urlencoded":
        if "params" in post_data:
            return None, _normalize_form_items(post_data["params"], index)
        text = post_data.get("text", "")
        if not isinstance(text, str):
            raise _entry_error(index, "body", "request.postData.text must be a string")
        form: dict[str, Any] = {}
        for name, value in parse_qsl(text, keep_blank_values=True):
            _add_query_value(form, name, value)
        return None, form

    if mime_type == "application/json" or mime_type.endswith("+json"):
        text = post_data.get("text")
        if not isinstance(text, str):
            raise _entry_error(
                index, "body", "JSON request.postData requires string text"
            )
        try:
            return json.loads(text), None
        except json.JSONDecodeError as exc:
            raise _entry_error(index, "body", "request.postData contains invalid JSON") from exc

    text = post_data.get("text", "")
    if text in {"", None} and not post_data.get("params"):
        return None, None
    rendered_type = mime_type or "unspecified"
    raise _entry_error(
        index,
        "body",
        f"unsupported request body content type {rendered_type!r}; use JSON or form data",
    )


def _step_id(index: int, method: str, path: str) -> str:
    decoded_path = unquote(path)
    parts = re.findall(r"[A-Za-z0-9]+", decoded_path.lower())
    slug = "-".join(parts)[:48].strip("-") or "root"
    digest = hashlib.sha256(f"{index}\0{method}\0{path}".encode()).hexdigest()[:8]
    return f"step-{index:04d}-{method.lower()}-{slug}-{digest}"


def normalize_har(document: Mapping[str, Any], options: HarCaptureOptions) -> dict[str, Any]:
    """Return a deterministic Workflow-shaped mapping without mutating *document*."""

    entries = document["log"]["entries"]
    entry_count = len(entries)
    for probe_index in options.state_probe_entry_indices:
        if probe_index >= entry_count:
            raise HarCaptureError(
                "HAR state probe error at entry "
                f"{probe_index}: index is out of range for {entry_count} entries"
            )

    expected_origin: tuple[str, str, int] | None = None
    base_url = ""
    steps: list[dict[str, Any]] = []
    step_by_entry: dict[int, str] = {}

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise _entry_error(index, "structure", "entry must be an object")
        request = entry.get("request")
        if not isinstance(request, dict):
            raise _entry_error(index, "structure", "request must be an object")

        raw_method = request.get("method")
        if not isinstance(raw_method, str):
            raise _entry_error(index, "method", "request.method must be a string")
        method = raw_method.upper()
        if method not in ALLOWED_METHODS:
            raise _entry_error(index, "method", f"unsupported request method {method!r}")

        raw_url = request.get("url")
        if not isinstance(raw_url, str):
            raise _entry_error(index, "URL", "request.url must be a string")
        origin, current_base_url, path, url_query = _origin_and_base_url(raw_url, index)
        if expected_origin is None:
            expected_origin = origin
            base_url = current_base_url
        elif origin != expected_origin:
            raise _entry_error(index, "origin", "all requests must belong to the same origin")

        json_body, form_body = _normalize_body(request, index)

        step_id = _step_id(index, method, path)
        depends_on = [steps[-1]["id"]] if steps else []
        is_probe = index in options.state_probe_entry_indices
        request_spec: dict[str, Any] = {
            "method": method,
            "path": path,
            "headers": _normalize_headers(
                request, index, strip_credentials=options.strip_credentials
            ),
            "query": _normalize_query(request, url_query, index),
        }
        if json_body is not None:
            request_spec["json_body"] = json_body
        if form_body is not None:
            request_spec["form_body"] = form_body

        steps.append(
            {
                "id": step_id,
                "role": "probe" if is_probe else "action",
                "session": "default",
                "request": request_spec,
                "extract": [],
                "depends_on": depends_on,
                "tags": ["har-1.2", "offline-import"],
            }
        )
        step_by_entry[index] = step_id

    missing_probes = [
        index for index in options.state_probe_entry_indices if index not in step_by_entry
    ]
    if missing_probes:
        raise HarCaptureError(
            f"HAR state probe error at entry {missing_probes[0]}: entry did not generate a step"
        )

    return {
        "name": "har-imported-workflow",
        "description": "Deterministic workflow imported offline from a HAR 1.2 recording.",
        "base_url": base_url,
        "sessions": {
            "default": {"headers": {}, "cookies": {}, "follow_redirects": True},
        },
        "variables": {},
        "steps": steps,
        "state_probe_steps": [step_by_entry[index] for index in options.state_probe_entry_indices],
    }
