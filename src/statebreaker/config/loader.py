"""Loading and scope validation for project configuration."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from statebreaker.config.models import ProjectConfig
from statebreaker.errors import ConfigError, ScopeViolationError


def load_project_config(path: str | Path) -> ProjectConfig:
    """Load and validate a ``project.yaml`` file."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"project config not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"project config must be a mapping: {config_path}")
    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid project config {config_path}: {exc}") from exc


def default_config_path(project_root: str | Path) -> Path:
    return Path(project_root) / "project.yaml"


class ScopeGuard:
    """Enforces allowed hosts and excluded paths on outbound requests."""

    def __init__(self, config: ProjectConfig) -> None:
        self._allowed_hosts = set(config.scope.allowed_hosts)
        self._excluded_paths = list(config.scope.excluded_paths)

    def check_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if self._allowed_hosts and host not in self._allowed_hosts:
            raise ScopeViolationError(f"host {host!r} is outside the allowed scope")
        path = parsed.path or "/"
        for excluded in self._excluded_paths:
            if path.startswith(excluded):
                raise ScopeViolationError(f"path {path!r} is excluded from the scope")
