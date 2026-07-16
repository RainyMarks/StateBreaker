"""YAML/JSON loading and deterministic artifact writing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from statebreaker.errors import DocumentError

ModelT = TypeVar("ModelT", bound=BaseModel)


def read_data(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DocumentError(f"cannot read {path}: {exc}") from exc
    try:
        if path.suffix.lower() == ".json":
            return json.loads(text)
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise DocumentError(f"invalid document {path}: {exc}") from exc
    raise DocumentError(f"unsupported document extension for {path}; use .json/.yaml/.yml")


def load_model(path: Path, model_type: type[ModelT]) -> ModelT:
    try:
        return model_type.model_validate(read_data(path))
    except ValidationError as exc:
        raise DocumentError(f"{path} failed validation:\n{exc}") from exc


def load_typed(path: Path, annotation: Any) -> Any:
    try:
        return TypeAdapter(annotation).validate_python(read_data(path))
    except ValidationError as exc:
        raise DocumentError(f"{path} failed validation:\n{exc}") from exc


def write_json(path: Path, value: BaseModel | list[BaseModel] | dict[str, Any] | Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Any
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, list):
        payload = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in value
        ]
    else:
        payload = value
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
