"""Batch coupon audit command for generated attack plans."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer
from statebreaker.documents import load_model, load_typed, write_json
from statebreaker.models import AttackPlan, Workflow
from statebreaker.runtime import ExecutionRuntime

from statebreaker_race_executor.plugin import RaceAttackExecutor


def run(
    workflow_path: Path,
    plans_path: Path,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path(
        ".statebreaker/coupon-audit"
    ),
) -> None:
    workflow = load_model(workflow_path, Workflow)
    plans = load_typed(plans_path, list[AttackPlan])
    output_dir.mkdir(parents=True, exist_ok=True)
    executor = RaceAttackExecutor()

    async def invoke() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for plan in plans:
            async with ExecutionRuntime(workflow) as runtime:
                result = await executor.execute(plan, runtime)
            result_path = output_dir / f"{plan.id}.json"
            write_json(result_path, result)
            rows.append(
                {
                    "plan_id": plan.id,
                    "attack_type": plan.attack_type,
                    "result_path": str(result_path),
                    **result.plugin_data,
                }
            )
        return rows

    rows = asyncio.run(invoke())
    summary = {
        "workflow_name": workflow.name,
        "total_plans": len(rows),
        "vulnerable_plans": sum(1 for row in rows if row["vulnerability_observed"]),
        "results": rows,
    }
    write_json(output_dir / "summary.json", summary)
    typer.echo(f"已写入：{(output_dir / 'summary.json').resolve()}")


def main() -> None:
    typer.run(run)
