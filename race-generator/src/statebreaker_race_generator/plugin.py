"""Deterministic attack-plan generation for coupon race conditions."""

from __future__ import annotations

from collections.abc import Iterable

from statebreaker import AttackPlan, Invariant, PluginManifest, Workflow
from statebreaker.errors import PluginError
from statebreaker.models import AttackSchedule, RequestStep, StepRole

SUPPORTED_INVARIANT_KINDS = frozenset(
    {
        "max-delta",
        "single-use",
        "count-limit",
        "state-transition",
    }
)
COUPON_MARKERS = ("coupon", "discount", "优惠", "券")
OFFSET_SWEEP_MS = (10.0, 50.0, 100.0, 140.0)
BURST_CONCURRENCY = 4
# Hard cap so multiple invariants cannot explode the plan list unbounded.
MAX_PLANS_TOTAL = 40
# Concurrent retries without a full setup reset share the same prepared state and
# are usually wrong for state-changing targets; default to a single attempt.
DEFAULT_MAX_ATTEMPTS = 1


class RaceAttackGenerator:
    """Generate bounded replay plans around coupon state-changing actions."""

    manifest = PluginManifest(
        plugin_id="team.race-generator",
        name="Coupon race-condition generator",
        version="0.1.1",
        api_version="0.1",
        group="statebreaker.generator",
        capabilities=[
            "concurrent-replay",
            "burst-replay",
            "offset-sweep",
            "precondition-bypass-replay",
            "idempotency-key-reuse",
            "stale-state-assisted-replay",
            "run-eviction-pressure",
            "deterministic-output",
            "bounded-concurrency",
        ],
        description="Generates bounded race plans for coupon state invariants.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        """Return deterministic plans without making any network requests."""

        plans: list[AttackPlan] = []
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}
        seen_ids: set[str] = set()

        for invariant in sorted(invariants, key=lambda item: item.id):
            if not self._supports_coupon_invariant(invariant):
                continue

            targets = self._find_targets(workflow, invariant, step_indexes)
            for target in targets:
                for plan in self._plans_for_target(workflow, invariant, target):
                    if plan.id in seen_ids:
                        continue
                    seen_ids.add(plan.id)
                    plans.append(plan)
                    if len(plans) >= MAX_PLANS_TOTAL:
                        self._validate_references(workflow, invariants, plans)
                        return plans

        self._validate_references(workflow, invariants, plans)
        return plans

    @staticmethod
    def _supports_coupon_invariant(invariant: Invariant) -> bool:
        if invariant.kind not in SUPPORTED_INVARIANT_KINDS:
            return False
        searchable = " ".join(
            (
                invariant.id,
                invariant.selector,
                invariant.description,
                str(invariant.parameters),
            )
        ).lower()
        return any(marker in searchable for marker in COUPON_MARKERS)

    def _find_targets(
        self,
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        candidates = self._steps_between_probes(workflow, invariant, step_indexes)
        if not candidates:
            candidates = [
                step
                for step in workflow.steps
                if step.role == StepRole.ACTION and "attack-target" in step.tags
            ]

        coupon_candidates = [step for step in candidates if self._is_coupon_action(step)]
        selected = coupon_candidates or [
            step for step in candidates if "attack-target" in step.tags
        ]
        return sorted(selected, key=lambda step: step.id)

    @staticmethod
    def _steps_between_probes(
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        if not invariant.before_probe or not invariant.after_probe:
            return []
        before_index = step_indexes.get(invariant.before_probe)
        after_index = step_indexes.get(invariant.after_probe)
        if before_index is None or after_index is None or before_index >= after_index:
            return []
        return [
            step
            for step in workflow.steps[before_index + 1 : after_index]
            if step.role == StepRole.ACTION
        ]

    @staticmethod
    def _is_coupon_action(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return "attack-target" in step.tags or any(
            marker in searchable for marker in COUPON_MARKERS
        )

    @staticmethod
    def _invariant_snapshot(invariant: Invariant) -> dict[str, object]:
        """Embed the full rule so executors can evaluate without reloading files."""

        return {
            "id": invariant.id,
            "kind": invariant.kind,
            "selector": invariant.selector,
            "before_probe": invariant.before_probe,
            "after_probe": invariant.after_probe,
            "parameters": dict(invariant.parameters),
            "description": invariant.description,
        }

    def _plans_for_target(
        self,
        workflow: Workflow,
        invariant: Invariant,
        target: RequestStep,
    ) -> Iterable[AttackPlan]:
        common = {
            "workflow_name": workflow.name,
            "target_steps": [target.id],
            "session_bindings": {target.id: target.session},
            "invariant_ids": [invariant.id],
        }
        base_metadata = {
            "generator": self.manifest.plugin_id,
            "generator_version": self.manifest.version,
            "target_reason": "coupon action located between invariant state probes",
            "invariant_kind": invariant.kind,
            "invariant": self._invariant_snapshot(invariant),
            "authorized_testing_only": True,
            "verdict_note": (
                "plugin_data.vulnerability_observed is a heuristic evidence flag, "
                "not a formal Finding; use a verifier plugin for confirmed verdicts."
            ),
        }

        # Concurrent / timing families — useful for max-delta and race-sensitive rules.
        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "simultaneous-c2"),
            attack_type="concurrent-replay",
            schedule=AttackSchedule(
                concurrency=2,
                offsets_ms=[0.0, 0.0],
                options={
                    "strategy": "simultaneous",
                    "max_attempts": DEFAULT_MAX_ATTEMPTS,
                    "hard_concurrency_limit": 2,
                },
            ),
            metadata={**base_metadata, "purpose": "reproduce the minimum two-request race"},
            **common,
        )

        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "burst-c4"),
            attack_type="burst-replay",
            schedule=AttackSchedule(
                concurrency=BURST_CONCURRENCY,
                offsets_ms=[0.0] * BURST_CONCURRENCY,
                options={
                    "strategy": "simultaneous",
                    "max_attempts": 1,
                    "hard_concurrency_limit": 4,
                },
            ),
            metadata={**base_metadata, "purpose": "measure amplification under a bounded burst"},
            **common,
        )

        # Sequential families — more informative for single-use / count-limit checks.
        skipped_steps = [invariant.before_probe] if invariant.before_probe else []
        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "precondition-bypass-r4"),
            attack_type="precondition-bypass-replay",
            schedule=AttackSchedule(
                concurrency=1,
                offsets_ms=[0.0],
                options={
                    "strategy": "sequential-replay",
                    "repeat_count": 4,
                    "skip_steps": skipped_steps,
                    "continue_on_rejection": True,
                    "hard_request_limit": 4,
                    "required_executor_capability": "precondition-bypass-replay",
                },
            ),
            metadata={
                **base_metadata,
                "purpose": "test whether coupon state is enforced without a client-side probe",
                "expected_on_current_lab": "first request succeeds; later requests are rejected",
                "semantic_note": (
                    "Skipping a Workflow probe does not bypass the server-side coupon_used check"
                ),
            },
            **common,
        )

        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "shared-request-id-c2"),
            attack_type="idempotency-key-reuse",
            schedule=AttackSchedule(
                concurrency=2,
                offsets_ms=[0.0, 0.0],
                options={
                    "strategy": "simultaneous",
                    "request_id_mode": "shared",
                    "request_id_value": "coupon-redeem-duplicate",
                    "hard_concurrency_limit": 2,
                    "max_attempts": DEFAULT_MAX_ATTEMPTS,
                    "required_executor_capability": "idempotency-key-reuse",
                },
            ),
            metadata={
                **base_metadata,
                "purpose": "test whether duplicate business requests are deduplicated",
                "expected_on_current_lab": (
                    "both requests can commit because X-Request-ID is only logged"
                ),
            },
            **common,
        )

        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "midstate-followup-c2"),
            attack_type="stale-state-assisted-replay",
            schedule=AttackSchedule(
                concurrency=2,
                offsets_ms=[0.0, 60.0],
                options={
                    "strategy": "state-probe-assisted",
                    "probe_after_ms": 50.0,
                    "followup_after_ms": 60.0,
                    "hard_request_limit": 2,
                    "required_executor_capability": "stale-state-assisted-replay",
                },
            ),
            metadata={
                **base_metadata,
                "purpose": (
                    "read state during the check-to-commit window, then submit a follow-up redeem"
                ),
                "expected_on_current_lab": (
                    "mid-state still reports unused, and both redeem requests can commit"
                ),
            },
            **common,
        )

        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "run-eviction-101"),
            attack_type="run-eviction-pressure",
            schedule=AttackSchedule(
                concurrency=1,
                offsets_ms=[0.0],
                options={
                    "strategy": "run-eviction-pressure",
                    "create_count": 101,
                    "hard_setup_request_limit": 101,
                    "required_executor_capability": "run-eviction-pressure",
                },
            ),
            metadata={
                **base_metadata,
                "purpose": "test whether bounded run storage can evict an active coupon flow",
                "expected_on_current_lab": (
                    "the original run becomes unavailable after 101 new runs"
                ),
                "semantic_note": (
                    "This is a state-availability check, not a multi-use coupon exploit."
                ),
            },
            **common,
        )

        for offset_ms in OFFSET_SWEEP_MS:
            offset_label = str(int(offset_ms))
            yield AttackPlan(
                id=self._plan_id(target.id, invariant.id, f"offset-{offset_label}ms"),
                attack_type="offset-sweep",
                schedule=AttackSchedule(
                    concurrency=2,
                    offsets_ms=[0.0, offset_ms],
                    options={
                        "strategy": "fixed-offset",
                        "offset_under_test_ms": offset_ms,
                        "max_attempts": DEFAULT_MAX_ATTEMPTS,
                        "hard_concurrency_limit": 2,
                    },
                ),
                metadata={
                    **base_metadata,
                    "purpose": "estimate the exploitable check-to-commit race window",
                },
                **common,
            )

    @staticmethod
    def _plan_id(target_id: str, invariant_id: str, variant: str) -> str:
        return f"race.{target_id}.{invariant_id}.{variant}"

    @staticmethod
    def _validate_references(
        workflow: Workflow,
        invariants: list[Invariant],
        plans: list[AttackPlan],
    ) -> None:
        step_ids = {step.id for step in workflow.steps}
        session_ids = set(workflow.sessions)
        invariant_ids = {invariant.id for invariant in invariants}

        for plan in plans:
            unknown_steps = set(plan.target_steps) - step_ids
            if unknown_steps:
                raise PluginError(
                    f"generated plan references unknown steps: {sorted(unknown_steps)}"
                )
            unknown_invariants = set(plan.invariant_ids) - invariant_ids
            if unknown_invariants:
                raise PluginError(
                    f"generated plan references unknown invariants: "
                    f"{sorted(unknown_invariants)}"
                )
            unknown_sessions = set(plan.session_bindings.values()) - session_ids
            if unknown_sessions:
                raise PluginError(
                    f"generated plan references unknown sessions: {sorted(unknown_sessions)}"
                )
            if len(plan.schedule.offsets_ms) != plan.schedule.concurrency:
                raise PluginError(
                    f"plan {plan.id!r} must provide one offset per concurrent request"
                )
