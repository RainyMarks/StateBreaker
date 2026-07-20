"""Deterministic attack-plan generation for coupon race conditions."""

from __future__ import annotations

from collections.abc import Iterable

from statebreaker import AttackPlan, Invariant, PluginManifest, Workflow
from statebreaker.errors import PluginError
from statebreaker.models import AttackSchedule, RequestStep, StepRole

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

SUPPORTED_INVARIANT_KINDS = frozenset(
    {
        "max-delta",
        "single-use",
        "count-limit",
        "state-transition",
    }
)
COUPON_MARKERS = ("coupon", "discount")
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

        # Concurrent / timing families 鈥?useful for max-delta and race-sensitive rules.
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

        # Sequential families 鈥?more informative for single-use / count-limit checks.
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


STEP_SKIP_MARKERS = (
    "step-skip",
    "payment",
    "pay",
    "checkout",
    "confirm",
    "confirmation",
    "order",
)
PRECONDITION_MARKERS = (
    "precondition",
    "payment",
    "pay",
    "checkout",
    "mfa",
    "otp",
    "kyc",
    "risk",
    "verify",
    "approval",
    "auth",
)
MAX_STEP_SKIP_PLANS_TOTAL = 20


class StepSkipAttackGenerator:
    """Generate single-request plans that skip prerequisite workflow actions."""

    manifest = PluginManifest(
        plugin_id="team.step-skip-generator",
        name="Payment step-skip generator",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.generator",
        capabilities=[
            "step-skip",
            "deterministic-output",
            "workflow-dependency-analysis",
            "bounded-concurrency",
        ],
        description="Generates bounded attack plans that skip payment/precondition steps.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        plans: list[AttackPlan] = []
        seen_ids: set[str] = set()
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}

        for invariant in sorted(invariants, key=lambda item: item.id):
            if not self._supports_step_skip_invariant(invariant):
                continue
            for target in self._find_targets(workflow, invariant, step_indexes):
                for skipped in self._find_skippable_preconditions(
                    workflow, target, invariant, step_indexes
                ):
                    plan = self._plan_for_skip(workflow, invariant, target, skipped)
                    if plan.id in seen_ids:
                        continue
                    self._validate_step_skip_plan(workflow, plan)
                    seen_ids.add(plan.id)
                    plans.append(plan)
                    if len(plans) >= MAX_STEP_SKIP_PLANS_TOTAL:
                        RaceAttackGenerator._validate_references(workflow, invariants, plans)
                        return plans

        RaceAttackGenerator._validate_references(workflow, invariants, plans)
        return plans

    @staticmethod
    def _supports_step_skip_invariant(invariant: Invariant) -> bool:
        if invariant.kind not in {"state-transition", "count-limit", "single-use"}:
            return False
        searchable = " ".join(
            (
                invariant.id,
                invariant.selector,
                invariant.description,
                str(invariant.parameters),
            )
        ).lower()
        return any(marker in searchable for marker in STEP_SKIP_MARKERS)

    def _find_targets(
        self,
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        candidates = RaceAttackGenerator._steps_between_probes(workflow, invariant, step_indexes)
        if not candidates:
            candidates = [step for step in workflow.steps if step.role == StepRole.ACTION]

        selected = [step for step in candidates if self._is_confirmation_target(step)]
        if not selected:
            selected = [step for step in candidates if "attack-target" in step.tags]
        return sorted(selected, key=lambda step: step.id)

    @staticmethod
    def _is_confirmation_target(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return "attack-target" in step.tags and any(
            marker in searchable
            for marker in (
                "confirm",
                "complete",
                "finalize",
                "submit",
                "transfer",
                "withdraw",
                "ship",
            )
        )

    def _find_skippable_preconditions(
        self,
        workflow: Workflow,
        target: RequestStep,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        target_index = step_indexes[target.id]
        before_index = (
            step_indexes.get(invariant.before_probe, -1) if invariant.before_probe else -1
        )
        direct_dependencies = set(target.depends_on)
        candidates = [
            step
            for step in workflow.steps[before_index + 1 : target_index]
            if step.role == StepRole.ACTION and step.id != target.id
        ]
        selected = [
            step
            for step in candidates
            if step.id in direct_dependencies or self._is_precondition_action(step)
        ]
        return sorted(selected, key=lambda step: step.id)

    @staticmethod
    def _is_precondition_action(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return any(marker in searchable for marker in PRECONDITION_MARKERS)

    def _plan_for_skip(
        self,
        workflow: Workflow,
        invariant: Invariant,
        target: RequestStep,
        skipped: RequestStep,
    ) -> AttackPlan:
        return AttackPlan(
            id=f"step-skip.{target.id}.{invariant.id}.skip-{skipped.id}",
            workflow_name=workflow.name,
            attack_type="step-skip",
            target_steps=[target.id],
            session_bindings={target.id: target.session},
            schedule=AttackSchedule(
                concurrency=1,
                offsets_ms=[0.0],
                options={
                    "strategy": "single-target",
                    "skip_steps": [skipped.id],
                    "hard_request_limit": 1,
                    "required_executor_capability": "step-skip",
                },
            ),
            invariant_ids=[invariant.id],
            metadata={
                "generator": self.manifest.plugin_id,
                "generator_version": self.manifest.version,
                "target_reason": "confirmation action depends on a skippable precondition",
                "skipped_step": skipped.id,
                "skipped_step_tags": list(skipped.tags),
                "invariant_kind": invariant.kind,
                "invariant": RaceAttackGenerator._invariant_snapshot(invariant),
                "authorized_testing_only": True,
            },
        )

    @staticmethod
    def _validate_step_skip_plan(workflow: Workflow, plan: AttackPlan) -> None:
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}
        target_id = plan.target_steps[0]
        skip_steps = plan.schedule.options.get("skip_steps")
        if not isinstance(skip_steps, list) or not skip_steps:
            raise PluginError(f"plan {plan.id!r} must include skip_steps")
        for step_id in skip_steps:
            if not isinstance(step_id, str) or step_id not in step_indexes:
                raise PluginError(f"plan {plan.id!r} references an unknown skipped step")
            if step_indexes[step_id] >= step_indexes[target_id]:
                raise PluginError(f"plan {plan.id!r} can only skip steps before its target")


BANK_RACE_MARKERS = (
    "bank",
    "balance",
    "withdraw",
    "withdrawal",
    "debit",
    "transfer",
    "account",
)
MAX_BANK_RACE_PLANS_TOTAL = 20


class BankRaceAttackGenerator:
    """Generate bounded concurrent withdrawal/debit race plans."""

    manifest = PluginManifest(
        plugin_id="team.bank-race-generator",
        name="Bank double-withdraw race generator",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.generator",
        capabilities=[
            "concurrent-replay",
            "burst-replay",
            "offset-sweep",
            "double-withdraw-race",
            "deterministic-output",
            "bounded-concurrency",
        ],
        description="Generates bounded race plans for balance, withdrawal, and debit invariants.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        plans: list[AttackPlan] = []
        seen_ids: set[str] = set()
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}

        for invariant in sorted(invariants, key=lambda item: item.id):
            if not self._supports_bank_race_invariant(invariant):
                continue
            targets = self._find_targets(workflow, invariant, step_indexes)
            for target in targets:
                for plan in self._plans_for_target(workflow, invariant, target):
                    if plan.id in seen_ids:
                        continue
                    seen_ids.add(plan.id)
                    plans.append(plan)
                    if len(plans) >= MAX_BANK_RACE_PLANS_TOTAL:
                        RaceAttackGenerator._validate_references(workflow, invariants, plans)
                        return plans

        RaceAttackGenerator._validate_references(workflow, invariants, plans)
        return plans

    @staticmethod
    def _supports_bank_race_invariant(invariant: Invariant) -> bool:
        if invariant.kind not in {"min-value", "max-delta", "count-limit", "state-transition"}:
            return False
        searchable = " ".join(
            (
                invariant.id,
                invariant.selector,
                invariant.description,
                str(invariant.parameters),
            )
        ).lower()
        return any(marker in searchable for marker in BANK_RACE_MARKERS)

    def _find_targets(
        self,
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        candidates = RaceAttackGenerator._steps_between_probes(workflow, invariant, step_indexes)
        if not candidates:
            candidates = [
                step
                for step in workflow.steps
                if step.role == StepRole.ACTION and "attack-target" in step.tags
            ]

        selected = [step for step in candidates if self._is_withdraw_action(step)]
        if not selected:
            selected = [step for step in candidates if "attack-target" in step.tags]
        return sorted(selected, key=lambda step: step.id)

    @staticmethod
    def _is_withdraw_action(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return "attack-target" in step.tags and any(
            marker in searchable for marker in ("withdraw", "debit", "transfer")
        )

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
            "target_reason": "withdraw/debit action located between balance state probes",
            "invariant_kind": invariant.kind,
            "invariant": RaceAttackGenerator._invariant_snapshot(invariant),
            "authorized_testing_only": True,
        }

        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "simultaneous-c2"),
            attack_type="concurrent-replay",
            schedule=AttackSchedule(
                concurrency=2,
                offsets_ms=[0.0, 0.0],
                options={
                    "strategy": "simultaneous",
                    "max_attempts": 1,
                    "hard_concurrency_limit": 2,
                    "required_executor_capability": "concurrent-replay",
                },
            ),
            metadata={**base_metadata, "purpose": "test two simultaneous withdrawals"},
            **common,
        )

        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "burst-c4"),
            attack_type="burst-replay",
            schedule=AttackSchedule(
                concurrency=4,
                offsets_ms=[0.0, 0.0, 0.0, 0.0],
                options={
                    "strategy": "simultaneous",
                    "max_attempts": 1,
                    "hard_concurrency_limit": 4,
                    "required_executor_capability": "burst-replay",
                },
            ),
            metadata={**base_metadata, "purpose": "measure overdraft amplification"},
            **common,
        )

        for offset_ms in (10.0, 50.0, 100.0, 140.0):
            yield AttackPlan(
                id=self._plan_id(target.id, invariant.id, f"offset-{int(offset_ms)}ms"),
                attack_type="offset-sweep",
                schedule=AttackSchedule(
                    concurrency=2,
                    offsets_ms=[0.0, offset_ms],
                    options={
                        "strategy": "fixed-offset",
                        "offset_under_test_ms": offset_ms,
                        "max_attempts": 1,
                        "hard_concurrency_limit": 2,
                        "required_executor_capability": "offset-sweep",
                    },
                ),
                metadata={**base_metadata, "purpose": "estimate the withdrawal race window"},
                **common,
            )

    @staticmethod
    def _plan_id(target_id: str, invariant_id: str, variant: str) -> str:
        return f"bank-race.{target_id}.{invariant_id}.{variant}"



