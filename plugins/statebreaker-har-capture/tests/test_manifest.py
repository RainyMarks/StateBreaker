from __future__ import annotations

import inspect

from statebreaker_har_capture.plugin import HarCapturePlugin


def test_manifest_is_complete_and_explicit() -> None:
    manifest = HarCapturePlugin.manifest

    assert manifest.plugin_id == "har.capture"
    assert manifest.name == "StateBreaker HAR Capture"
    assert manifest.version == "0.1.0"
    assert manifest.api_version == "0.1"
    assert manifest.group == "statebreaker.capture"
    assert manifest.capabilities == [
        "har-1.2",
        "deterministic-workflow",
        "offline-import",
        "json-body",
        "form-body",
        "replayable-credentials",
    ]
    assert "Offline HAR 1.2 import" in manifest.description


def test_capture_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(HarCapturePlugin.capture)
