"""Copy this package, rename it, and replace the harmless dry-run behavior."""

from datetime import UTC, datetime

from statebreaker import AttackPlan, PluginManifest, RawAttackResult
from statebreaker.runtime import ExecutionRuntime


class DryRunExecutor:
    manifest = PluginManifest(
        plugin_id="template.dry-run",
        name="Template dry-run executor",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.executor",
        capabilities=["preview-only", "no-network-requests"],
        description="Validates plugin discovery without implementing an attack.",
    )

    async def execute(
        self,
        plan: AttackPlan,
        runtime: ExecutionRuntime,
    ) -> RawAttackResult:
        started_at = datetime.now(UTC)
        runtime.emit(
            kind="plugin.dry-run",
            correlation_id="dry-run",
            message=f"Would execute plan {plan.id}; no requests were sent.",
        )
        return RawAttackResult(
            run_id=runtime.run_id,
            attack_plan_id=plan.id,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            responses=[],
            events=runtime.events,
            plugin_data={
                "dry_run": True,
                "target_steps": plan.target_steps,
                "schedule": plan.schedule.model_dump(mode="json"),
            },
        )