CALLBACK_IDEMPOTENCY_MARKERS = (
    "callback",
    "webhook",
    "payment_event",
    "event_id",
    "idempotency",
    "merchant_credit",
)
MAX_CALLBACK_PLANS_TOTAL = 20


class PaymentCallbackIdempotencyGenerator:
    """Generate duplicate payment callback plans."""

    manifest = PluginManifest(
        plugin_id="team.payment-callback-generator",
        name="Payment callback idempotency generator",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.generator",
        capabilities=[
            "concurrent-replay",
            "burst-replay",
            "idempotency-key-reuse",
            "sequential-replay",
            "payment-callback-idempotency",
            "deterministic-output",
            "bounded-concurrency",
        ],
        description="Generates duplicate callback plans for payment idempotency invariants.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        plans: list[AttackPlan] = []
        seen_ids: set[str] = set()
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}
        for invariant in sorted(invariants, key=lambda item: item.id):
            if not self._supports_callback_invariant(invariant):
                continue
            for target in self._find_targets(workflow, invariant, step_indexes):
                for plan in self._plans_for_target(workflow, invariant, target):
                    if plan.id in seen_ids:
                        continue
                    seen_ids.add(plan.id)
                    plans.append(plan)
                    if len(plans) >= MAX_CALLBACK_PLANS_TOTAL:
                        RaceAttackGenerator._validate_references(workflow, invariants, plans)
                        return plans
        RaceAttackGenerator._validate_references(workflow, invariants, plans)
        return plans

    @staticmethod
    def _supports_callback_invariant(invariant: Invariant) -> bool:
        if invariant.kind not in {"max-delta", "count-limit", "single-use"}:
            return False
        searchable = " ".join(
            (invariant.id, invariant.selector, invariant.description, str(invariant.parameters))
        ).lower()
        return any(marker in searchable for marker in CALLBACK_IDEMPOTENCY_MARKERS)

    def _find_targets(
        self,
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        candidates = RaceAttackGenerator._steps_between_probes(workflow, invariant, step_indexes)
        if not candidates:
            candidates = [step for step in workflow.steps if step.role == StepRole.ACTION]
        selected = [step for step in candidates if self._is_callback_action(step)]
        if not selected:
            selected = [step for step in candidates if "attack-target" in step.tags]
        return sorted(selected, key=lambda step: step.id)

    @staticmethod
    def _is_callback_action(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return "attack-target" in step.tags and any(
            marker in searchable for marker in ("callback", "webhook", "payment")
        )

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
            "target_reason": "payment callback action located between state probes",
            "invariant_kind": invariant.kind,
            "invariant": RaceAttackGenerator._invariant_snapshot(invariant),
            "authorized_testing_only": True,
        }
        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "simultaneous-c2"),
            attack_type="concurrent-replay",
            schedule=AttackSchedule(
                concurrency=2,
                offsets_ms=[0.0, 0.0],
                options={
                    "strategy": "simultaneous",
                    "max_attempts": 1,
                    "hard_concurrency_limit": 2,
                    "required_executor_capability": "concurrent-replay",
                },
            ),
            metadata={**base_metadata, "purpose": "submit two identical callbacks at once"},
            **common,
        )
        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "burst-c4"),
            attack_type="burst-replay",
            schedule=AttackSchedule(
                concurrency=4,
                offsets_ms=[0.0, 0.0, 0.0, 0.0],
                options={
                    "strategy": "simultaneous",
                    "max_attempts": 1,
                    "hard_concurrency_limit": 4,
                    "required_executor_capability": "burst-replay",
                },
            ),
            metadata={**base_metadata, "purpose": "amplify duplicate callback processing"},
            **common,
        )
        yield AttackPlan(
            id=self._plan_id(target.id, invariant.id, "sequential-r3"),
            attack_type="sequential-replay",
            schedule=AttackSchedule(
                concurrency=1,
                offsets_ms=[0.0],
                options={
                    "strategy": "sequential-replay",
                    "repeat_count": 3,
                    "continue_on_rejection": True,
                    "hard_request_limit": 3,
                    "required_executor_capability": "sequential-replay",
                },
            ),
            metadata={**base_metadata, "purpose": "replay one callback sequentially"},
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
                    "request_id_value": "payment-callback-duplicate",
                    "hard_concurrency_limit": 2,
                    "max_attempts": 1,
                    "required_executor_capability": "idempotency-key-reuse",
                },
            ),
            metadata={**base_metadata, "purpose": "reuse one request id for duplicate callback"},
            **common,
        )

    @staticmethod
    def _plan_id(target_id: str, invariant_id: str, variant: str) -> str:
        return f"callback-idempotency.{target_id}.{invariant_id}.{variant}"


