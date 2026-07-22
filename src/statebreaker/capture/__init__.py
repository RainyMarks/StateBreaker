"""Capture adapters: HAR, Postman, OpenAPI, HTTP proxy, and browser recording."""

from statebreaker.capture.har import load_har, parse_har
from statebreaker.capture.openapi import load_openapi, parse_openapi
from statebreaker.capture.postman import load_postman, parse_postman
from statebreaker.capture.proxy import (
    HttpProxyRecorder,
    is_loopback_listen_host,
    start_http_proxy_recorder,
)

__all__ = [
    "HttpProxyRecorder",
    "is_loopback_listen_host",
    "load_har",
    "load_openapi",
    "load_postman",
    "parse_har",
    "parse_openapi",
    "parse_postman",
    "record_browser_trace",
    "start_http_proxy_recorder",
]


def __getattr__(name: str) -> object:
    if name == "record_browser_trace":
        from statebreaker.capture.browser import record_browser_trace

        return record_browser_trace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
