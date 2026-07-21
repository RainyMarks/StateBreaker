"""Baseline learning: run normal-execution experiments and learn behavior.

Experiments per mutating action (spec §8.1): control (probe twice, no action),
single (A), sequential (A -> A). Each replay re-creates fresh resources, so
every trial is isolated by construction.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from statebreaker.baseline.effect_model import classify_repeat, response_signature
from statebreaker.baseline.invariant_learner import learn_invariants
from statebreaker.baseline.state_normalizer import StateNormalizer
from statebreaker.execution.client import HttpSender, exchange_to_record
from statebreaker.execution.reset import ResetStrategy
from statebreaker.execution.snapshots import ProbeSnapshotRunner
from statebreaker.intelligence.dependency_inference import replay_flow, send_template
from statebreaker.intelligence.jsondiff import diff_flat
from statebreaker.models.base import template_variables
from statebreaker.models.capture import RequestTemplate
from statebreaker.models.execution import ExecutionTrial, HttpResponseRecord, TrialRole
from statebreaker.models.state import (
    BaselineProfile,
    FieldChange,
    OperationEffect,
    StateProbe,
    StateSnapshot,
)
from statebreaker.models.workflow import VariableBinding, WorkflowGraph

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class MutatingAction:
    """One state-changing template plus the prefix that prepares its inputs."""

    template: RequestTemplate
    prefix: list[RequestTemplate] = field(default_factory=list)


def find_mutating_actions(
    templates: list[RequestTemplate],
    *,
    probes: list[StateProbe] | None = None,
    max_actions: int = 10,
) -> list[MutatingAction]:
    """State-changing templates with enough observable state to race.

    Variable-consuming actions remain the strongest signal. Fixed-path actions
    are also learned when a later state probe can be replayed, which covers
    flows where the raced resource lives in a stable path, header, or body
    field rather than in a path variable.
    """
    probe_source_ids = {
        probe.request_template.source_exchange_id or probe.request_template.template_id
        for probe in probes or []
    }
    probe_indexes = [
        index
        for index, template in enumerate(templates)
        if template.template_id in probe_source_ids
    ]
    actions: list[MutatingAction] = []
    for index, template in enumerate(templates):
        if template.method not in MUTATING_METHODS:
            continue
        referenced = template_variables(
            {
                "path": template.path_template,
                "query": template.query,
                "headers": template.headers,
                "body": template.body,
            }
        )
        has_later_probe = any(probe_index > index for probe_index in probe_indexes)
        if not referenced and not has_later_probe:
            continue
        actions.append(MutatingAction(template=template, prefix=templates[:index]))
    return actions[:max_actions]


class BaselineLearner:
    """Runs the normal-execution experiment battery and learns a profile."""

    def __init__(
        self,
        sender: HttpSender,
        reset: ResetStrategy,
        *,
        session_id: str = "default",
    ) -> None:
        self._sender = sender
        self._reset = reset
        self._session_id = session_id
        self._trial_counter = 0
        self.normalizers: dict[str, StateNormalizer] = {}

    async def learn(
        self,
        *,
        graph: WorkflowGraph,
        templates: list[RequestTemplate],
        probes: list[StateProbe],
        capture_id: str,
        max_actions: int = 10,
    ) -> tuple[BaselineProfile, list[ExecutionTrial]]:
        actions = find_mutating_actions(templates, probes=probes, max_actions=max_actions)
        bindings = graph.variable_bindings
        trials: list[ExecutionTrial] = []
        effects: list[OperationEffect] = []
        normalizers: dict[str, StateNormalizer] = {}

        for action in actions:
            control = await self._run_control(action, probes, normalizers, bindings)
            trials.append(control)
            single = await self._run_single(action, probes, normalizers, bindings)
            trials.append(single)
            sequential = await self._run_sequential(action, probes, normalizers, bindings)
            trials.append(sequential)
            effect = self._build_effect(action, single, sequential)
            if effect is not None:
                effects.append(effect)

        invariants = learn_invariants(effects, trials)
        self.normalizers = normalizers
        profile = BaselineProfile(
            profile_id=f"baseline-{capture_id}",
            capture_id=capture_id,
            graph_id=graph.graph_id,
            effects=effects,
            invariants=invariants,
            probe_ids=[probe.probe_id for probe in probes],
            trial_ids=[trial.trial_id for trial in trials],
        )
        return profile, trials

    # -- experiments ---------------------------------------------------------

    async def _prepare(
        self,
        action: MutatingAction,
        experiment: str,
        bindings: list[VariableBinding],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        context = await self._reset.prepare_trial(f"{experiment}-{self._trial_counter}")
        replay = await replay_flow(
            action.prefix,
            bindings,
            self._sender,
            session_id=self._session_id,
            initial_variables=context.variables,
        )
        if not replay.success:
            raise RuntimeError(
                f"prefix replay failed during {experiment}: {replay.failure_reason}"
            )
        variables = dict(context.variables)
        variables.update(replay.variables)
        return variables, {"experiment": experiment, **context.metadata}

    async def _run_control(
        self,
        action: MutatingAction,
        probes: list[StateProbe],
        normalizers: dict[str, StateNormalizer],
        bindings: list[VariableBinding],
    ) -> ExecutionTrial:
        variables, meta = await self._prepare(action, "control", bindings)
        first = await self._snapshot(probes, variables)
        second = await self._snapshot(probes, variables)
        self._teach_normalizers(first, second, normalizers)
        first = self._renormalize(first, normalizers)
        second = self._renormalize(second, normalizers)
        return self._trial(action, "control", first, second, [], meta)

    async def _run_single(
        self,
        action: MutatingAction,
        probes: list[StateProbe],
        normalizers: dict[str, StateNormalizer],
        bindings: list[VariableBinding],
    ) -> ExecutionTrial:
        variables, meta = await self._prepare(action, "single", bindings)
        before = await self._snapshot(probes, variables, normalizers)
        exchange = await send_template(
            action.template, variables, self._sender, session_id=self._session_id
        )
        after = await self._snapshot(probes, variables, normalizers)
        meta["response_status"] = exchange.response_status
        return self._trial(
            action,
            "baseline",
            before,
            after,
            [exchange_to_record(exchange, action.template.template_id)],
            meta,
        )

    async def _run_sequential(
        self,
        action: MutatingAction,
        probes: list[StateProbe],
        normalizers: dict[str, StateNormalizer],
        bindings: list[VariableBinding],
    ) -> ExecutionTrial:
        variables, meta = await self._prepare(action, "sequential", bindings)
        before = await self._snapshot(probes, variables, normalizers)
        first = await send_template(
            action.template, variables, self._sender, session_id=self._session_id
        )
        mid = await self._snapshot(probes, variables, normalizers)
        second_exchange = await send_template(
            action.template, variables, self._sender, session_id=self._session_id
        )
        after = await self._snapshot(probes, variables, normalizers)
        meta["first_status"] = first.response_status
        meta["second_status"] = second_exchange.response_status
        trial = self._trial(
            action,
            "baseline",
            before,
            after,
            [
                exchange_to_record(first, f"{action.template.template_id}#1"),
                exchange_to_record(second_exchange, f"{action.template.template_id}#2"),
            ],
            meta,
        )
        trial.reset_context["mid_state"] = [
            snapshot.to_json_dict() for snapshot in mid
        ]
        return trial

    # -- helpers -------------------------------------------------------------

    async def _snapshot(
        self,
        probes: list[StateProbe],
        variables: Mapping[str, Any],
        normalizers: dict[str, StateNormalizer] | None = None,
    ) -> list[StateSnapshot]:
        runner = ProbeSnapshotRunner(self._sender, probes, session_id=self._session_id)
        return await runner.snapshot(variables, normalizers)

    def _teach_normalizers(
        self,
        first: list[StateSnapshot],
        second: list[StateSnapshot],
        normalizers: dict[str, StateNormalizer],
    ) -> None:
        for snap_first, snap_second in zip(first, second, strict=False):
            normalizers[snap_first.probe_id] = StateNormalizer.from_control_samples(
                [snap_first.raw, snap_second.raw]
            )

    @staticmethod
    def _renormalize(
        snapshots: list[StateSnapshot],
        normalizers: dict[str, StateNormalizer],
    ) -> list[StateSnapshot]:
        return [
            snapshot.model_copy(
                update={
                    "normalized": normalizers[snapshot.probe_id].normalize(snapshot.raw)
                    if snapshot.probe_id in normalizers
                    else snapshot.normalized
                }
            )
            for snapshot in snapshots
        ]

    def _trial(
        self,
        action: MutatingAction,
        role: TrialRole,
        before: list[StateSnapshot],
        after: list[StateSnapshot],
        responses: list[HttpResponseRecord],
        meta: dict[str, Any],
    ) -> ExecutionTrial:
        self._trial_counter += 1
        return ExecutionTrial(
            trial_id=f"trial-{role}-{self._trial_counter}",
            candidate_id="",
            plan_id="",
            control_or_attack=role,
            before_state=before,
            after_state=after,
            responses=responses,
            reset_context={"action_id": action.template.template_id, **meta},
            started_at_ns=time.perf_counter_ns(),
            completed_at_ns=time.perf_counter_ns(),
        )

    def _build_effect(
        self,
        action: MutatingAction,
        single: ExecutionTrial,
        sequential: ExecutionTrial,
    ) -> OperationEffect | None:
        first_changes = _normalized_changes(single)
        mid_state = sequential.reset_context.get("mid_state")
        if not isinstance(mid_state, list):
            return None
        second_changes = _sequential_second_changes(sequential)
        first_response = single.responses[0] if single.responses else None
        second_response = sequential.responses[1] if len(sequential.responses) > 1 else None
        first_signature = (
            response_signature(first_response.status, first_response.body)
            if first_response
            else None
        )
        second_signature = (
            response_signature(second_response.status, second_response.body)
            if second_response
            else None
        )
        behavior = classify_repeat(
            first_changes=first_changes,
            second_changes=second_changes,
            first_signature=first_signature,
            second_signature=second_signature,
        )
        success = first_response is not None and first_response.status < 400
        if not first_changes:
            return None
        return OperationEffect(
            action_id=action.template.template_id,
            state_changes=first_changes,
            response_signature=first_signature,
            success_probability=1.0 if success else 0.0,
            repeat_behavior=behavior,
            supporting_trial_ids=[single.trial_id, sequential.trial_id],
        )


def _normalized_changes(trial: ExecutionTrial) -> list[FieldChange]:
    changes: list[FieldChange] = []
    for before, after in zip(trial.before_state, trial.after_state, strict=False):
        if before.normalized is None or after.normalized is None:
            continue
        changes.extend(diff_flat(before.normalized.fields, after.normalized.fields))
    return changes


def _sequential_second_changes(trial: ExecutionTrial) -> list[FieldChange]:
    mid_state = trial.reset_context.get("mid_state")
    if not isinstance(mid_state, list):
        return []
    changes: list[FieldChange] = []
    for mid_raw, after in zip(mid_state, trial.after_state, strict=False):
        if not isinstance(mid_raw, dict) or after.normalized is None:
            continue
        mid_fields = (mid_raw.get("normalized") or {}).get("fields") or {}
        changes.extend(diff_flat(mid_fields, after.normalized.fields))
    return changes
