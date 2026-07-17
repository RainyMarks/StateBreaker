"""Minimal, non-mutating HAR 1.2 parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from statebreaker_har_capture.errors import HarCaptureError


def parse_har(source: Path) -> dict[str, Any]:
    """Read and validate the minimal HAR 1.2 envelope from *source*."""

    try:
        raw = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HarCaptureError(
            f"HAR read error at {source}: file is not valid UTF-8 ({exc.reason})"
        ) from exc
    except OSError as exc:
        raise HarCaptureError(f"HAR read error at {source}: {exc.strerror or exc}") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HarCaptureError(
            f"HAR JSON error at {source}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if not isinstance(document, dict):
        raise HarCaptureError(f"HAR structure error at {source}: top level must be an object")

    log = document.get("log")
    if not isinstance(log, dict):
        raise HarCaptureError(f"HAR structure error at {source}: log must be an object")
    if log.get("version") != "1.2":
        raise HarCaptureError(f"HAR version error at {source}: log.version must be exactly '1.2'")
    if "entries" not in log:
        raise HarCaptureError(f"HAR structure error at {source}: log.entries is required")

    entries = log["entries"]
    if not isinstance(entries, list):
        raise HarCaptureError(f"HAR structure error at {source}: log.entries must be a list")
    if not entries:
        raise HarCaptureError(f"HAR structure error at {source}: log.entries must not be empty")

    return document
