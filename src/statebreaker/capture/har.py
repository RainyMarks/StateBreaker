"""HAR 1.2 adapter: normalize exported browser traffic into a captured trace."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from statebreaker.errors import CaptureError
from statebreaker.models.capture import BodyEncoding, CapturedTrace, HttpExchange

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def parse_har(data: dict[str, Any], *, capture_id: str, project: str = "default") -> CapturedTrace:
    """Normalize an in-memory HAR document into a :class:`CapturedTrace`."""
    log = data.get("log")
    if not isinstance(log, dict):
        raise CaptureError("HAR document must contain a 'log' object")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise CaptureError("HAR log must contain an 'entries' list")
    exchanges: list[HttpExchange] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CaptureError(f"HAR entry #{index} must be an object")
        exchanges.append(_parse_entry(index, entry))
    return CapturedTrace(
        capture_id=capture_id,
        source="har",
        project=project,
        sessions=[],
        exchanges=exchanges,
    )


def load_har(
    path: str | Path, *, capture_id: str | None = None, project: str = "default"
) -> CapturedTrace:
    """Load a HAR file from disk and normalize it."""
    har_path = Path(path)
    if not har_path.exists():
        raise CaptureError(f"HAR file not found: {har_path}")
    try:
        data = json.loads(har_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"invalid JSON in HAR file {har_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptureError(f"HAR file must contain a JSON object: {har_path}")
    return parse_har(data, capture_id=capture_id or har_path.stem, project=project)


def _parse_entry(index: int, entry: dict[str, Any]) -> HttpExchange:
    request = entry.get("request")
    response = entry.get("response")
    method, url, req_headers, req_body, req_encoding = _parse_request(
        request if isinstance(request, dict) else {}
    )
    status, resp_headers, resp_body, resp_encoding = _parse_response(
        response if isinstance(response, dict) else {}
    )
    started_at_ns, completed_at_ns = _entry_times(entry)
    return HttpExchange(
        exchange_id=f"har-{index}",
        method=method,
        url=url,
        request_headers=req_headers,
        request_body=req_body,
        request_body_encoding=req_encoding,
        response_status=status,
        response_headers=resp_headers,
        response_body=resp_body,
        response_body_encoding=resp_encoding,
        started_at_ns=started_at_ns,
        completed_at_ns=completed_at_ns,
    )


def _parse_request(
    request: dict[str, Any],
) -> tuple[str, str, dict[str, str], Any | None, BodyEncoding]:
    method = str(request.get("method", "GET")).upper()
    url = str(request.get("url", ""))
    headers = _headers_to_dict(request.get("headers"))
    body, encoding = _parse_post_data(request.get("postData"))
    return method, url, headers, body, encoding


def _parse_response(
    response: dict[str, Any],
) -> tuple[int, dict[str, str], Any | None, BodyEncoding]:
    raw_status = response.get("status", 0)
    status = int(raw_status) if isinstance(raw_status, (int, float)) else 0
    headers = _headers_to_dict(response.get("headers"))
    body, encoding = _parse_content(response.get("content"))
    return status, headers, body, encoding


def _headers_to_dict(headers: Any) -> dict[str, str]:
    """Flatten a HAR header list; lowercase keys, later duplicates win."""
    result: dict[str, str] = {}
    if not isinstance(headers, list):
        return result
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = header.get("name")
        if name is None:
            continue
        result[str(name).lower()] = _text(header.get("value", ""))
    return result


def _parse_post_data(post_data: Any) -> tuple[Any | None, BodyEncoding]:
    if not isinstance(post_data, dict):
        return None, "none"
    mime = str(post_data.get("mimeType", "")).split(";")[0].strip().lower()
    text = post_data.get("text")
    if "json" in mime:
        if isinstance(text, str):
            try:
                return json.loads(text), "json"
            except json.JSONDecodeError:
                return text, "raw"
        return None, "none"
    if mime == "application/x-www-form-urlencoded":
        params = post_data.get("params")
        if isinstance(params, list):
            return {
                str(param.get("name", "")): _text(param.get("value", ""))
                for param in params
                if isinstance(param, dict)
            }, "form"
        if isinstance(text, str):
            return dict(parse_qsl(text, keep_blank_values=True)), "form"
        return None, "none"
    if isinstance(text, str):
        return text, "raw"
    return None, "none"


def _parse_content(content: Any) -> tuple[Any | None, BodyEncoding]:
    if not isinstance(content, dict):
        return None, "none"
    text = content.get("text")
    if not isinstance(text, str):
        return None, "none"
    if content.get("encoding") == "base64":
        try:
            text = base64.b64decode(text).decode("utf-8", errors="replace")
        except ValueError:
            return text, "raw"
    mime = str(content.get("mimeType", "")).lower()
    if "json" in mime:
        try:
            return json.loads(text), "json"
        except json.JSONDecodeError:
            pass
    return text, "raw"


def _entry_times(entry: dict[str, Any]) -> tuple[int, int]:
    started_at_ns = _parse_started(entry.get("startedDateTime"))
    timings = entry.get("timings")
    if not isinstance(timings, dict):
        return started_at_ns, started_at_ns
    extra_ms = 0.0
    for key in ("wait", "receive"):
        value = timings.get(key)
        if isinstance(value, (int, float)) and value > 0:
            extra_ms += float(value)
    return started_at_ns, started_at_ns + int(extra_ms * 1_000_000)


def _parse_started(value: Any) -> int:
    """Convert a HAR ``startedDateTime`` ISO string to epoch nanoseconds (0 on failure)."""
    if not isinstance(value, str) or not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = parsed - _EPOCH
    total_ns = (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )
    return total_ns


def _text(value: Any) -> str:
    return "" if value is None else str(value)