REFUND_FULFILL_MARKERS = (
    "refund",
    "fulfill",
    "fulfilled",
    "ship",
    "shipment",
    "refunded_and_fulfilled",
)
MAX_REFUND_FULFILL_PLANS_TOTAL = 10


class RefundFulfillRaceGenerator:
    """Generate refund-versus-fulfillment race plans."""

    manifest = PluginManifest(
        plugin_id="team.refund-fulfill-generator",
        name="Refund vs fulfill race generator",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.generator",
        capabilities=[
            "parallel-step-race",
            "refund-vs-fulfill-race",
            "deterministic-output",
            "bounded-concurrency",
        ],
        description="Generates two-endpoint races between refund and fulfillment actions.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        plans: list[AttackPlan] = []
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}
        seen_ids: set[str] = set()
        for invariant in sorted(invariants, key=lambda item: item.id):
            if not self._supports_refund_fulfill_invariant(invariant):
                continue
            pair = self._find_refund_fulfill_pair(workflow, invariant, step_indexes)
            if pair is None:
                continue
            refund_step, fulfill_step = pair
            for plan in self._plans_for_pair(workflow, invariant, refund_step, fulfill_step):
                if plan.id in seen_ids:
                    continue
                seen_ids.add(plan.id)
                plans.append(plan)
                if len(plans) >= MAX_REFUND_FULFILL_PLANS_TOTAL:
                    break
        RaceAttackGenerator._validate_references(workflow, invariants, plans)
        return plans

    @staticmethod
    def _supports_refund_fulfill_invariant(invariant: Invariant) -> bool:
        if invariant.kind not in {"state-transition", "count-limit"}:
            return False
        searchable = " ".join(
            (invariant.id, invariant.selector, invariant.description, str(invariant.parameters))
        ).lower()
        return any(marker in searchable for marker in REFUND_FULFILL_MARKERS)

    def _find_refund_fulfill_pair(
        self,
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> tuple[RequestStep, RequestStep] | None:
        candidates = RaceAttackGenerator._steps_between_probes(workflow, invariant, step_indexes)
        if not candidates:
            candidates = [step for step in workflow.steps if step.role == StepRole.ACTION]
        refund = next((step for step in candidates if self._is_refund_action(step)), None)
        fulfill = next((step for step in candidates if self._is_fulfill_action(step)), None)
        if refund is None or fulfill is None:
            return None
        return refund, fulfill

    @staticmethod
    def _is_refund_action(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return "refund" in searchable

    @staticmethod
    def _is_fulfill_action(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return any(marker in searchable for marker in ("fulfill", "ship", "shipment"))

    def _plans_for_pair(
        self,
        workflow: Workflow,
        invariant: Invariant,
        refund_step: RequestStep,
        fulfill_step: RequestStep,
    ) -> Iterable[AttackPlan]:
        variants = (
            ("simultaneous-c2", [refund_step, fulfill_step], [0.0, 0.0]),
            ("refund-first-50ms", [refund_step, fulfill_step], [0.0, 50.0]),
            ("fulfill-first-50ms", [fulfill_step, refund_step], [0.0, 50.0]),
        )
        for label, steps, offsets in variants:
            yield AttackPlan(
                id=(
                    f"refund-fulfill.{steps[0].id}.{steps[1].id}."
                    f"{invariant.id}.{label}"
                ),
                workflow_name=workflow.name,
                attack_type="parallel-step-race",
                target_steps=[step.id for step in steps],
                session_bindings={step.id: step.session for step in steps},
                schedule=AttackSchedule(
                    concurrency=2,
                    offsets_ms=offsets,
                    options={
                        "strategy": "parallel-steps",
                        "hard_request_limit": 2,
                        "required_executor_capability": "parallel-step-race",
                    },
                ),
                invariant_ids=[invariant.id],
                metadata={
                    "generator": self.manifest.plugin_id,
                    "generator_version": self.manifest.version,
                    "target_reason": "refund and fulfillment actions can race from one paid state",
                    "invariant_kind": invariant.kind,
                    "invariant": RaceAttackGenerator._invariant_snapshot(invariant),
                    "authorized_testing_only": True,
                },
            )



PAYMENT_BINDING_MARKERS = (
    "authorization",
    "authorization-bypass",
    "binding",
    "binding-mismatch",
    "idor",
    "ownership",
    "payment_token",
    "paid_by",
    "bob_paid_by_alice",
    "bob_paid_with_alice_token",
)
MAX_PAYMENT_BINDING_PLANS_TOTAL = 10


class PaymentBindingAttackGenerator:
    """Generate single-request payment authorization and binding-mismatch plans."""

    manifest = PluginManifest(
        plugin_id="team.payment-binding-generator",
        name="Payment authorization/binding generator",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.generator",
        capabilities=[
            "authorization-bypass",
            "binding-mismatch",
            "payment-binding-mismatch",
            "deterministic-output",
            "bounded-concurrency",
        ],
        description="Generates IDOR-style payment and token/order binding mismatch plans.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        plans: list[AttackPlan] = []
        seen_ids: set[str] = set()
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}
        for invariant in sorted(invariants, key=lambda item: item.id):
            if not self._supports_payment_binding_invariant(invariant):
                continue
            for target in self._find_targets(workflow, invariant, step_indexes):
                plan = self._plan_for_target(workflow, invariant, target)
                if plan.id in seen_ids:
                    continue
                seen_ids.add(plan.id)
                plans.append(plan)
                if len(plans) >= MAX_PAYMENT_BINDING_PLANS_TOTAL:
                    RaceAttackGenerator._validate_references(workflow, invariants, plans)
                    return plans
        RaceAttackGenerator._validate_references(workflow, invariants, plans)
        return plans

    @staticmethod
    def _supports_payment_binding_invariant(invariant: Invariant) -> bool:
        if invariant.kind not in {"state-transition", "count-limit", "single-use"}:
            return False
        searchable = " ".join(
            (invariant.id, invariant.selector, invariant.description, str(invariant.parameters))
        ).lower()
        return any(marker in searchable for marker in PAYMENT_BINDING_MARKERS)

    def _find_targets(
        self,
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        candidates = RaceAttackGenerator._steps_between_probes(workflow, invariant, step_indexes)
        if not candidates:
            candidates = [step for step in workflow.steps if step.role == StepRole.ACTION]
        selected = [step for step in candidates if self._is_payment_binding_target(step)]
        if not selected:
            selected = [step for step in candidates if "attack-target" in step.tags]
        return sorted(selected, key=lambda step: step.id)

    @staticmethod
    def _is_payment_binding_target(step: RequestStep) -> bool:
        searchable = " ".join((step.id, step.request.path, *step.tags)).lower()
        return "attack-target" in step.tags and any(
            marker in searchable for marker in PAYMENT_BINDING_MARKERS
        )

    def _plan_for_target(
        self,
        workflow: Workflow,
        invariant: Invariant,
        target: RequestStep,
    ) -> AttackPlan:
        attack_type = self._attack_type_for_target(target)
        base_metadata = {
            "generator": self.manifest.plugin_id,
            "generator_version": self.manifest.version,
            "target_reason": "payment ownership or token/order binding action",
            "invariant_kind": invariant.kind,
            "invariant": RaceAttackGenerator._invariant_snapshot(invariant),
            "authorized_testing_only": True,
        }
        return AttackPlan(
            id=f"payment-binding.{target.id}.{invariant.id}.single",
            workflow_name=workflow.name,
            attack_type=attack_type,
            target_steps=[target.id],
            session_bindings={target.id: target.session},
            schedule=AttackSchedule(
                concurrency=1,
                offsets_ms=[0.0],
                options={
                    "strategy": "single-target",
                    "hard_request_limit": 1,
                    "required_executor_capability": attack_type,
                },
            ),
            invariant_ids=[invariant.id],
            metadata={
                **base_metadata,
                "purpose": "submit one cross-principal or cross-resource payment request",
            },
        )

    @staticmethod
    def _attack_type_for_target(target: RequestStep) -> str:
        searchable = " ".join((target.id, target.request.path, *target.tags)).lower()
        if any(marker in searchable for marker in ("binding", "token-mismatch", "payment_token")):
            return "binding-mismatch"
        return "authorization-bypass"


GENERIC_REPLAY_MAX_PLANS_TOTAL = 30


class GenericInvariantReplayGenerator:
    """Fallback generator for tagged state-changing actions around invariants."""

    manifest = PluginManifest(
        plugin_id="team.generic-replay-generator",
        name="Generic invariant replay generator",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.generator",
        capabilities=[
            "concurrent-replay",
            "burst-replay",
            "offset-sweep",
            "idempotency-key-reuse",
            "sequential-replay",
            "invariant-driven-replay",
            "deterministic-output",
            "bounded-concurrency",
        ],
        description="Generates bounded replay plans for generic tagged action steps.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        plans: list[AttackPlan] = []
        seen_ids: set[str] = set()
        step_indexes = {step.id: index for index, step in enumerate(workflow.steps)}
        for invariant in sorted(invariants, key=lambda item: item.id):
            if invariant.kind not in SUPPORTED_INVARIANT_KINDS | {"min-value"}:
                continue
            for target in self._find_targets(workflow, invariant, step_indexes):
                for plan in self._plans_for_target(workflow, invariant, target):
                    if plan.id in seen_ids:
                        continue
                    seen_ids.add(plan.id)
                    plans.append(plan)
                    if len(plans) >= GENERIC_REPLAY_MAX_PLANS_TOTAL:
                        RaceAttackGenerator._validate_references(workflow, invariants, plans)
                        return plans
        RaceAttackGenerator._validate_references(workflow, invariants, plans)
        return plans

    @staticmethod
    def _find_targets(
        workflow: Workflow,
        invariant: Invariant,
        step_indexes: dict[str, int],
    ) -> list[RequestStep]:
        candidates = RaceAttackGenerator._steps_between_probes(workflow, invariant, step_indexes)
        if not candidates:
            candidates = [step for step in workflow.steps if step.role == StepRole.ACTION]
        tagged = [step for step in candidates if "attack-target" in step.tags]
        selected = tagged or [
            step for step in candidates if step.request.method in STATE_CHANGING_METHODS
        ]
        return sorted(selected, key=lambda step: step.id)[:3]

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
            "target_reason": "generic state-changing action selected from invariant window",
            "invariant_kind": invariant.kind,
            "invariant": RaceAttackGenerator._invariant_snapshot(invariant),
            "authorized_testing_only": True,
        }
        variants = (
            (
                "simultaneous-c2",
                "concurrent-replay",
                AttackSchedule(
                    concurrency=2,
                    offsets_ms=[0.0, 0.0],
                    options={
                        "strategy": "simultaneous",
                        "max_attempts": 1,
                        "hard_concurrency_limit": 2,
                        "required_executor_capability": "concurrent-replay",
                    },
                ),
            ),
            (
                "offset-50ms",
                "offset-sweep",
                AttackSchedule(
                    concurrency=2,
                    offsets_ms=[0.0, 50.0],
                    options={
                        "strategy": "fixed-offset",
                        "offset_under_test_ms": 50.0,
                        "max_attempts": 1,
                        "hard_concurrency_limit": 2,
                        "required_executor_capability": "offset-sweep",
                    },
                ),
            ),
            (
                "sequential-r3",
                "sequential-replay",
                AttackSchedule(
                    concurrency=1,
                    offsets_ms=[0.0],
                    options={
                        "strategy": "sequential-replay",
                        "repeat_count": 3,
                        "continue_on_rejection": True,
                        "hard_request_limit": 3,
                        "required_executor_capability": "sequential-replay",
                    },
                ),
            ),
        )
        for label, attack_type, schedule in variants:
            yield AttackPlan(
                id=f"generic-replay.{target.id}.{invariant.id}.{label}",
                attack_type=attack_type,
                schedule=schedule,
                metadata={**base_metadata, "purpose": "generic invariant-driven replay"},
                **common,
            )


class BusinessLogicAttackGenerator:
    """Aggregate the bundled business-logic attack strategies."""

    manifest = PluginManifest(
        plugin_id="team.business-logic-generator",
        name="Business logic attack generator",
        version="0.1.0",
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
            "step-skip",
            "double-withdraw-race",
            "payment-callback-idempotency",
            "refund-vs-fulfill-race",
            "parallel-step-race",
            "authorization-bypass",
            "binding-mismatch",
            "payment-binding-mismatch",
            "sequential-replay",
            "invariant-driven-replay",
            "deterministic-output",
            "bounded-concurrency",
        ],
        description="Runs all bundled business-logic generators and returns one strategy set.",
    )

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        generators = (
            RaceAttackGenerator(),
            StepSkipAttackGenerator(),
            BankRaceAttackGenerator(),
            PaymentCallbackIdempotencyGenerator(),
            RefundFulfillRaceGenerator(),
            PaymentBindingAttackGenerator(),
        )
        plans: list[AttackPlan] = []
        seen_ids: set[str] = set()
        for generator in generators:
            for plan in await generator.generate(workflow, invariants):
                if plan.id in seen_ids:
                    continue
                seen_ids.add(plan.id)
                plans.append(plan)
        if not plans:
            for plan in await GenericInvariantReplayGenerator().generate(workflow, invariants):
                if plan.id in seen_ids:
                    continue
                seen_ids.add(plan.id)
                plans.append(plan)
        return sorted(plans, key=lambda item: item.id)