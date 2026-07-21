"""Redaction of credentials and tokens for logs, reports, and CLI output.

Raw evidence stays intact on disk (it is needed for replay); redaction is
applied whenever data leaves the process: CLI rendering, reports, logs.
"""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "session",
    "api-key",
    "apikey",
    "credential",
    "csrf",
    "xsrf",
)

REDACTED = "***REDACTED***"

_BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``mapping`` with sensitive values masked."""
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        if is_sensitive_key(str(key)):
            redacted[key] = REDACTED
        elif isinstance(value, dict):
            redacted[key] = redact_mapping(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_mapping(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            redacted[key] = value
    return redacted


def redact_text(text: str) -> str:
    """Mask bearer-style credentials embedded in free text."""
    return _BEARER_PATTERN.sub(r"\1" + REDACTED, text)
