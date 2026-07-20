from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.header_normalizer import (
    BROWSER_MANAGED_HEADER_NAMES,
    BROWSER_MANAGED_HEADER_PREFIXES,
    is_browser_managed_header,
)
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions

DENIED_EXACT_HEADERS = [
    "Host",
    "Content-Length",
    "Transfer-Encoding",
    "Connection",
    "Proxy-Connection",
    "Keep-Alive",
    "Upgrade",
    "TE",
    "Trailer",
    "Accept-Encoding",
    "User-Agent",
    "Priority",
    "DNT",
    "Sec-GPC",
    "Cache-Control",
    "Pragma",
    "If-None-Match",
    "If-Modified-Since",
]
DENIED_PREFIX_HEADERS = [
    "Sec-Fetch-Site",
    "Sec-Fetch-Mode",
    "Sec-Fetch-Dest",
    "Sec-CH-UA",
    "Sec-CH-UA-Mobile",
    "Sec-CH-UA-Platform",
    "Sec-WebSocket-Key",
    "Sec-WebSocket-Version",
]
RETAINED_HEADERS = {
    "Content-Type": "application/json; profile=synthetic",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.5",
    "Origin": "https://origin.synthetic.test",
    "Referer": "https://referer.synthetic.test/page",
    "Authorization": "Bearer synthetic-test-credential",
    "Cookie": "synthetic_session=test-only",
    "X-Requested-With": "SyntheticClient",
    "X-Capture-Test": "preserve-exact-value",
    "Idempotency-Key": "synthetic-idempotency-key",
    "If-Match": '"synthetic-etag"',
    "If-Unmodified-Since": "Sat, 01 Jan 2000 00:00:00 GMT",
    "Range": "bytes=0-99",
    "Application-Mode": "synthetic-mode",
}
LEGACY_REMOVED_HEADERS = {
    "connection",
    "content-length",
    "host",
    "proxy-authorization",
    "transfer-encoding",
}


def _header_items(headers: dict[str, str] | list[tuple[str, str]]) -> list[dict[str, str]]:
    items = headers.items() if isinstance(headers, dict) else headers
    return [{"name": name, "value": value} for name, value in items]


def _entry(
    headers: dict[str, str] | list[tuple[str, str]] | Any,
    *,
    url: str = "https://headers.synthetic.test/api/check",
    resource_type: str = "xhr",
) -> dict[str, Any]:
    return {
        "_resourceType": resource_type,
        "request": {
            "method": "GET",
            "url": url,
            "headers": _header_items(headers)
            if isinstance(headers, (dict, list))
            else headers,
        },
        "response": {"content": {"mimeType": "application/json", "text": "{}"}},
    }


def _document(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"log": {"version": "1.2", "entries": list(entries)}}


def _normalized_headers(
    headers: dict[str, str] | list[tuple[str, str]],
    **options: bool,
) -> dict[str, str]:
    candidate = normalize_har(
        _document(_entry(headers)),
        HarCaptureOptions.model_validate(options),
    )
    return candidate["steps"][0]["request"]["headers"]


def test_normalize_browser_headers_option_is_strict_and_defaults_true() -> None:
    assert HarCaptureOptions().normalize_browser_headers is True
    assert HarCaptureOptions(normalize_browser_headers=True).normalize_browser_headers is True
    assert HarCaptureOptions(normalize_browser_headers=False).normalize_browser_headers is False
    for value in ("true", 1, None):
        with pytest.raises(ValidationError, match="valid boolean"):
            HarCaptureOptions.model_validate({"normalize_browser_headers": value})


def test_unknown_option_remains_forbidden() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HarCaptureOptions.model_validate({"normalize_headers": True})


@pytest.mark.parametrize("name", DENIED_EXACT_HEADERS)
def test_exact_browser_managed_headers_are_case_insensitively_denied(name: str) -> None:
    assert is_browser_managed_header(name)
    assert is_browser_managed_header(name.lower())
    assert is_browser_managed_header(name.upper())


@pytest.mark.parametrize("name", DENIED_PREFIX_HEADERS)
def test_browser_prefix_headers_are_case_insensitively_denied(name: str) -> None:
    assert is_browser_managed_header(name)
    assert is_browser_managed_header(name.lower())
    assert is_browser_managed_header(name.upper())


@pytest.mark.parametrize("name", [":authority", ":method", ":path", ":scheme"])
def test_http_pseudo_headers_are_denied(name: str) -> None:
    assert is_browser_managed_header(name)


def test_denylist_constants_are_order_independent_immutable_collections() -> None:
    assert isinstance(BROWSER_MANAGED_HEADER_NAMES, frozenset)
    assert set(BROWSER_MANAGED_HEADER_PREFIXES) == {
        "sec-fetch-",
        "sec-ch-",
        "sec-websocket-",
    }


def test_all_application_headers_and_values_are_preserved_by_default() -> None:
    headers = _normalized_headers(RETAINED_HEADERS)

    expected = {name.lower(): value for name, value in RETAINED_HEADERS.items()}
    assert headers == expected
    assert all(not is_browser_managed_header(name) for name in RETAINED_HEADERS)


def test_full_denylist_is_removed_before_request_spec_creation() -> None:
    source = [
        *((name, f"synthetic-denied-{index}") for index, name in enumerate(DENIED_EXACT_HEADERS)),
        *((name, f"synthetic-prefix-{index}") for index, name in enumerate(DENIED_PREFIX_HEADERS)),
        (":authority", "headers.synthetic.test"),
        ("X-Capture-Test", "retained-value"),
    ]

    headers = _normalized_headers(source)

    assert headers == {"x-capture-test": "retained-value"}


