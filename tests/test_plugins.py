from __future__ import annotations

from dataclasses import dataclass

import pytest

from statebreaker.errors import PluginError
from statebreaker.models import PluginManifest
from statebreaker.plugins import PluginRegistry


class FakeExecutor:
    manifest = PluginManifest(
        plugin_id="test.executor",
        name="Test executor",
        version="0.1.0",
        group="statebreaker.executor",
        capabilities=["test"],
    )

    async def execute(self, plan, runtime):  # pragma: no cover - contract shape only
        raise NotImplementedError


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    target: object

    def load(self):
        return self.target


class FakeEntryPoints(list):
    def select(self, *, group: str):
        return FakeEntryPoints(item for item in self if item.group == group)


def provider(*entry_points: FakeEntryPoint):
    return lambda: FakeEntryPoints(entry_points)


def test_registry_discovers_and_loads_plugin_class() -> None:
    registry = PluginRegistry(
        provider(FakeEntryPoint("test", "statebreaker.executor", FakeExecutor))
    )

    manifest, instance = registry.discover("statebreaker.executor")[0]
    assert manifest.plugin_id == "test.executor"
    assert isinstance(instance, FakeExecutor)
    assert isinstance(registry.get("statebreaker.executor", "test.executor"), FakeExecutor)


def test_registry_rejects_duplicate_plugin_ids() -> None:
    registry = PluginRegistry(
        provider(
            FakeEntryPoint("one", "statebreaker.executor", FakeExecutor),
            FakeEntryPoint("two", "statebreaker.executor", FakeExecutor),
        )
    )

    with pytest.raises(PluginError, match="duplicate"):
        registry.discover("statebreaker.executor")


def test_registry_rejects_manifest_group_mismatch() -> None:
    registry = PluginRegistry(
        provider(FakeEntryPoint("wrong", "statebreaker.capture", FakeExecutor))
    )

    with pytest.raises(PluginError, match="declares group"):
        registry.discover("statebreaker.capture")


def test_registry_reports_missing_plugin_cleanly() -> None:
    registry = PluginRegistry(provider())

    with pytest.raises(PluginError, match="No plugins installed"):
        registry.get("statebreaker.executor", "missing")
