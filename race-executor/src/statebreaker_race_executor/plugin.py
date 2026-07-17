"""Bounded executor for coupon race-condition attack plans."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

from statebreaker import AttackPlan, PluginManifest, RawAttackResult
from statebreaker.errors import PluginError
from statebreaker.models import RequestSpec, RequestStep, ResponseRecord, StepRole
from statebreaker.runtime import ExecutionRuntime

SUPPORTED_ATTACK_TYPES = frozenset(
    {
        "concurrent-replay",
        "burst-replay",
        "offset-sweep",
        "precondition-bypass-replay",
        "idempotency-key-reuse",
        "stale-state-assisted-replay",
        "run-eviction-pressure",
    }
)
MAX_TARGET_REQUESTS = 16
MAX_AUXILIARY_REQUESTS = 128
MAX_ATTEMPTS_CAP = 10


class RaceAttackExecutor:
    """Execute bounded coupon attack plans and collect state evidence."""

    manifest = PluginManifest(
        plugin_id="team.race-executor",
        name="Coupon race-condition executor",
        version="0.1.1",
        api_version="0.1",
        group="statebreaker.executor",
        capabilities=[
            "concurrent-replay",
            "burst-replay",
            "offset-sweep",
            "precondition-bypass-replay",
            "idempotency-key-reuse",
            "stale-state-assisted-replay",
            "run-eviction-pressure",
            "bounded-concurrency",
            "state-evidence",
        ],
        description=(
            "Executes bounded coupon race plans and records state evidence. "
            "plugin_data.vulnerability_observed is heuristic evidence, not a Finding."
        ),
    )

    async def execute(
        self,
        plan: AttackPlan,
        runtime: ExecutionRuntime,
    ) -> RawAttackResult:
        try:
            return await self._execute(plan, runtime)
        except PluginError:
            raise
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise PluginError(f"race executor failed for plan {plan.id!r}: {exc}") from exc

    async def _execute(
        self,
        plan: AttackPlan,
        runtime: ExecutionRuntime,
    ) -> RawAttackResult:
        if plan.attack_type not in SUPPORTED_ATTACK_TYPES:
            raise PluginError(f"unsupported coupon attack type: {plan.attack_type}")
        if len(plan.target_steps) != 1:
            raise PluginError("race executor expects exactly one target step")

        required = plan.schedule.options.get("required_executor_capability")
        if required is not None:
            required_name = str(required)
            if required_name not in self.manifest.capabilities:
                raise PluginError(
                    f"plan {plan.id!r} requires capability {required_name!r}, "
                    f"executor provides {sorted(self.manifest.capabilities)}"
                )

        started_at = datetime.now(UTC)
        target_id = plan.target_steps[0]
        step_indexes = {step.id: index for index, step in enumerate(runtime.workflow.steps)}
        step_by_id = {step.id: step for step in runtime.workflow.steps}
        if target_id not in step_by_id:
            raise PluginError(f"unknown target step: {target_id}")

        target_index = step_indexes[target_id]
        skip_steps = set(_string_list(plan.schedule.options.get("skip_steps", [])))
        before_probe_id, after_probe_id = _resolve_probe_ids(plan, runtime.workflow, target_index)
        before_state: dict[str, Any] = {}
        after_state: dict[str, Any] = {}
        lab_events: list[dict[str, Any]] = []
        intermediate_states: list[dict[str, Any]] = []

        for step in runtime.workflow.steps[:target_index]:
            if step.id in skip_steps:
                runtime.emit(
                    kind="attack.step.skipped",
                    correlation_id=f"skip-{step.id}",
                    step_id=step.id,
                    message=f"Skipped by attack plan {plan.id}",
                )
                continue
            record = await runtime.execute_step(step)
            if (before_probe_id is not None and step.id == before_probe_id) or (
                before_probe_id is None and _fallback_is_before_probe(step)
            ):
                before_state = _json_object(record.body_preview)

        target = _with_bound_session(step_by_id[target_id], plan)

        async def refresh_prefix() -> dict[str, Any]:
            """Re-run setup/probe steps before the target (optional multi-attempt)."""

            refreshed_before: dict[str, Any] = {}
            for step in runtime.workflow.steps[:target_index]:
                if step.id in skip_steps:
                    continue
                record = await runtime.execute_step(step)
                if (before_probe_id is not None and step.id == before_probe_id) or (
                    before_probe_id is None and _fallback_is_before_probe(step)
                ):
                    refreshed_before = _json_object(record.body_preview)
            return refreshed_before

        target_records, before_state = await self._execute_target(
            plan,
            runtime,
            target,
            intermediate_states,
            before_state,
            refresh_prefix,
        )

        for step in runtime.workflow.steps[target_index + 1 :]:
            if step.id in skip_steps:
                continue
            record = await runtime.execute_step(step)
            if (after_probe_id is not None and step.id == after_probe_id) or (
                after_probe_id is None and _fallback_is_after_probe(step)
            ):
                after_state = _json_object(record.body_preview)

        # If after probe was skipped or missing, try a final state read when run_id exists.
        if not after_state and "run_id" in runtime.variables:
            probe = await _read_state(
                runtime, target, step_id="state-after-fallback", request_ordinal=0
            )
            if probe is not None:
                after_state = _json_object(probe.body_preview)

        events_record = await _try_read_lab_events(runtime, target)
        if events_record is not None:
            lab_events = _json_object(events_record.body_preview).get("events", [])
            if not isinstance(lab_events, list):
                lab_events = []

        finished_at = datetime.now(UTC)
        plugin_data = _summarize(
            plan,
            target_records,
            before_state,
            after_state,
            lab_events,
            intermediate_states,
        )
        return RawAttackResult(
            run_id=runtime.run_id,
            attack_plan_id=plan.id,
            started_at=started_at,
            finished_at=finished_at,
            responses=list(runtime.responses),
            before_state=before_state,
            after_state=after_state,
            events=runtime.events,
            plugin_data=plugin_data,
        )

    async def _execute_target(
        self,
        plan: AttackPlan,
        runtime: ExecutionRuntime,
        target: RequestStep,
        intermediate_states: list[dict[str, Any]],
        before_state: dict[str, Any],
        refresh_prefix: Any,
    ) -> tuple[list[ResponseRecord], dict[str, Any]]:
        options = plan.schedule.options
        strategy = str(options.get("strategy", "simultaneous"))
        if strategy == "state-probe-assisted":
            records = await _execute_state_probe_assisted(
                plan,
                runtime,
                target,
                intermediate_states,
            )
            return records, before_state
        if strategy == "run-eviction-pressure":
            records = await _execute_run_eviction_pressure(plan, runtime, target)
            return records, before_state
        if strategy == "sequential-replay":
            records = await _execute_sequential_replay(plan, runtime, target)
            return records, before_state

        concurrency = plan.schedule.concurrency
        _enforce_request_limit(concurrency, options)
        offsets_ms = plan.schedule.offsets_ms or [0.0] * concurrency
        if len(offsets_ms) != concurrency:
            raise PluginError("schedule must provide one offset per concurrent request")

        max_attempts = _max_attempts(options)
        reset_before_retry = bool(options.get("reset_before_retry", False))
        last_records: list[ResponseRecord] = []
        current_before = before_state
        for attempt in range(max_attempts):
            if attempt > 0:
                if not reset_before_retry:
                    raise PluginError(
                        "max_attempts > 1 requires schedule.options.reset_before_retry=true "
                        "so each attempt re-runs setup against a fresh prepared state"
                    )
                current_before = await refresh_prefix()

            async def run_one(
                ordinal: int, offset_ms: float, *, _attempt: int = attempt
            ) -> ResponseRecord:
                if offset_ms > 0:
                    await asyncio.sleep(offset_ms / 1000)
                return await runtime.execute_step(
                    _with_request_id(target, plan, ordinal + _attempt * concurrency),
                    request_ordinal=ordinal + _attempt * concurrency,
                )

            last_records = list(
                await asyncio.gather(
                    *(run_one(index, offset) for index, offset in enumerate(offsets_ms))
                )
            )
            # Early-stop retries once concurrent successes already indicate breakage.
            if attempt + 1 < max_attempts and _attempt_looks_broken(
                plan, current_before, last_records
            ):
                break
        return last_records, current_before


async def _execute_sequential_replay(
    plan: AttackPlan,
    runtime: ExecutionRuntime,
    target: RequestStep,
) -> list[ResponseRecord]:
    options = plan.schedule.options
    repeat_count = int(options.get("repeat_count", 1))
    _enforce_request_limit(repeat_count, options)
    continue_on_rejection = bool(options.get("continue_on_rejection", True))
    records: list[ResponseRecord] = []
    for ordinal in range(repeat_count):
        record = await runtime.execute_step(
            _with_request_id(target, plan, ordinal),
            request_ordinal=ordinal,
        )
        records.append(record)
        if record.status_code >= 400 and not continue_on_rejection:
            runtime.emit(
                kind="attack.sequential.stopped",
                correlation_id=f"stop-{ordinal}",
                step_id=target.id,
                request_ordinal=ordinal,
                message=(
                    f"Stopped sequential replay after status {record.status_code} "
                    f"(continue_on_rejection=false)"
                ),
            )
            break
    return records


async def _execute_state_probe_assisted(
    plan: AttackPlan,
    runtime: ExecutionRuntime,
    target: RequestStep,
    intermediate_states: list[dict[str, Any]],
) -> list[ResponseRecord]:
    options = plan.schedule.options
    probe_after_ms = float(options.get("probe_after_ms", 50.0))
    followup_after_ms = float(options.get("followup_after_ms", 60.0))
    _enforce_request_limit(2, options)

    first = asyncio.create_task(runtime.execute_step(target, request_ordinal=0))
    await asyncio.sleep(probe_after_ms / 1000)
    probe_record = await _read_state(runtime, target, step_id="state-mid", request_ordinal=0)
    if probe_record is not None:
        intermediate_states.append(_json_object(probe_record.body_preview))
    await asyncio.sleep(max(0.0, followup_after_ms - probe_after_ms) / 1000)
    second = asyncio.create_task(runtime.execute_step(target, request_ordinal=1))
    return list(await asyncio.gather(first, second))


async def _execute_run_eviction_pressure(
    plan: AttackPlan,
    runtime: ExecutionRuntime,
    target: RequestStep,
) -> list[ResponseRecord]:
    options = plan.schedule.options
    create_count = int(options.get("create_count", 101))
    hard_limit = int(options.get("hard_setup_request_limit", MAX_AUXILIARY_REQUESTS))
    if create_count < 1 or create_count > min(hard_limit, MAX_AUXILIARY_REQUESTS):
        raise PluginError(
            "create_count must be between 1 and "
            f"{min(hard_limit, MAX_AUXILIARY_REQUESTS)}"
        )

    create_step = next(
        (step for step in runtime.workflow.steps if "reset" in step.tags),
        runtime.workflow.steps[0],
    )
    original_run_id = runtime.variables.get("run_id")
    for ordinal in range(create_count):
        await runtime.execute_step(create_step, request_ordinal=ordinal)
    if original_run_id is not None:
        runtime.variables["run_id"] = original_run_id
    return [await runtime.execute_step(target, request_ordinal=0)]


def _max_attempts(options: dict[str, Any]) -> int:
    raw = int(options.get("max_attempts", 1))
    if raw < 1 or raw > MAX_ATTEMPTS_CAP:
        raise PluginError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_CAP}")
    return raw


def _enforce_request_limit(count: int, options: dict[str, Any]) -> None:
    hard_limit = int(
        options.get(
            "hard_request_limit",
            options.get("hard_concurrency_limit", MAX_TARGET_REQUESTS),
        )
    )
    if count < 1 or count > min(hard_limit, MAX_TARGET_REQUESTS):
        raise PluginError(
            f"target request count must be between 1 and {min(hard_limit, MAX_TARGET_REQUESTS)}"
        )


def _with_bound_session(step: RequestStep, plan: AttackPlan) -> RequestStep:
    session = plan.session_bindings.get(step.id, step.session)
    return step.model_copy(update={"session": session})


def _with_request_id(step: RequestStep, plan: AttackPlan, ordinal: int) -> RequestStep:
    mode = plan.schedule.options.get("request_id_mode")
    if mode not in {"shared", "per-ordinal"}:
        return step

    value = str(plan.schedule.options.get("request_id_value", plan.id))
    if mode == "per-ordinal":
        value = f"{value}-{ordinal}"
    headers = {**step.request.headers, "X-Request-ID": value}
    request = step.request.model_copy(update={"headers": headers})
    return step.model_copy(update={"request": request})


async def _try_read_lab_events(
    runtime: ExecutionRuntime,
    target: RequestStep,
) -> ResponseRecord | None:
    if "run_id" not in runtime.variables:
        return None
    step = _state_step("events-after", target.session, "/api/runs/${run_id}/events")
    record = await runtime.execute_step(step)
    return record if record.status_code == 200 else None


async def _read_state(
    runtime: ExecutionRuntime,
    target: RequestStep,
    *,
    step_id: str,
    request_ordinal: int,
) -> ResponseRecord | None:
    if "run_id" not in runtime.variables:
        return None
    record = await runtime.execute_step(
        _state_step(step_id, target.session, "/api/runs/${run_id}/state"),
        request_ordinal=request_ordinal,
    )
    return record if record.status_code == 200 else None


def _state_step(step_id: str, session: str, path: str) -> RequestStep:
    return RequestStep(
        id=step_id,
        role=StepRole.PROBE,
        session=session,
        request=RequestSpec(method="GET", path=path),
        tags=["state", "events", "after"],
    )


def _resolve_probe_ids(
    plan: AttackPlan,
    workflow: Any,
    target_index: int,
) -> tuple[str | None, str | None]:
    """Prefer invariant probe refs, then workflow.state_probe_steps around the target."""

    inv = plan.metadata.get("invariant")
    if isinstance(inv, dict):
        before = inv.get("before_probe")
        after = inv.get("after_probe")
        if isinstance(before, str) or isinstance(after, str):
            return (
                before if isinstance(before, str) else None,
                after if isinstance(after, str) else None,
            )

    step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}
    probes = list(workflow.state_probe_steps)
    before_candidates = [
        probe_id
        for probe_id in probes
        if probe_id in step_indexes and step_indexes[probe_id] < target_index
    ]
    after_candidates = [
        probe_id
        for probe_id in probes
        if probe_id in step_indexes and step_indexes[probe_id] > target_index
    ]
    return (
        before_candidates[-1] if before_candidates else None,
        after_candidates[0] if after_candidates else None,
    )


def _fallback_is_before_probe(step: RequestStep) -> bool:
    return step.role == StepRole.PROBE and ("before" in step.tags or step.id.endswith("before"))


def _fallback_is_after_probe(step: RequestStep) -> bool:
    return step.role == StepRole.PROBE and ("after" in step.tags or step.id.endswith("after"))


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _select_value(state: dict[str, Any], selector: str) -> Any:
    """Resolve a simple JSONPath-like selector such as ``$.discount_yuan``."""

    if not selector:
        return None
    path = selector.strip()
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:].lstrip(".")
    if not path:
        return state

    current: Any = state
    for part in path.split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?", part)
        if match is None:
            return None
        key, index = match.group(1), match.group(2)
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
        if index is not None:
            if not isinstance(current, list):
                return None
            position = int(index)
            if position < 0 or position >= len(current):
                return None
            current = current[position]
    return current


def _is_numeric(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _invariant_from_plan(plan: AttackPlan) -> dict[str, Any] | None:
    inv = plan.metadata.get("invariant")
    return inv if isinstance(inv, dict) else None


def _evaluate_invariant_violation(
    plan: AttackPlan,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    target_records: list[ResponseRecord],
) -> tuple[bool | None, dict[str, Any]]:
    """Return (violated|None, evidence). None means invariant could not be evaluated."""

    inv = _invariant_from_plan(plan)
    if inv is None:
        return None, {"reason": "no invariant embedded in plan.metadata"}

    kind = str(inv.get("kind", ""))
    selector = str(inv.get("selector", ""))
    raw_parameters = inv.get("parameters")
    parameters: dict[str, Any] = raw_parameters if isinstance(raw_parameters, dict) else {}
    before_value = _select_value(before_state, selector) if before_state else None
    after_value = _select_value(after_state, selector) if after_state else None
    evidence: dict[str, Any] = {
        "invariant_id": inv.get("id"),
        "kind": kind,
        "selector": selector,
        "before_value": before_value,
        "after_value": after_value,
        "parameters": parameters,
    }

    if kind == "max-delta":
        max_delta = parameters.get("max_delta")
        if not _is_numeric(max_delta):
            return None, {**evidence, "reason": "max_delta parameter missing or non-numeric"}
        if before_value is None and after_value is None:
            return None, {**evidence, "reason": "selector values unavailable"}
        # Missing before (e.g. skipped probe) → treat as 0 only when after is numeric.
        start: float = float(before_value) if _is_numeric(before_value) else 0.0
        if not _is_numeric(after_value):
            return None, {**evidence, "reason": "after value not numeric"}
        end = float(after_value)
        delta = end - start
        evidence["observed_delta"] = delta
        return bool(delta > float(max_delta)), evidence

    if kind == "min-value":
        min_value = parameters.get("min_value", 0)
        if not _is_numeric(min_value):
            return None, {**evidence, "reason": "min_value parameter missing or non-numeric"}
        values = [float(value) for value in (before_value, after_value) if _is_numeric(value)]
        if not values:
            return None, {**evidence, "reason": "selector values unavailable"}
        observed_min = min(values)
        evidence["observed_min"] = observed_min
        return bool(observed_min < float(min_value)), evidence

    if kind == "count-limit":
        limit = parameters.get("max_count", parameters.get("limit", parameters.get("max_delta")))
        if not _is_numeric(limit):
            return None, {**evidence, "reason": "count limit parameter missing"}
        if _is_numeric(after_value) and _is_numeric(before_value):
            observed = float(after_value) - float(before_value)
        elif _is_numeric(after_value):
            observed = float(after_value)
        else:
            observed = float(
                sum(1 for record in target_records if 200 <= record.status_code < 300)
            )
        evidence["observed_count"] = observed
        return bool(observed > float(limit)), evidence

    if kind == "single-use":
        successes = sum(1 for record in target_records if 200 <= record.status_code < 300)
        evidence["successful_target_responses"] = successes
        if _is_numeric(before_value) and _is_numeric(after_value):
            numeric_delta = float(after_value) - float(before_value)
            evidence["numeric_delta"] = numeric_delta
            return bool(numeric_delta > 1 or successes > 1), evidence
        return bool(successes > 1), evidence

    if kind == "state-transition":
        expected_from = parameters.get("from")
        expected_to = parameters.get("to")
        if expected_from is None and expected_to is None:
            return None, {**evidence, "reason": "from/to parameters missing"}
        # A race often leaves the field at the expected "to" while over-applying
        # side effects elsewhere; still flag unexpected transitions when present.
        if before_value is not None and expected_from is not None and before_value != expected_from:
            evidence["unexpected_before"] = True
            return True, evidence
        if after_value is not None and expected_to is not None and after_value != expected_to:
            evidence["unexpected_after"] = True
            return True, evidence
        # If transition matches but multiple targets succeeded, still not a
        # transition violation by itself.
        return False, evidence

    return None, {**evidence, "reason": f"unsupported invariant kind for evaluation: {kind}"}


def _fallback_vulnerability(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    target_records: list[ResponseRecord],
) -> bool:
    """Last-resort heuristic when no evaluable invariant is embedded."""

    before_discount = before_state.get("discount_yuan")
    after_discount = after_state.get("discount_yuan")
    coupon_value = after_state.get("coupon_value", before_state.get("coupon_value"))
    redemptions = after_state.get("successful_redemptions")
    if (
        _is_numeric(before_discount)
        and _is_numeric(after_discount)
        and _is_numeric(coupon_value)
        and float(after_discount) - float(before_discount) > float(coupon_value)
    ):
        return True
    if _is_numeric(redemptions) and float(redemptions) > 1:
        return True
    successes = sum(1 for record in target_records if 200 <= record.status_code < 300)
    return successes > 1


def _attempt_looks_broken(
    plan: AttackPlan,
    before_state: dict[str, Any],
    target_records: list[ResponseRecord],
) -> bool:
    successes = sum(1 for record in target_records if 200 <= record.status_code < 300)
    if successes > 1:
        return True
    violated, _ = _evaluate_invariant_violation(plan, before_state, {}, target_records)
    return violated is True


def _summarize(
    plan: AttackPlan,
    target_records: list[ResponseRecord],
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    lab_events: list[dict[str, Any]],
    intermediate_states: list[dict[str, Any]],
) -> dict[str, Any]:
    before_discount = before_state.get("discount_yuan", 0)
    after_discount = after_state.get("discount_yuan", before_discount)
    coupon_value = after_state.get("coupon_value", before_state.get("coupon_value", 50))
    successful_redemptions = after_state.get("successful_redemptions", 0)
    discount_delta = (
        after_discount - before_discount
        if _is_numeric(after_discount) and _is_numeric(before_discount)
        else None
    )

    violated, invariant_evidence = _evaluate_invariant_violation(
        plan, before_state, after_state, target_records
    )
    if violated is None:
        vulnerability_observed = _fallback_vulnerability(
            before_state, after_state, target_records
        )
        evaluation_mode = "fallback-heuristic"
    else:
        vulnerability_observed = violated
        evaluation_mode = "invariant"

    event_kinds = [str(event.get("kind")) for event in lab_events]
    stale_state_observed = any(
        state.get("coupon_used") is False for state in intermediate_states
    )
    run_evicted = (
        plan.attack_type == "run-eviction-pressure"
        and any(record.status_code == 404 for record in target_records)
    )

    return {
        "attack_type": plan.attack_type,
        "target_status_codes": [record.status_code for record in target_records],
        "target_request_count": len(target_records),
        "discount_before": before_discount,
        "discount_after": after_discount,
        "discount_delta": discount_delta,
        "coupon_value": coupon_value,
        "successful_redemptions": successful_redemptions,
        "vulnerability_observed": vulnerability_observed,
        "evaluation_mode": evaluation_mode,
        "invariant_evidence": invariant_evidence,
        "is_formal_finding": False,
        "stale_state_observed": stale_state_observed,
        "run_evicted": run_evicted,
        "availability_issue_observed": run_evicted,
        "intermediate_states": intermediate_states,
        "lab_event_kinds": event_kinds,
        "checked_events": event_kinds.count("coupon.checked"),
        "committed_events": event_kinds.count("coupon.committed"),
        "rejected_events": event_kinds.count("coupon.rejected"),
    }