@pytest.mark.parametrize(
    (
        "normalize_browser_headers",
        "strip_credentials",
        "expected_names",
    ),
    [
        (True, False, {"authorization", "cookie", "x-capture-test"}),
        (True, True, {"x-capture-test"}),
        (
            False,
            False,
            {
                "accept-encoding",
                "authorization",
                "cache-control",
                "cookie",
                "sec-fetch-site",
                "user-agent",
                "x-capture-test",
            },
        ),
        (
            False,
            True,
            {
                "accept-encoding",
                "cache-control",
                "sec-fetch-site",
                "user-agent",
                "x-capture-test",
            },
        ),
    ],
)
def test_normalization_and_credential_stripping_are_independent(
    normalize_browser_headers: bool,
    strip_credentials: bool,
    expected_names: set[str],
) -> None:
    source = {
        "Host": "headers.synthetic.test",
        "Content-Length": "999",
        "Connection": "synthetic-connection",
        "Transfer-Encoding": "synthetic-transfer",
        "Proxy-Authorization": "synthetic-proxy-credential",
        "User-Agent": "SyntheticBrowser/1.0",
        "Sec-Fetch-Site": "same-origin",
        "Accept-Encoding": "synthetic-encoding",
        "Cache-Control": "no-cache",
        "Authorization": "Bearer synthetic-test-credential",
        "Cookie": "synthetic_session=test-only",
        "X-Capture-Test": "retained-value",
    }

    headers = _normalized_headers(
        source,
        normalize_browser_headers=normalize_browser_headers,
        strip_credentials=strip_credentials,
    )

    assert set(headers) == expected_names
    assert not (set(headers) & LEGACY_REMOVED_HEADERS)


def test_credential_names_are_case_insensitive() -> None:
    headers = _normalized_headers(
        {
            "AUTHORIZATION": "Bearer synthetic-test-credential",
            "cOoKiE": "synthetic_session=test-only",
            "X-Capture-Test": "retained-value",
        },
        strip_credentials=True,
    )

    assert headers == {"x-capture-test": "retained-value"}


def test_empty_header_list_is_supported() -> None:
    assert _normalized_headers([]) == {}


def test_header_name_casing_is_semantically_equivalent() -> None:
    title_case = _normalized_headers(
        {"User-Agent": "removed", "X-Capture-Test": "same-value"}
    )
    upper_case = _normalized_headers(
        {"USER-AGENT": "removed", "X-CAPTURE-TEST": "same-value"}
    )

    assert title_case == upper_case == {"x-capture-test": "same-value"}


def test_normalization_is_deterministic_and_does_not_mutate_har() -> None:
    document = _document(
        _entry(
            {
                "User-Agent": "SyntheticBrowser/1.0",
                "X-Capture-Test": "preserve-exact-value",
            }
        )
    )
    original = deepcopy(document)
    options = HarCaptureOptions()

    first = normalize_har(document, options)
    second = normalize_har(document, options)

    assert first == second
    assert document == original
    assert first["steps"][0]["request"]["headers"] == {
        "x-capture-test": "preserve-exact-value"
    }


def test_excluded_entry_headers_are_never_processed() -> None:
    document = _document(
        _entry("invalid excluded header collection", url="https://excluded.test/"),
        _entry({"X-Capture-Test": "retained"}),
    )

    candidate = normalize_har(
        document,
        HarCaptureOptions(exclude_entry_indices=[0]),
    )

    assert len(candidate["steps"]) == 1
    assert candidate["steps"][0]["request"]["headers"] == {
        "x-capture-test": "retained"
    }


def test_statically_filtered_entry_headers_are_never_processed() -> None:
    filtered = _entry(
        "invalid filtered header collection",
        url="https://headers.synthetic.test/static/app.js",
        resource_type="script",
    )
    filtered["response"]["content"] = {"mimeType": "text/javascript"}
    document = _document(
        filtered,
        _entry({"X-Capture-Test": "retained"}),
    )

    candidate = normalize_har(document, HarCaptureOptions())

    assert len(candidate["steps"]) == 1
    assert candidate["steps"][0]["request"]["headers"] == {
        "x-capture-test": "retained"
    }


def test_filter_disabled_keeps_static_entry_but_still_normalizes_headers() -> None:
    document = _document(
        _entry(
            {
                "User-Agent": "SyntheticBrowser/1.0",
                "Content-Type": "application/javascript",
            },
            url="https://headers.synthetic.test/static/app.js",
            resource_type="script",
        )
    )

    candidate = normalize_har(
        document,
        HarCaptureOptions(filter_static_resources=False),
    )

    assert candidate["steps"][0]["request"]["headers"] == {
        "content-type": "application/javascript"
    }


@pytest.mark.parametrize("normalize_browser_headers", [True, False])
def test_duplicate_retained_header_behavior_is_unchanged(
    normalize_browser_headers: bool,
) -> None:
    document = _document(
        _entry(
            [
                ("X-Capture-Test", "first"),
                ("x-capture-test", "second"),
            ]
        )
    )

    with pytest.raises(HarCaptureError, match="duplicate retained header name") as error:
        normalize_har(
            document,
            HarCaptureOptions(normalize_browser_headers=normalize_browser_headers),
        )

    assert "x-capture-test" not in str(error.value).casefold()
