from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from statebreaker.errors import DocumentError
from statebreaker.models import (
    AttackPlan,
    Finding,
    Invariant,
    RawAttackResult,
    ReportArtifacts,
    Workflow,
)
from statebreaker.pipeline import PipelinePlugins, run_pipeline, select_attack_plan


def _workflow() -> Workflow:
    return Workflow.model_validate(
        {
            "name": "pipeline-test",
            "base_url": "http://127.0.0.1:1",
            "steps": [
                {
                    "id": "redeem",
                    "request": {"method": "POST", "path": "/redeem"},
                }
            ],
        }
    )


def _plan(plan_id: str, attack_type: str = "concurrent-replay") -> AttackPlan:
    return AttackPlan(
        id=plan_id,
        workflow_name="pipeline-test",
        attack_type=attack_type,
        target_steps=["redeem"],
    )


def test_select_attack_plan_is_deterministic() -> None:
    selected = select_attack_plan([_plan("z-plan"), _plan("a-plan")])
    assert selected.id == "a-plan"
    assert select_attack_plan([_plan("one")], plan_id="one").id == "one"


def test_select_attack_plan_rejects_unknown_type() -> None:
    with pytest.raises(DocumentError, match="available types"):
        select_attack_plan([_plan("one")], attack_type="step-skip")


class _Generator:
    async def generate(
        self, workflow: Workflow, invariants: list[Invariant]
    ) -> list[AttackPlan]:
        return [_plan("generated-plan")]


class _Executor:
    async def execute(self, plan: AttackPlan, runtime: Any) -> RawAttackResult:
        now = datetime.now(UTC)
        return RawAttackResult(
            run_id=runtime.run_id,
            attack_plan_id=plan.id,
            started_at=now,
            finished_at=now,
            before_state={"discount_yuan": 0},
            after_state={"discount_yuan": 100},
        )


class _Verifier:
    async def verify(
        self, result: RawAttackResult, invariants: list[Invariant]
    ) -> list[Finding]:
        return [
            Finding(
                id="coupon-race",
                verdict="confirmed",
                title="Coupon redeemed twice",
                invariant_id=invariants[0].id,
            )
        ]


class _Reporter:
    async def render(self, bundle: Any, output_dir: Path) -> ReportArtifacts:
        output_dir.mkdir(parents=True, exist_ok=True)
        report = output_dir / "report.txt"
        report.write_text(bundle.attack_plan.id, encoding="utf-8")
        return ReportArtifacts(files=[str(report)])


class _Registry:
    plugins = {
        ("statebreaker.generator", "test.generator"): _Generator(),
        ("statebreaker.executor", "test.executor"): _Executor(),
        ("statebreaker.verifier", "test.verifier"): _Verifier(),
        ("statebreaker.reporter", "test.reporter"): _Reporter(),
    }

    def get(self, group: str, plugin_id: str) -> Any:
        return self.plugins[(group, plugin_id)]


async def test_pipeline_writes_complete_run_bundle(tmp_path: Path) -> None:
    invariant = Invariant(id="max-discount", kind="max-delta", selector="$.discount_yuan")
    outcome = await run_pipeline(
        _workflow(),
        [invariant],
        plugins=PipelinePlugins(
            generator="test.generator",
            executor="test.executor",
            verifier="test.verifier",
            reporter="test.reporter",
        ),
        registry=_Registry(),  # type: ignore[arg-type]
        output_root=tmp_path,
    )

    assert outcome.selected_plan.id == "generated-plan"
    assert outcome.findings[0].verdict == "confirmed"
    for name in (
        "workflow.json",
        "invariants.json",
        "attack-plans.json",
        "selected-plan.json",
        "raw-attack-result.json",
        "findings.json",
        "run-bundle.json",
        "summary.json",
        "events.jsonl",
    ):
        assert (outcome.run_dir / name).exists()
    assert (outcome.run_dir / "report" / "artifacts.json").exists()
