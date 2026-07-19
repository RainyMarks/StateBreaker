"""Conservative, case-insensitive filtering of browser-managed request headers."""


BROWSER_MANAGED_HEADER_NAMES = frozenset(
    {
        "accept-encoding",
        "cache-control",
        "connection",
        "content-length",
        "dnt",
        "host",
        "if-modified-since",
        "if-none-match",
        "keep-alive",
        "pragma",
        "priority",
        "proxy-connection",
        "sec-gpc",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
    }
)
BROWSER_MANAGED_HEADER_PREFIXES = (
    "sec-fetch-",
    "sec-ch-",
    "sec-websocket-",
)


def is_browser_managed_header(name: str) -> bool:
    """Return whether *name* belongs to the explicit browser denylist."""

    normalized_name = name.casefold()
    return (
        normalized_name.startswith(":")
        or normalized_name in BROWSER_MANAGED_HEADER_NAMES
        or normalized_name.startswith(BROWSER_MANAGED_HEADER_PREFIXES)
    )
