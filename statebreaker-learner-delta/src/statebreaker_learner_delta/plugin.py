"""Entry point: replay a Workflow N times and propose Invariants from the deltas.

See the package README for the full design rationale. In one sentence: this
plugin never asserts that a candidate rule is correct -- it only reports what
was observed, how many samples support it, and how consistently.
"""

from __future__ import annotations

import os

from statebreaker.models import Invariant, LearningResult, PluginManifest, Workflow
from statebreaker.runtime import ExecutionRuntime

from .profiling import build_state_profile
from .proposers import DEFAULT_PROPOSERS
from .sampling import MIN_SAMPLE_SUPPORT, collect_normal_samples, probe_pairs

DEFAULT_SAMPLE_COUNT = 10
SAMPLE_COUNT_ENV_VAR = "STATEBREAKER_LEARNER_SAMPLES"


class DeltaLearnerPlugin:
    manifest = PluginManifest(
        plugin_id="delta-learner",
        name="Delta-based normal-state learner",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.learner",
        capabilities=["multi-sample-baseline", "max-delta", "min-value", "state-transition"],
        description=(
            "重复正常流程多轮采样，比较探针前后状态，按数值上界/下界/状态转换"
            "提出候选 Invariant，附样本数与置信度证据。"
        ),
    )

    def __init__(self, sample_count: int | None = None) -> None:
        self.sample_count = (
            sample_count
            if sample_count is not None
            else int(os.environ.get(SAMPLE_COUNT_ENV_VAR, DEFAULT_SAMPLE_COUNT))
        )

    async def learn(self, workflow: Workflow, runtime: ExecutionRuntime) -> LearningResult:
        if len(workflow.state_probe_steps) < 2:
            raise ValueError(
                "delta learner needs at least two state_probe_steps to compare "
                "before/after state"
            )

        samples = await collect_normal_samples(workflow, runtime, sample_count=self.sample_count)
        profile = build_state_profile(workflow.name, samples)

        if len(samples) < MIN_SAMPLE_SUPPORT:
            runtime.emit(
                kind="learner.insufficient-samples",
                correlation_id="learner-summary",
                message=(
                    f"only {len(samples)}/{self.sample_count} rounds succeeded; "
                    "no invariants proposed"
                ),
            )
            return LearningResult(profile=profile, invariants=[])

        stable_fields = sorted(profile.stable_fields)
        invariants: list[Invariant] = []
        seen_ids: set[str] = set()
        for pair in probe_pairs(workflow.state_probe_steps):
            for key in stable_fields:
                for proposer in DEFAULT_PROPOSERS:
                    invariant = proposer.propose(key=key, pair=pair, samples=samples)
                    # Multiple probe pairs touching the same field could otherwise
                    # produce colliding ids; keep the first (earliest pair) only.
                    if invariant is not None and invariant.id not in seen_ids:
                        seen_ids.add(invariant.id)
                        invariants.append(invariant)

        runtime.emit(
            kind="learner.completed",
            correlation_id="learner-summary",
            message=f"{len(samples)} samples collected, {len(invariants)} invariants proposed",
        )
        return LearningResult(profile=profile, invariants=invariants)
