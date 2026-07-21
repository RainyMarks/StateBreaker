"""Project config loading, defaults, and scope enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from statebreaker.config.loader import ScopeGuard, load_project_config
from statebreaker.errors import ConfigError, ScopeViolationError

CONFIG_YAML = """
schema_version: "0.2"
project:
  name: target-system
  base_url: http://127.0.0.1:8080
scope:
  allowed_hosts:
    - 127.0.0.1
  excluded_paths:
    - /logout
  requests_per_second: 10
sessions:
  alice:
    capture_context: alice
  bob: {}
discovery:
  max_candidates: 5
execution:
  schedulers:
    - async-http
budget:
  maximum_requests: 100
"""


def test_load_project_config(tmp_path: Path) -> None:
    config_file = tmp_path / "project.yaml"
    config_file.write_text(CONFIG_YAML, encoding="utf-8")
    config = load_project_config(config_file)
    assert config.project.name == "target-system"
    assert config.scope.allowed_hosts == ["127.0.0.1"]
    assert config.sessions["alice"].capture_context == "alice"
    assert config.discovery.max_candidates == 5
    assert config.budget.maximum_requests == 100
    assert config.execution.schedulers == ["async-http"]
    assert config.oracle.require_state_evidence_for_confirmed is True


def test_load_project_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_project_config(tmp_path / "nope.yaml")


def test_load_project_config_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "project.yaml"
    bad.write_text("project: 42\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_project_config(bad)


def test_scope_guard(tmp_path: Path) -> None:
    config_file = tmp_path / "project.yaml"
    config_file.write_text(CONFIG_YAML, encoding="utf-8")
    guard = ScopeGuard(load_project_config(config_file))
    guard.check_url("http://127.0.0.1:8080/api/thing")
    with pytest.raises(ScopeViolationError):
        guard.check_url("http://evil.example.com/api/thing")
    with pytest.raises(ScopeViolationError):
        guard.check_url("http://127.0.0.1:8080/logout")
