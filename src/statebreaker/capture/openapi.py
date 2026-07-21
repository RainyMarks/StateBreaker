"""OpenAPI 3.x adapter: derive request templates from an API description."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from statebreaker.errors import CaptureError
from statebreaker.models.capture import BodyEncoding, RequestTemplate

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head")
_PATH_PARAM = re.compile(r"\{([^{}]+)\}")


def parse_openapi(data: dict[str, Any], *, source_name: str = "openapi") -> list[RequestTemplate]:
    """Turn an OpenAPI document into one template per operation."""
    paths = data.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise CaptureError(f"OpenAPI document {source_name!r} has no paths")
    schemas = (data.get("components") or {}).get("schemas") or {}
    templates: list[RequestTemplate] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters") or []
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            templates.append(
                _operation_template(
                    str(path),
                    method,
                    operation,
                    list(shared_params) if isinstance(shared_params, list) else [],
                    schemas,
                )
            )
    return templates


def load_openapi(path: str | Path, *, source_name: str | None = None) -> list[RequestTemplate]:
    """Load an OpenAPI document (JSON or YAML) from disk."""
    spec_path = Path(path)
    if not spec_path.exists():
        raise CaptureError(f"OpenAPI document not found: {spec_path}")
    try:
        data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CaptureError(f"invalid YAML/JSON in {spec_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptureError(f"OpenAPI document must be a mapping: {spec_path}")
    return parse_openapi(data, source_name=source_name or spec_path.stem)


def _operation_template(
    path: str,
    method: str,
    operation: dict[str, Any],
    shared_params: list[Any],
    schemas: dict[str, Any],
) -> RequestTemplate:
    path_template = _PATH_PARAM.sub(lambda m: "${" + m.group(1) + "}", path)
    template_id = operation.get("operationId") or _fallback_id(method, path)

    query: dict[str, str] = {}
    headers: dict[str, str] = {}
    parameters = list(shared_params) + list(operation.get("parameters") or [])
    for parameter in parameters:
        if not isinstance(parameter, dict) or not parameter.get("required"):
            continue
        name = str(parameter.get("name", ""))
        if not name:
            continue
        location = parameter.get("in")
        if location == "query":
            query[name] = "${" + name + "}"
        elif location == "header":
            headers[name] = "${" + name + "}"

    body: Any = None
    encoding: BodyEncoding = "none"
    request_body = operation.get("requestBody") or {}
    content = request_body.get("content") or {}
    for mime, media in content.items():
        if "json" in str(mime) and isinstance(media, dict):
            body = _schema_skeleton(media.get("schema") or {}, schemas, depth=0)
            encoding = "json"
            break

    return RequestTemplate(
        template_id=str(template_id),
        method=method.upper(),
        path_template=path_template,
        query=query,
        headers=headers,
        body=body,
        body_encoding=encoding,
    )


def _fallback_id(method: str, path: str) -> str:
    slug = re.sub(r"[/{}/]+", "-", path).strip("-")
    return f"{method}-{slug}" if slug else method


def _schema_skeleton(schema: dict[str, Any], schemas: dict[str, Any], *, depth: int) -> Any:
    """Generate a placeholder JSON value from a schema (one $ref level deep)."""
    if depth > 4:
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        target = schemas.get(name)
        if isinstance(target, dict):
            return _schema_skeleton(target, schemas, depth=depth + 1)
        return None
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        return {
            str(key): _schema_skeleton(value, schemas, depth=depth + 1)
            for key, value in properties.items()
            if isinstance(value, dict)
        }
    if schema_type == "array":
        items = schema.get("items") or {}
        return [_schema_skeleton(items, schemas, depth=depth + 1)] if items else []
    if schema_type in ("integer", "number"):
        return 0
    if schema_type == "boolean":
        return False
    return ""
