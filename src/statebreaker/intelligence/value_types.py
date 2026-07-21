"""Classification of dynamic-looking scalar values found in traffic.

Purely structural heuristics — no business vocabulary.
"""

from __future__ import annotations

import re
from typing import Any

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")


def classify_value(value: Any) -> str:
    """Best-effort structural type of a scalar found in traffic."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "numeric_id" if value >= 100 else "small_number"
    if isinstance(value, float):
        return "amount"
    if not isinstance(value, str):
        return "unknown"
    text = value.strip()
    if not text:
        return "empty"
    if _UUID_RE.match(text):
        return "uuid"
    if _JWT_RE.match(text) and len(text) > 20:
        return "jwt"
    if _ISO_TS_RE.match(text):
        return "timestamp"
    if _EMAIL_RE.match(text):
        return "email"
    if _URL_RE.match(text):
        return "url"
    if text.isdigit():
        return "numeric_id" if len(text) >= 3 else "small_number"
    if len(text) >= 16 and _HEX_RE.match(text):
        return "token"
    if len(text) >= 12 and _BASE64ISH_RE.match(text) and any(c.isdigit() for c in text):
        return "token"
    if len(text) <= 24 and re.fullmatch(r"[a-z][a-z0-9_]*", text):
        return "enum"
    if len(text) <= 32:
        return "short_string"
    return "string"


def is_dynamic_value(value: Any) -> bool:
    """Whether a value is worth tracking for producer→consumer lineage."""
    return classify_value(value) in {
        "uuid",
        "numeric_id",
        "jwt",
        "token",
        "email",
        "url",
        "timestamp",
    }
