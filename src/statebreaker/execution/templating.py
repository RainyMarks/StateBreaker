"""Rendering of ``${variable}`` templates into concrete requests."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from statebreaker.errors import TemplateError
from statebreaker.models.base import TEMPLATE_PATTERN
from statebreaker.models.capture import RequestTemplate

_FULL_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}$")


def render_string(text: str, variables: Mapping[str, Any]) -> Any:
    """Substitute ``${var}`` references inside ``text``.

    A string that is exactly one reference resolves to the raw value
    (preserving non-string types); embedded references produce a string.
    """
    full = _FULL_REFERENCE.match(text)
    if full:
        name = full.group(1)
        if name not in variables:
            raise TemplateError(f"missing variable {name!r}")
        return variables[name]

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise TemplateError(f"missing variable {name!r}")
        return str(variables[name])

    return TEMPLATE_PATTERN.sub(_replace, text)


def render_value(value: Any, variables: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return render_string(value, variables)
    if isinstance(value, dict):
        return {key: render_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [render_value(item, variables) for item in value]
    return value


class RenderedRequest:
    """A template with all variables resolved."""

    def __init__(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, Any],
        headers: dict[str, str],
        body: Any,
        body_encoding: str,
    ) -> None:
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.body_encoding = body_encoding

    def build_content(self) -> tuple[bytes | None, dict[str, str]]:
        """Encode the body; returns (content, extra headers)."""
        if self.body is None or self.body_encoding == "none":
            return None, {}
        if self.body_encoding == "json":
            return json.dumps(self.body).encode(), {"Content-Type": "application/json"}
        if self.body_encoding == "form":
            flat = {str(k): str(v) for k, v in dict(self.body).items()}
            return urlencode(flat).encode(), {
                "Content-Type": "application/x-www-form-urlencoded"
            }
        raw = self.body if isinstance(self.body, bytes) else str(self.body).encode()
        return raw, {}


def render_template(template: RequestTemplate, variables: Mapping[str, Any]) -> RenderedRequest:
    """Resolve every placeholder in a request template."""
    try:
        path = str(render_string(template.path_template, variables))
        query = {
            str(key): render_value(value, variables) for key, value in template.query.items()
        }
        headers = {
            str(key): str(render_value(value, variables))
            for key, value in template.headers.items()
        }
        body = render_value(template.body, variables)
    except TemplateError as exc:
        raise TemplateError(f"template {template.template_id!r}: {exc}") from exc
    return RenderedRequest(
        method=template.method.upper(),
        path=path,
        query=query,
        headers=headers,
        body=body,
        body_encoding=template.body_encoding,
    )
