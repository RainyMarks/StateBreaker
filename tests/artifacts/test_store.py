"""Artifact store round-trip, indexing, and redaction."""

from __future__ import annotations

from pathlib import Path

from statebreaker.artifacts.redaction import is_sensitive_key, redact_mapping, redact_text
from statebreaker.artifacts.store import ArtifactStore
from statebreaker.models.capture import CapturedTrace, HttpExchange
from statebreaker.models.findings import ScanOutcome


def _trace() -> CapturedTrace:
    return CapturedTrace(
        capture_id="cap-1",
        source="har",
        project="demo",
        exchanges=[
            HttpExchange(
                exchange_id="exchange-1",
                method="GET",
                url="http://127.0.0.1:8080/api/state",
                response_status=200,
            )
        ],
    )


def test_store_save_load_list(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "project")
    trace = _trace()
    store.save("captures", "cap-1", trace, summary="demo capture")
    assert store.exists("captures", "cap-1")
    loaded = store.load("captures", "cap-1", CapturedTrace)
    assert loaded == trace
    assert store.list_ids("captures") == ["cap-1"]

    outcome = ScanOutcome(scan_id="scan-1", project="demo", capture_id="cap-1")
    store.save("trials", "scan-1-outcome", outcome)
    assert store.load("trials", "scan-1-outcome", ScanOutcome).status == "running"
    store.close()


def test_store_rejects_unknown_kind(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "project")
    try:
        store.save("mystery", "x", _trace())
    except Exception as exc:
        assert "unknown artifact kind" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ArtifactError")
    store.close()


def test_redaction_masks_credentials() -> None:
    assert is_sensitive_key("Authorization")
    assert is_sensitive_key("set-cookie")
    assert is_sensitive_key("x-csrf-token")
    assert not is_sensitive_key("content-type")

    mapping = {
        "Authorization": "Bearer abcdef123456",
        "nested": {"session_token": "s3cret", "ok": 1},
        "items": [{"password": "pw"}],
    }
    redacted = redact_mapping(mapping)
    assert redacted["Authorization"] == "***REDACTED***"
    assert redacted["nested"]["session_token"] == "***REDACTED***"
    assert redacted["nested"]["ok"] == 1
    assert redacted["items"][0]["password"] == "***REDACTED***"

    text = redact_text("header: Bearer abcdef1234567890 done")
    assert "abcdef1234567890" not in text
