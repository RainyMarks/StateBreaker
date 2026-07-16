"""Plugin protocols and package-metadata discovery."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from statebreaker.errors import PluginError
from statebreaker.models import (
    API_VERSION,
    AttackPlan,
    Finding,
    Invariant,
    LearningResult,
    PluginManifest,
    RawAttackResult,
    ReportArtifacts,
    RunBundle,
    Workflow,
)
from statebreaker.runtime import ExecutionRuntime

PLUGIN_GROUPS = (
    "statebreaker.capture",
    "statebreaker.learner",
    "statebreaker.generator",
    "statebreaker.executor",
    "statebreaker.verifier",
    "statebreaker.reporter",
)


@runtime_checkable
class CapturePlugin(Protocol):
    manifest: PluginManifest

    async def capture(self, source: Path, options: dict[str, Any]) -> Workflow: ...


@runtime_checkable
class LearnerPlugin(Protocol):
    manifest: PluginManifest

    async def learn(self, workflow: Workflow, runtime: ExecutionRuntime) -> LearningResult: ...


@runtime_checkable
class GeneratorPlugin(Protocol):
    manifest: PluginManifest

    async def generate(
        self, workflow: Workflow, invariants: list[Invariant]
    ) -> list[AttackPlan]: ...


@runtime_checkable
class ExecutorPlugin(Protocol):
    manifest: PluginManifest

    async def execute(
        self, plan: AttackPlan, runtime: ExecutionRuntime
    ) -> RawAttackResult: ...


@runtime_checkable
class VerifierPlugin(Protocol):
    manifest: PluginManifest

    async def verify(
        self, result: RawAttackResult, invariants: list[Invariant]
    ) -> list[Finding]: ...


@runtime_checkable
class ReporterPlugin(Protocol):
    manifest: PluginManifest

    async def render(self, bundle: RunBundle, output_dir: Path) -> ReportArtifacts: ...


class PluginRegistry:
    def __init__(
        self,
        entry_points_provider: Callable[[], Any] = metadata.entry_points,
    ) -> None:
        self._entry_points_provider = entry_points_provider

    def _entry_points(self, group: str) -> Iterable[Any]:
        if group not in PLUGIN_GROUPS:
            raise PluginError(f"unknown plugin group: {group}")
        discovered = self._entry_points_provider()
        if hasattr(discovered, "select"):
            return list(discovered.select(group=group))
        return list(discovered.get(group, ()))

    @staticmethod
    def _instantiate(entry_point: Any) -> Any:
        try:
            loaded = entry_point.load()
            return loaded() if inspect.isclass(loaded) else loaded
        except Exception as exc:  # plugin import failures must remain user-readable
            raise PluginError(
                f"failed to load plugin entry point {entry_point.name!r}: {exc}"
            ) from exc

    @staticmethod
    def _validate(instance: Any, expected_group: str) -> PluginManifest:
        try:
            manifest = PluginManifest.model_validate(instance.manifest)
        except Exception as exc:
            raise PluginError(
                f"plugin in {expected_group!r} has an invalid manifest: {exc}"
            ) from exc
        if manifest.group != expected_group:
            raise PluginError(
                f"plugin {manifest.plugin_id!r} declares group {manifest.group!r}, "
                f"expected {expected_group!r}"
            )
        if manifest.api_version != API_VERSION:
            raise PluginError(
                f"plugin {manifest.plugin_id!r} requires API {manifest.api_version}, "
                f"core provides {API_VERSION}"
            )
        method_name = expected_group.rsplit(".", maxsplit=1)[-1]
        if expected_group == "statebreaker.executor":
            method_name = "execute"
        elif expected_group == "statebreaker.capture":
            method_name = "capture"
        elif expected_group == "statebreaker.learner":
            method_name = "learn"
        elif expected_group == "statebreaker.generator":
            method_name = "generate"
        elif expected_group == "statebreaker.verifier":
            method_name = "verify"
        elif expected_group == "statebreaker.reporter":
            method_name = "render"
        if not callable(getattr(instance, method_name, None)):
            raise PluginError(
                f"plugin {manifest.plugin_id!r} does not implement async method {method_name}()"
            )
        return manifest

    def discover(self, group: str | None = None) -> list[tuple[PluginManifest, Any]]:
        groups = (group,) if group else PLUGIN_GROUPS
        found: list[tuple[PluginManifest, Any]] = []
        seen: set[tuple[str, str]] = set()
        for current_group in groups:
            for entry_point in self._entry_points(current_group):
                instance = self._instantiate(entry_point)
                manifest = self._validate(instance, current_group)
                key = (current_group, manifest.plugin_id)
                if key in seen:
                    raise PluginError(
                        f"duplicate plugin id {manifest.plugin_id!r} in group {current_group!r}"
                    )
                seen.add(key)
                found.append((manifest, instance))
        return sorted(found, key=lambda item: (item[0].group, item[0].plugin_id))

    def get(self, group: str, plugin_id: str) -> Any:
        matches = [
            instance
            for manifest, instance in self.discover(group)
            if manifest.plugin_id == plugin_id
        ]
        if not matches:
            available = [manifest.plugin_id for manifest, _ in self.discover(group)]
            suffix = (
                f" Available: {', '.join(available)}" if available else " No plugins installed."
            )
            raise PluginError(f"plugin {plugin_id!r} not found in {group!r}.{suffix}")
        return matches[0]
