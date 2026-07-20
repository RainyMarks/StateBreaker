from __future__ import annotations

from copy import deepcopy

import pytest

from statebreaker_har_capture.resource_filter import (
    StaticResourceReason,
    static_resource_filter_reason,
)


def _entry(
    *,
    url: str = "https://capture.example.test/api/orders",
    resource_type: str | None = None,
    mime_type: str | None = None,
) -> dict:
    entry: dict = {
        "request": {
            "method": "GET",
            "url": url,
            "headers": [{"name": "X-Fictional", "value": "fixture"}],
        }
    }
    if resource_type is not None:
        entry["_resourceType"] = resource_type
    if mime_type is not None:
        entry["response"] = {"content": {"mimeType": mime_type}}
    return entry


@pytest.mark.parametrize("resource_type", ["xhr", "XHR", "fetch", "FeTcH"])
def test_fetch_and_xhr_are_kept_ahead_of_static_signals(resource_type: str) -> None:
    entry = _entry(
        url="https://capture.example.test/assets/application.js",
        resource_type=resource_type,
        mime_type="image/png",
    )

    assert static_resource_filter_reason(entry) is None


@pytest.mark.parametrize(
    ("mime_type", "resource_type"),
    [
        ("application/json", None),
        ("Application/JSON; charset=UTF-8", "image"),
        ("application/problem+json", "script"),
        ("application/vnd.example+json; version=1", None),
    ],
)
def test_json_mime_is_kept_ahead_of_static_signals(
    mime_type: str, resource_type: str | None
) -> None:
    entry = _entry(
        url="https://capture.example.test/assets/application.js",
        resource_type=resource_type,
        mime_type=mime_type,
    )

    assert static_resource_filter_reason(entry) is None


@pytest.mark.parametrize(
    ("resource_type", "expected"),
    [
        ("image", StaticResourceReason.IMAGE_RESOURCE_TYPE),
        ("FONT", StaticResourceReason.FONT_RESOURCE_TYPE),
        ("StyleSheet", StaticResourceReason.STYLESHEET_RESOURCE_TYPE),
        ("script", StaticResourceReason.SCRIPT_RESOURCE_TYPE),
        ("MEDIA", StaticResourceReason.MEDIA_RESOURCE_TYPE),
    ],
)
def test_explicit_static_resource_types_are_filtered(
    resource_type: str, expected: StaticResourceReason
) -> None:
    assert static_resource_filter_reason(_entry(resource_type=resource_type)) is expected


@pytest.mark.parametrize(
    ("mime_type", "expected"),
    [
        ("image/avif; q=1", StaticResourceReason.IMAGE_MIME),
        ("font/woff2", StaticResourceReason.FONT_MIME),
        ("audio/ogg", StaticResourceReason.AUDIO_MIME),
        ("video/webm; charset=binary", StaticResourceReason.VIDEO_MIME),
        ("text/css; charset=UTF-8", StaticResourceReason.CSS_MIME),
        ("application/javascript", StaticResourceReason.JAVASCRIPT_MIME),
        ("text/javascript", StaticResourceReason.JAVASCRIPT_MIME),
        ("application/ecmascript", StaticResourceReason.JAVASCRIPT_MIME),
        ("text/ecmascript", StaticResourceReason.JAVASCRIPT_MIME),
    ],
)
def test_explicit_static_mime_types_are_filtered(
    mime_type: str, expected: StaticResourceReason
) -> None:
    assert static_resource_filter_reason(_entry(mime_type=mime_type)) is expected


@pytest.mark.parametrize(
    ("extension", "expected"),
    [
        ("png", StaticResourceReason.IMAGE_EXTENSION),
        ("jpeg", StaticResourceReason.IMAGE_EXTENSION),
        ("svg", StaticResourceReason.IMAGE_EXTENSION),
        ("woff2", StaticResourceReason.FONT_EXTENSION),
        ("otf", StaticResourceReason.FONT_EXTENSION),
        ("css", StaticResourceReason.STYLESHEET_EXTENSION),
        ("js", StaticResourceReason.SCRIPT_EXTENSION),
        ("mjs", StaticResourceReason.SCRIPT_EXTENSION),
        ("mp3", StaticResourceReason.MEDIA_EXTENSION),
        ("mp4", StaticResourceReason.MEDIA_EXTENSION),
        ("avi", StaticResourceReason.MEDIA_EXTENSION),
    ],
)
def test_static_path_extensions_are_filtered(
    extension: str, expected: StaticResourceReason
) -> None:
    entry = _entry(url=f"https://capture.example.test/assets/fictional.{extension}")

    assert static_resource_filter_reason(entry) is expected


def test_uppercase_path_extension_is_filtered() -> None:
    entry = _entry(url="https://capture.example.test/assets/FICTIONAL.WEBP?version=1#preview")

    assert static_resource_filter_reason(entry) is StaticResourceReason.IMAGE_EXTENSION


def test_query_and_fragment_substrings_do_not_count_as_extensions() -> None:
    entry = _entry(
        url="https://capture.example.test/api/orders?next=application.js#asset.css"
    )

    assert static_resource_filter_reason(entry) is None


@pytest.mark.parametrize(
    "entry",
    [
        _entry(),
        _entry(mime_type="application/octet-stream"),
        _entry(url="https://capture.example.test/dashboard", mime_type="text/html"),
        _entry(url="https://capture.example.test/api/orders", mime_type="application/json"),
        {"request": {"method": "GET", "url": "https://capture.example.test/api/unknown"}},
    ],
)
def test_uncertain_and_business_entries_are_conservatively_kept(entry: dict) -> None:
    assert static_resource_filter_reason(entry) is None


def test_classification_is_deterministic_and_non_mutating() -> None:
    entry = _entry(
        url="https://capture.example.test/assets/fictional.css?cache=1",
        mime_type="application/unknown",
    )
    original = deepcopy(entry)

    first = static_resource_filter_reason(entry)
    second = static_resource_filter_reason(entry)

    assert first is StaticResourceReason.STYLESHEET_EXTENSION
    assert second is first
    assert entry == original
