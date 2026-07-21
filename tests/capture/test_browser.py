"""Browser CDP capture normalization tests."""

from __future__ import annotations

import base64

import pytest

from statebreaker.capture.browser import ExchangeTracker, find_browser_executable
from statebreaker.errors import CaptureError


def test_browser_tracker_records_json_and_form_exchange() -> None:
    tracker = ExchangeTracker()

    tracker.request_will_be_sent(
        {
            "requestId": "1",
            "timestamp": 10.0,
            "wallTime": 1_700_000_000.0,
            "request": {
                "method": "POST",
                "url": "https://example.test/api",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                "postData": "a=1&b=two",
            },
        }
    )
    tracker.response_received(
        {
            "requestId": "1",
            "response": {
                "status": 201,
                "headers": {"Content-Type": "application/json"},
                "mimeType": "application/json",
            },
        }
    )
    exchange = tracker.loading_finished(
        {"requestId": "1", "timestamp": 10.25},
        body=base64.b64encode(b'{"ok": true}').decode("ascii"),
        base64_encoded=True,
    )

    assert exchange is not None
    assert exchange.method == "POST"
    assert exchange.url == "https://example.test/api"
    assert exchange.request_headers == {"content-type": "application/x-www-form-urlencoded"}
    assert exchange.request_body == {"a": "1", "b": "two"}
    assert exchange.request_body_encoding == "form"
    assert exchange.response_status == 201
    assert exchange.response_body == {"ok": True}
    assert exchange.response_body_encoding == "json"
    assert exchange.started_at_ns == 1_700_000_000_000_000_000
    assert exchange.completed_at_ns == 1_700_000_000_250_000_000


def test_browser_tracker_handles_failed_redirect_and_ignored_urls() -> None:
    tracker = ExchangeTracker()
    tracker.request_will_be_sent(
        {
            "requestId": "ignored",
            "timestamp": 1.0,
            "wallTime": 100.0,
            "request": {"method": "GET", "url": "data:text/plain,hello"},
        }
    )
    tracker.request_will_be_sent(
        {
            "requestId": "failed",
            "timestamp": 2.0,
            "wallTime": 200.0,
            "request": {"method": "GET", "url": "https://example.test/fail"},
        }
    )
    failed = tracker.loading_failed({"requestId": "failed", "timestamp": 2.1})

    tracker.request_will_be_sent(
        {
            "requestId": "redir",
            "timestamp": 3.0,
            "wallTime": 300.0,
            "request": {"method": "GET", "url": "https://example.test/old"},
        }
    )
    tracker.request_will_be_sent(
        {
            "requestId": "redir",
            "timestamp": 3.2,
            "wallTime": 300.2,
            "redirectResponse": {"status": 302, "headers": {"Location": "/new"}},
            "request": {"method": "GET", "url": "https://example.test/new"},
        }
    )
    tracker.response_received(
        {
            "requestId": "redir",
            "response": {"status": 200, "headers": {}, "mimeType": "text/plain"},
        }
    )
    final = tracker.loading_finished(
        {"requestId": "redir", "timestamp": 3.3},
        body="done",
        base64_encoded=False,
    )

    assert failed is not None
    assert failed.response_status == 0
    assert final is not None
    assert [exchange.url for exchange in tracker.exchanges] == [
        "https://example.test/fail",
        "https://example.test/old",
        "https://example.test/new",
    ]
    assert tracker.exchanges[1].response_status == 302
    assert tracker.exchanges[2].response_status == 200


def test_find_browser_executable_raises_capture_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STATEBREAKER_BROWSER", raising=False)
    monkeypatch.setattr("statebreaker.capture.browser.shutil.which", lambda name: None)
    monkeypatch.setattr("statebreaker.capture.browser.sys.platform", "linux")

    with pytest.raises(CaptureError):
        find_browser_executable()
