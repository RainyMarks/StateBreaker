"""Conservative, non-mutating classification of static HAR resources."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit


class StaticResourceReason(StrEnum):
    """Stable, safe categories explaining why a HAR entry was filtered."""

    IMAGE_RESOURCE_TYPE = "image resource type"
    FONT_RESOURCE_TYPE = "font resource type"
    STYLESHEET_RESOURCE_TYPE = "stylesheet resource type"
    SCRIPT_RESOURCE_TYPE = "script resource type"
    MEDIA_RESOURCE_TYPE = "media resource type"
    IMAGE_MIME = "image MIME"
    FONT_MIME = "font MIME"
    AUDIO_MIME = "audio MIME"
    VIDEO_MIME = "video MIME"
    CSS_MIME = "CSS MIME"
    JAVASCRIPT_MIME = "JavaScript MIME"
    IMAGE_EXTENSION = "image extension"
    FONT_EXTENSION = "font extension"
    STYLESHEET_EXTENSION = "stylesheet extension"
    SCRIPT_EXTENSION = "script extension"
    MEDIA_EXTENSION = "media extension"


_FILTERED_RESOURCE_TYPES = {
    "image": StaticResourceReason.IMAGE_RESOURCE_TYPE,
    "font": StaticResourceReason.FONT_RESOURCE_TYPE,
    "stylesheet": StaticResourceReason.STYLESHEET_RESOURCE_TYPE,
    "script": StaticResourceReason.SCRIPT_RESOURCE_TYPE,
    "media": StaticResourceReason.MEDIA_RESOURCE_TYPE,
}
_FILTERED_MIME_TYPES = {
    "text/css": StaticResourceReason.CSS_MIME,
    "application/javascript": StaticResourceReason.JAVASCRIPT_MIME,
    "text/javascript": StaticResourceReason.JAVASCRIPT_MIME,
    "application/ecmascript": StaticResourceReason.JAVASCRIPT_MIME,
    "text/ecmascript": StaticResourceReason.JAVASCRIPT_MIME,
}
_FILTERED_MIME_PREFIXES = {
    "image/": StaticResourceReason.IMAGE_MIME,
    "font/": StaticResourceReason.FONT_MIME,
    "audio/": StaticResourceReason.AUDIO_MIME,
    "video/": StaticResourceReason.VIDEO_MIME,
}
_FILTERED_EXTENSIONS = {
    **{
        extension: StaticResourceReason.IMAGE_EXTENSION
        for extension in {
            "png",
            "jpg",
            "jpeg",
            "gif",
            "webp",
            "svg",
            "ico",
            "avif",
            "bmp",
            "tiff",
        }
    },
    **{
        extension: StaticResourceReason.FONT_EXTENSION
        for extension in {"woff", "woff2", "ttf", "otf", "eot"}
    },
    "css": StaticResourceReason.STYLESHEET_EXTENSION,
    **{
        extension: StaticResourceReason.SCRIPT_EXTENSION
        for extension in {"js", "mjs", "cjs"}
    },
    **{
        extension: StaticResourceReason.MEDIA_EXTENSION
        for extension in {"mp3", "mp4", "webm", "ogg", "wav", "m4a", "mov", "avi"}
    },
}


def _normalized_resource_type(entry: Mapping[str, Any]) -> str | None:
    resource_type = entry.get("_resourceType")
    return resource_type.strip().lower() if isinstance(resource_type, str) else None


def _normalized_response_mime(entry: Mapping[str, Any]) -> str | None:
    response = entry.get("response")
    if not isinstance(response, Mapping):
        return None
    content = response.get("content")
    if not isinstance(content, Mapping):
        return None
    mime_type = content.get("mimeType")
    if not isinstance(mime_type, str):
        return None
    normalized = mime_type.split(";", maxsplit=1)[0].strip().lower()
    return normalized or None


def _is_json_mime(mime_type: str | None) -> bool:
    if mime_type == "application/json":
        return True
    if mime_type is None or "/" not in mime_type:
        return False
    return mime_type.rsplit("/", maxsplit=1)[1].endswith("+json")


def _url_extension(entry: Mapping[str, Any]) -> str | None:
    request = entry.get("request")
    if not isinstance(request, Mapping):
        return None
    url = request.get("url")
    if not isinstance(url, str):
        return None
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    suffix = PurePosixPath(path).suffix
    return suffix[1:].lower() if suffix.startswith(".") and len(suffix) > 1 else None


def static_resource_filter_reason(
    entry: Mapping[str, Any],
) -> StaticResourceReason | None:
    """Return a filter reason, or None when the entry must be kept.

    The classifier is intentionally conservative and never mutates *entry*.
    """

    resource_type = _normalized_resource_type(entry)
    mime_type = _normalized_response_mime(entry)

    # Explicit business-request signals outrank every static-resource signal.
    if resource_type in {"fetch", "xhr"} or _is_json_mime(mime_type):
        return None

    resource_type_reason = _FILTERED_RESOURCE_TYPES.get(resource_type or "")
    if resource_type_reason is not None:
        return resource_type_reason

    if mime_type is not None:
        for prefix, reason in _FILTERED_MIME_PREFIXES.items():
            if mime_type.startswith(prefix):
                return reason
        mime_reason = _FILTERED_MIME_TYPES.get(mime_type)
        if mime_reason is not None:
            return mime_reason

    extension = _url_extension(entry)
    return _FILTERED_EXTENSIONS.get(extension or "")
