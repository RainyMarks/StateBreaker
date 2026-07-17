"""Entry point: replay a Workflow N times and propose Invariants from the deltas.

See the package README for the full design rationale. In one sentence: this
plugin never asserts that a candidate rule is correct -- it only reports what
was observed, how many samples support it, and how consistently.
"""

from __future__ import annotations

import os

from statebreaker.errors import PluginError
from statebreaker.models import Invariant, LearningResult, PluginManifest, Workflow
from statebreaker.runtime import ExecutionRuntime

from .profiling import build_state_profile
from .proposers import DEFAULT_PROPOSERS
from .sampling import MIN_SAMPLE_SUPPORT, collect_normal_samples, probe_pairs

DEFAULT_SAMPLE_COUNT = 10
SAMPLE_COUNT_ENV_VAR = "STATEBREAKER_LEARNER_SAMPLES"
MAX_SAMPLE_COUNT = 100


def _resolve_sample_count(explicit: int | None) -> int:
    if explicit is not None:
        sample_count = explicit
    else:
        raw = os.environ.get(SAMPLE_COUNT_ENV_VAR)
        if raw is None or raw.strip() == "":
            sample_count = DEFAULT_SAMPLE_COUNT
        else:
            try:
                sample_count = int(raw)
            except ValueError as exc:
                raise PluginError(
                    f"environment variable {SAMPLE_COUNT_ENV_VAR} must be an integer, "
                    f"got {raw!r}"
                ) from exc
    if sample_count < 1 or sample_count > MAX_SAMPLE_COUNT:
        raise PluginError(
            f"sample_count must be between 1 and {MAX_SAMPLE_COUNT}, got {sample_count}"
        )
    return sample_count


class DeltaLearnerPlugin:
    manifest = PluginManifest(
        plugin_id="team.delta-learner",
        name="Delta-based normal-state learner",
        version="0.1.1",
        api_version="0.1",
        group="statebreaker.learner",
        capabilities=["multi-sample-baseline", "max-delta", "min-value", "state-transition"],
        description=(
            "重复正常流程多轮采样，比较探针前后状态，按数值上界/下界/状态转换"
            "提出候选 Invariant，附样本数与置信度证据。"
        ),
    )

    def __init__(self, sample_count: int | None = None) -> None:
        self.sample_count = _resolve_sample_count(sample_count)

    async def learn(self, workflow: Workflow, runtime: ExecutionRuntime) -> LearningResult:
        if len(workflow.state_probe_steps) < 2:
            raise PluginError(
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
