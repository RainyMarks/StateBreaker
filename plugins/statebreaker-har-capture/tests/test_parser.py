from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.har_parser import parse_har

FIXTURES = Path(__file__).parent / "fixtures"


def test_minimal_har_parses_without_mutating_file() -> None:
    source = FIXTURES / "minimal.har"
    before = source.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()

    document = parse_har(source)

    assert document["log"]["version"] == "1.2"
    assert len(document["log"]["entries"]) == 2
    assert source.read_bytes() == before
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash


def test_invalid_json_raises_safe_local_error() -> None:
    source = FIXTURES / "invalid-json.har"

    with pytest.raises(HarCaptureError, match=r"HAR JSON error.*invalid-json\.har"):
        parse_har(source)


def test_missing_entries_raises_safe_local_error() -> None:
    source = FIXTURES / "missing-entries.har"

    with pytest.raises(HarCaptureError, match=r"HAR structure error.*log\.entries"):
        parse_har(source)
