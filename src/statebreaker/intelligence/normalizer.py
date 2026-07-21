"""Trace normalization: filter noise, canonicalize headers, tag path shapes."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from statebreaker.intelligence.value_types import classify_value
from statebreaker.models.capture import CapturedTrace, HttpExchange

_STATIC_EXTENSIONS = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
    ".webp",
    ".mp4",
)

_NOISE_PATH_HINTS = ("analytics", "telemetry", "heartbeat", "ping", "healthz", "metrics")


def is_noise_exchange(exchange: HttpExchange) -> bool:
    """Static assets and telemetry are never interesting for workflow logic."""
    path = urlparse(exchange.url).path.lower()
    if path.endswith(_STATIC_EXTENSIONS):
        return True
    return any(hint in path for hint in _NOISE_PATH_HINTS)


def dynamic_path_shape(path: str) -> str:
    """Replace dynamic-looking path segments with ``*`` for grouping."""
    segments = []
    for segment in path.split("/"):
        if segment and classify_value(segment) in {"uuid", "numeric_id", "token"}:
            segments.append("*")
        else:
            segments.append(segment)
    return "/".join(segments)


def normalize_exchange(exchange: HttpExchange) -> HttpExchange:
    """Canonical header casing and a path shape for grouping."""
    update = {
        "request_headers": {k.lower(): v for k, v in exchange.request_headers.items()},
        "response_headers": {k.lower(): v for k, v in exchange.response_headers.items()},
        "path_template": dynamic_path_shape(urlparse(exchange.url).path),
    }
    return exchange.model_copy(update=update)


def normalize_trace(
    trace: CapturedTrace,
    *,
    base_url: str | None = None,
    drop_noise: bool = True,
) -> CapturedTrace:
    """Filter and canonicalize a captured trace in-place-by-copy.

    Third-party hosts (relative to ``base_url`` or the most common host in the
    trace) are dropped: they are outside the target scope by definition.
    """
    exchanges = [normalize_exchange(e) for e in trace.exchanges]
    if drop_noise:
        exchanges = [e for e in exchanges if not is_noise_exchange(e)]

    reference_host = urlparse(base_url or trace.base_url or "").hostname
    if reference_host is None and exchanges:
        hosts: dict[str, int] = {}
        for exchange in exchanges:
            host = urlparse(exchange.url).hostname or ""
            hosts[host] = hosts.get(host, 0) + 1
        reference_host = max(hosts, key=lambda h: hosts[h]) if hosts else None
    if reference_host:
        exchanges = [
            e for e in exchanges if (urlparse(e.url).hostname or "") == reference_host
        ]

    keep_ids = {e.exchange_id for e in exchanges}
    actions = [
        action.model_copy(
            update={
                "triggered_exchange_ids": [
                    xid for xid in action.triggered_exchange_ids if xid in keep_ids
                ]
            }
        )
        for action in trace.actions
    ]
    return trace.model_copy(update={"exchanges": exchanges, "actions": actions})


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*(html|div|body|span)\b", text[:2000], re.IGNORECASE))
