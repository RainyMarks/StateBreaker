from __future__ import annotations

import inspect
from pathlib import Path

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
        "static-resource-filtering",
        "explicit-entry-exclusion",
        "browser-header-normalization",
        "required-response-body-validation",
        "json-response-extractors",
        "explicit-step-roles",
    ]
    assert "Offline HAR 1.2 import" in manifest.description


def test_capture_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(HarCapturePlugin.capture)


def test_readme_documents_inference_capability_and_limits() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(
        encoding="utf-8"
    )

    assert "json-response-extractors" in readme
    assert "explicit-step-roles" in readme
    assert "explicit-entry-exclusion" in readme
    assert "browser-header-normalization" in readme
    assert "required-response-body-validation" in readme
    assert "required_response_body_entry_indices" in readme
    assert "parseable body does not guarantee" in readme
    assert "normalize_browser_headers" in readme
    assert "Sec-Fetch-*" in readme
    assert "strip_credentials" in readme
    assert "exclude_entry_indices" in readme
    assert "original zero-based" in readme
    assert "business-flow selection" in readme
    assert "setup_entry_indices" in readme
    assert "infer_response_variables=False" in readme
    assert "does not automatically infer setup roles" in readme
    assert "--options capture-options.json" in readme
    assert "automatic-role-inference" not in readme
    assert "does not prove Runtime replay" in readme
