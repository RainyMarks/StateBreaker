"""Shared, non-mutating decoding of HAR JSON response bodies."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ResponseJsonFailure(StrEnum):
    """Safe reasons why a HAR response cannot supply inference JSON."""

    RESPONSE_MISSING = "response is missing"
    CONTENT_MISSING = "response.content is missing"
    TEXT_MISSING = "response.content.text is missing"
    BODY_EMPTY = "response body is empty"
    MIME_NOT_JSON = "response MIME is not JSON-compatible"
    INVALID_JSON = "response body is not valid JSON"
    INVALID_BASE64_JSON = "response body is not valid base64-encoded UTF-8 JSON"
    UNSUPPORTED_ENCODING = "response body uses an unsupported encoding"
    TRUNCATED = "response body is explicitly truncated"
    STATUS_204 = "status 204 cannot provide a required response body"


@dataclass(frozen=True, slots=True)
class ResponseJsonResult:
    """A decoded JSON value or one safe, deterministic failure reason."""

    value: Any = None
    failure: ResponseJsonFailure | None = None


def _is_json_mime(mime_type: Any) -> bool:
    if not isinstance(mime_type, str):
        return False
    normalized = mime_type.split(";", maxsplit=1)[0].strip().lower()
    if normalized == "application/json":
        return True
    return "/" in normalized and normalized.rsplit("/", maxsplit=1)[1].endswith("+json")


def _is_explicitly_truncated(value: Mapping[str, Any]) -> bool:
    return value.get("_truncated") is True or value.get("truncated") is True


def decode_json_response(entry: Mapping[str, Any]) -> ResponseJsonResult:
    """Decode inference-compatible JSON without mutating or disclosing the entry."""

    response = entry.get("response")
    if not isinstance(response, Mapping):
        return ResponseJsonResult(failure=ResponseJsonFailure.RESPONSE_MISSING)
    if response.get("status") == 204:
        return ResponseJsonResult(failure=ResponseJsonFailure.STATUS_204)
    if _is_explicitly_truncated(response):
        return ResponseJsonResult(failure=ResponseJsonFailure.TRUNCATED)

    content = response.get("content")
    if not isinstance(content, Mapping):
        return ResponseJsonResult(failure=ResponseJsonFailure.CONTENT_MISSING)
    if _is_explicitly_truncated(content):
        return ResponseJsonResult(failure=ResponseJsonFailure.TRUNCATED)
    if not _is_json_mime(content.get("mimeType")):
        return ResponseJsonResult(failure=ResponseJsonFailure.MIME_NOT_JSON)

    text = content.get("text")
    if not isinstance(text, str):
        return ResponseJsonResult(failure=ResponseJsonFailure.TEXT_MISSING)
    if text == "":
        return ResponseJsonResult(failure=ResponseJsonFailure.BODY_EMPTY)

    encoding = content.get("encoding")
    if encoding not in (None, "", "base64"):
        return ResponseJsonResult(failure=ResponseJsonFailure.UNSUPPORTED_ENCODING)
    if encoding == "base64":
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
            return ResponseJsonResult(value=json.loads(text))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            return ResponseJsonResult(failure=ResponseJsonFailure.INVALID_BASE64_JSON)

    try:
        return ResponseJsonResult(value=json.loads(text))
    except json.JSONDecodeError:
        return ResponseJsonResult(failure=ResponseJsonFailure.INVALID_JSON)
