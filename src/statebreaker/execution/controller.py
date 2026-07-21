"""Experiment controller: run isolated control and attack trials for a plan.

Every trial: reset -> setup replay -> before-state -> fire -> after-state.
Control trials fire sequentially; attack trials fire through a scheduler
backend. Same plan, same probes — the only difference is concurrency.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from statebreaker.baseline.state_normalizer import StateNormalizer
from statebreaker.errors import ExecutionError, TemplateError
from statebreaker.execution.client import (
    BudgetTracker,
    HttpSender,
    append_query,
    exchange_to_record,
)
from statebreaker.execution.reset import ResetStrategy
from statebreaker.execution.snapshots import ProbeSnapshotRunner
from statebreaker.execution.templating import render_template
from statebreaker.execution.transports.base import RaceResult, SchedulerBackend
from statebreaker.intelligence.dependency_inference import replay_flow, send_template
from statebreaker.models.capture import RequestTemplate
from statebreaker.models.discovery import AttackPlan
from statebreaker.models.execution import (
    ExecutionTrial,
    HttpResponseRecord,
    PreparedRequest,
    TimelineEvent,
    TrialRole,
)
from statebreaker.models.state import StateProbe, StateSnapshot
from statebreaker.models.workflow import VariableBinding


class ExperimentController:
    """Owns trial lifecycle; all race evidence is born here."""

    def __init__(
        self,
        *,
        sender: HttpSender,
        reset: ResetStrategy,
        backends: Mapping[str, SchedulerBackend],
        probes: list[StateProbe],
        normalizers: Mapping[str, StateNormalizer] | None = None,
        bindings: list[VariableBinding] | None = None,
        templates: Mapping[str, RequestTemplate] | None = None,
        budget: BudgetTracker | None = None,
        session_id: str = "default",
    ) -> None:
        self._sender = sender
        self._reset = reset
        self._backends = dict(backends)
        self._normalizers = dict(normalizers or {})
        self._bindings = list(bindings or [])
        self._templates = dict(templates or {})
        self._budget = budget
        self._session_id = session_id
        self._trial_counter = 0
        self._snapshots = ProbeSnapshotRunner(sender, probes, session_id=session_id)

    async def run_trial(
        self,
        plan: AttackPlan,
        *,
        role: TrialRole,
        scheduler_id: str | None = None,
    ) -> ExecutionTrial:
        """Run one isolated trial of ``plan`` (``control`` or ``attack``)."""
        if self._budget is not None:
            self._budget.count_trial()
            self._budget.check_time()
        self._trial_counter += 1
        trial_id = f"trial-{role}-{self._trial_counter}"
        started = time.perf_counter_ns()

        context = await self._reset.prepare_trial(trial_id)
        variables = dict(context.variables)
        setup_templates = [
            self._templates[template_id]
            for template_id in plan.setup_action_ids
            if template_id in self._templates
        ]
        if setup_templates:
            replay = await replay_flow(
                setup_templates,
                self._bindings,
                self._sender,
                session_id=self._session_id,
                initial_variables=variables,
            )
            if not replay.success:
                raise ExecutionError(f"plan setup replay failed: {replay.failure_reason}")
            variables.update(replay.variables)

        before = await self._snapshot(variables)
        if role == "control":
            responses, timeline, requests = await self._fire_sequential(plan, variables)
        else:
            responses, timeline, requests = await self._fire_concurrent(
                plan, variables, scheduler_id or plan.scheduler
            )
        after = await self._snapshot(variables)

        return ExecutionTrial(
            trial_id=trial_id,
            candidate_id=plan.candidate_id,
            plan_id=plan.plan_id,
            control_or_attack=role,
            requests=requests,
            before_state=before,
            responses=responses,
            after_state=after,
            timeline=timeline,
            reset_context={"strategy": plan.reset_strategy, **context.metadata},
            started_at_ns=started,
            completed_at_ns=time.perf_counter_ns(),
        )

    # -- firing --------------------------------------------------------------

    async def _fire_sequential(
        self, plan: AttackPlan, variables: Mapping[str, Any]
    ) -> tuple[list[HttpResponseRecord], list[TimelineEvent], list[PreparedRequest]]:
        responses: list[HttpResponseRecord] = []
        timeline: list[TimelineEvent] = []
        requests: list[PreparedRequest] = []
        for instance in plan.action_instances:
            for template in instance.exchange_templates:
                session_id = instance.session_id or self._session_id
                exchange = await send_template(
                    template,
                    variables,
                    self._sender,
                    session_id=session_id,
                )
                requests.append(
                    PreparedRequest(
                        instance_id=instance.instance_id,
                        session_id=session_id,
                        method=exchange.method,
                        url=exchange.url,
                        headers=dict(exchange.request_headers),
                        body=(
                            exchange.request_body.encode()
                            if isinstance(exchange.request_body, str)
                            else None
                        ),
                    )
                )
                responses.append(exchange_to_record(exchange, instance.instance_id))
                timeline.append(
                    TimelineEvent(
                        instance_id=instance.instance_id,
                        event="completed",
                        at_ns=exchange.completed_at_ns,
                    )
                )
        return responses, timeline, requests

    async def _fire_concurrent(
        self,
        plan: AttackPlan,
        variables: Mapping[str, Any],
        scheduler_id: str,
    ) -> tuple[list[HttpResponseRecord], list[TimelineEvent], list[PreparedRequest]]:
        backend = self._backends.get(scheduler_id)
        if backend is None:
            raise ExecutionError(f"unknown scheduler backend: {scheduler_id!r}")
        prepared: list[PreparedRequest] = []
        for instance in plan.action_instances:
            if len(instance.exchange_templates) != 1:
                raise ExecutionError(
                    "race instances must hold exactly one exchange template "
                    f"({instance.instance_id} has {len(instance.exchange_templates)})"
                )
            prepared.append(
                self._prepare_request(
                    instance.instance_id,
                    instance.session_id,
                    instance.exchange_templates[0],
                    variables,
                )
            )
        race = await backend.prepare(prepared)
        race = race.model_copy(update={"offsets_ms": list(plan.offsets_ms)})
        result: RaceResult = await backend.release(race)
        return result.responses, result.timeline, prepared

    def _prepare_request(
        self,
        instance_id: str,
        session_id: str,
        template: RequestTemplate,
        variables: Mapping[str, Any],
    ) -> PreparedRequest:
        try:
            rendered = render_template(template, variables)
        except TemplateError as exc:
            raise ExecutionError(f"cannot render attack instance {instance_id}: {exc}") from exc
        content, content_headers = rendered.build_content()
        headers = {
            **self._sender.session_headers(session_id or self._session_id),
            **rendered.headers,
            **content_headers,
        }
        url = append_query(self._sender.absolute_url(rendered.path), rendered.query)
        return PreparedRequest(
            instance_id=instance_id,
            session_id=session_id or self._session_id,
            method=rendered.method,
            url=url,
            headers=headers,
            body=content,
        )

    # -- state ---------------------------------------------------------------

    async def _snapshot(self, variables: Mapping[str, Any]) -> list[StateSnapshot]:
        return await self._snapshots.snapshot(variables, self._normalizers)
