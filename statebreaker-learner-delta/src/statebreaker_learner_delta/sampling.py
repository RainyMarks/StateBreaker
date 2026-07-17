"""Collect independent normal-baseline samples by replaying a Workflow N times.

The learner receives a single ``ExecutionRuntime`` (see the ``statebreaker.learner``
entry-point contract), so "independent samples" means repeatedly walking the same
step list rather than constructing a fresh runtime per round. This works because a
normal Workflow already contains its own reset step (e.g. ``create-run``) among its
``setup`` steps: replaying the full step list each round re-triggers that reset and
yields a fresh server-side baseline while reusing the same HTTP sessions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from statebreaker.errors import StateBreakerError
from statebreaker.models import Workflow
from statebreaker.runtime import ExecutionRuntime

MIN_SAMPLE_SUPPORT = 3


@dataclass(frozen=True)
class NormalSample:
    """One full replay of the workflow: probe step id -> parsed JSON response body."""

    index: int
    probes: dict[str, dict[str, object]]


@dataclass(frozen=True)
class ProbePair:
    """A before/after pair of state-probe steps to compare within one sample."""

    before_step: str
    after_step: str


def probe_pairs(state_probe_steps: list[str]) -> list[ProbePair]:
    """Pair up consecutive probes in declaration order (before, after, before, after, ...)."""

    return [
        ProbePair(before_step=before, after_step=after)
        for before, after in zip(state_probe_steps, state_probe_steps[1:], strict=False)
    ]


async def collect_normal_samples(
    workflow: Workflow,
    runtime: ExecutionRuntime,
    *,
    sample_count: int,
) -> list[NormalSample]:
    """Replay the workflow ``sample_count`` times, discarding rounds that fail."""

    if sample_count < 1:
        raise ValueError("sample_count must be >= 1")

    samples: list[NormalSample] = []
    for index in range(sample_count):
        # Drop per-round HTTP records so multi-sample learning does not retain
        # O(N * steps) response objects in memory. Events stay on disk via EventLog.
        runtime.responses.clear()
        probes = await _replay_once(workflow, runtime, index)
        if probes is not None:
            samples.append(NormalSample(index=index, probes=probes))
    return samples


async def _replay_once(
    workflow: Workflow, runtime: ExecutionRuntime, index: int
) -> dict[str, dict[str, object]] | None:
    probes: dict[str, dict[str, object]] = {}
    for step in workflow.steps:
        try:
            record = await runtime.execute_step(step)
        except StateBreakerError as exc:
            runtime.emit(
                kind="learner.sample-discarded",
                correlation_id=f"learner-sample-{index}",
                step_id=step.id,
                message=f"round {index} discarded at step {step.id!r}: {exc}",
            )
            return None
        if step.id in workflow.state_probe_steps:
            try:
                body = json.loads(record.body_preview)
            except json.JSONDecodeError:
                runtime.emit(
                    kind="learner.sample-discarded",
                    correlation_id=f"learner-sample-{index}",
                    step_id=step.id,
                    message=f"round {index} discarded: probe {step.id!r} did not return JSON",
                )
                return None
            if not isinstance(body, dict):
                runtime.emit(
                    kind="learner.sample-discarded",
                    correlation_id=f"learner-sample-{index}",
                    step_id=step.id,
                    message=f"round {index} discarded: probe {step.id!r} body was not an object",
                )
                return None
            probes[step.id] = body
    return probes
