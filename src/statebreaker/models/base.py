"""Versioned base contracts shared by every StateBreaker model.

All models are business-agnostic: they describe HTTP traffic, workflows,
state, and race experiments in abstract terms only.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "0.2"
"""Current schema version written by this build."""

SUPPORTED_READ_VERSIONS = ("0.1", "0.2")
"""Schema versions this build can load (via migration for older ones)."""

IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]*$"
TEMPLATE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


def utc_now() -> datetime:
    return datetime.now(UTC)


def template_variables(value: Any) -> set[str]:
    """Collect ``${variable}`` references from an arbitrary JSON-like value."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(match.group(1) for match in TEMPLATE_PATTERN.finditer(value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.update(template_variables(key))
            found.update(template_variables(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(template_variables(item))
    return found


class ContractModel(BaseModel):
    """Strict, versioned base for every StateBreaker model."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = SCHEMA_VERSION

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        return cls.model_validate_json(payload)
