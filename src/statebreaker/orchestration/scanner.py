"""The scan orchestrator: trace in, findings out.

Stages: graph discovery -> baseline -> planning -> trials -> verdicts.
Checkpoints are written after each durable stage.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.baseline.learner import BaselineLearner
from statebreaker.config.loader import ScopeGuard
from statebreaker.config.models import ProjectConfig
from statebreaker.discovery.pair_generator import generate_candidates
from statebreaker.errors import BudgetExhaustedError, StateBreakerError
from statebreaker.execution.client import BudgetTracker, HttpSender
from statebreaker.execution.controller import ExperimentController
from statebreaker.execution.reset import (
    ApiResetStrategy,
    FreshResourceResetStrategy,
    NoResetStrategy,
    ResetStrategy,
)
from statebreaker.execution.sessions import SessionManager
from statebreaker.execution.timing import release_spread_ms
from statebreaker.execution.transports.async_http import AsyncHttpBackend
from statebreaker.execution.transports.base import SchedulerBackend
from statebreaker.execution.transports.http1_gate import Http1LastByteBackend
from statebreaker.execution.transports.http2_gate import Http2StreamGateBackend
from statebreaker.minimization import (
    SIMPLICITY_ORDER,
    TrialSignal,
    measure_run_statistics,
    minimize_concurrency,
    minimize_setup_steps,
    simplest_scheduler,
)
from statebreaker.models.base import utc_now
from statebreaker.models.capture import CapturedTrace, RequestTemplate
from statebreaker.models.discovery import AttackPlan, RaceCandidate
from statebreaker.models.execution import ExecutionTrial, ScanBudget
from statebreaker.models.findings import Finding, RunStatistics, ScanOutcome
from statebreaker.models.state import BaselineProfile
from statebreaker.models.workflow import WorkflowGraph
from statebreaker.oracle.comparator import summarize_trial
from statebreaker.oracle.verifier import evaluate_candidate, evaluate_trial
from statebreaker.orchestration.checkpoints import ScanCheckpoint, save_checkpoint
from statebreaker.orchestration.stages import build_graph_discovery, session_configs
from statebreaker.planning.budget import affordable_plans
from statebreaker.planning.synthesizer import synthesize_plans
from statebreaker.reporting import write_finding_reports

# The durable scan pipeline, in order. A checkpoint records every stage up to
# and including the one just finished.
_STAGE_ORDER = ("capture", "graph", "baseline", "planning", "trials")


@dataclass
class BaselineStageResult:
    """Artifacts needed after baseline learning."""

    profile: BaselineProfile
    learner: BaselineLearner


@dataclass
class PlanningStageResult:
    """Candidate and plan artifacts produced before execution."""

    candidates: list[RaceCandidate]
    plans: list[AttackPlan]


class AutoRaceScanner:
    """Fully automatic race discovery: no hand-written rules, ever."""

    def __init__(
        self,
        store: ArtifactStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        extra_backends: dict[str, SchedulerBackend] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport
        self._extra_backends = dict(extra_backends or {})

    async def scan(
        self,
        project: ProjectConfig,
        *,
        capture_id: str,
        budget: ScanBudget,
    ) -> ScanOutcome:
        store = self._store
        tracker = BudgetTracker(budget)
        outcome = ScanOutcome(
            scan_id=f"scan-{capture_id}",
            project=project.project.name,
            capture_id=capture_id,
        )
        session_id = self._primary_session(project)
        trace = store.load("captures", capture_id, CapturedTrace)
        session_config_map = session_configs(project, trace)
        session_ids = list(session_config_map) or ["default"]

        sessions = SessionManager(
            project.project.base_url,
            session_config_map,
            transport=self._transport,
        )
        scope = ScopeGuard(project)
        sender = HttpSender(
            sessions,
            scope,
            budget=tracker,
            requests_per_second=project.scope.requests_per_second or 1000,
        )
        try:
            reset = self._reset_strategy(project, sender, session_id)
            await reset.prepare_trial(f"discover-{outcome.scan_id}")
            graph, templates = await self._discover_graph(
                project,
                trace,
                sender,
                session_id=session_id,
                session_ids=session_ids,
                outcome=outcome,
            )
            baseline = await self._learn_baseline(
                project,
                graph,
                templates,
                capture_id=capture_id,
                sender=sender,
                reset=reset,
                session_id=session_id,
                outcome=outcome,
            )
            planning = self._plan_candidates(
                project,
                graph,
                templates,
                baseline.profile,
                budget=budget,
                session_ids=session_ids,
                outcome=outcome,
            )
            backends = self._backends(sessions, scope, tracker)
            controller = self._controller(
                sender,
                reset,
                backends,
                graph,
                templates,
                baseline,
                tracker=tracker,
                session_id=session_id,
            )
            repetitions = max(1, min(project.execution.repetitions, 20))
            findings = await self._execute_plans(
                controller,
                planning.plans,
                baseline.profile,
                repetitions=repetitions,
                require_state_evidence=project.oracle.require_state_evidence_for_confirmed,
                outcome=outcome,
                available_schedulers=list(backends),
                statistics_rounds=min(10, budget.maximum_trial_rounds),
            )
            outcome.finding_ids.extend(f.finding_id for f in findings)
            save_checkpoint(store, self._checkpoint(outcome, "trials"))
        finally:
            await sessions.aclose()

        self._complete_outcome(outcome, tracker)
        store.save("scans", outcome.scan_id, outcome)
        return outcome

    # -- scan stages ----------------------------------------------------------

    async def _discover_graph(
        self,
        project: ProjectConfig,
        trace: CapturedTrace,
        sender: HttpSender,
        *,
        session_id: str,
        session_ids: list[str],
        outcome: ScanOutcome,
    ) -> tuple[WorkflowGraph, list[RequestTemplate]]:
        discovery = await build_graph_discovery(
            project,
            trace,
            sender,
            session_id=session_id,
            session_ids=session_ids,
            clone_session_probes=True,
        )
        graph = discovery.graph
        self._store.save("graphs", graph.graph_id, graph)
        outcome.graph_id = graph.graph_id
        save_checkpoint(self._store, self._checkpoint(outcome, "graph"))
        return graph, discovery.templates

    async def _learn_baseline(
        self,
        project: ProjectConfig,
        graph: WorkflowGraph,
        templates: list[RequestTemplate],
        *,
        capture_id: str,
        sender: HttpSender,
        reset: ResetStrategy,
        session_id: str,
        outcome: ScanOutcome,
    ) -> BaselineStageResult:
        learner = BaselineLearner(sender, reset, session_id=session_id)
        profile, baseline_trials = await learner.learn(
            graph=graph,
            templates=templates,
            probes=graph.state_probes,
            capture_id=capture_id,
            max_actions=project.discovery.max_candidates,
        )
        self._store.save("baselines", profile.profile_id, profile)
        for trial in baseline_trials:
            self._store.save("trials", trial.trial_id, trial)
        outcome.baseline_id = profile.profile_id
        outcome.trial_ids.extend(trial.trial_id for trial in baseline_trials)
        save_checkpoint(self._store, self._checkpoint(outcome, "baseline"))
        return BaselineStageResult(profile=profile, learner=learner)

    def _plan_candidates(
        self,
        project: ProjectConfig,
        graph: WorkflowGraph,
        templates: list[RequestTemplate],
        profile: BaselineProfile,
        *,
        budget: ScanBudget,
        session_ids: list[str],
        outcome: ScanOutcome,
    ) -> PlanningStageResult:
        candidates = generate_candidates(
            graph,
            templates,
            profile.effects,
            sessions=session_ids,
            max_candidates=project.discovery.max_candidates,
            max_action_pairs=project.discovery.max_action_pairs,
        )
        for candidate in candidates:
            self._store.save("candidates", candidate.candidate_id, candidate)
        outcome.candidate_ids.extend(c.candidate_id for c in candidates)

        plans = synthesize_plans(
            candidates,
            templates,
            probe_ids=[probe.probe_id for probe in graph.state_probes],
            schedulers=project.execution.schedulers,
            concurrencies=project.execution.concurrency,
            offsets_ms=project.execution.offsets_ms,
            reset_strategy=(project.reset.strategy if project.reset else "fresh-resource"),
            sessions=session_ids,
        )
        plans = affordable_plans(plans, budget, project.execution.repetitions)
        for plan in plans:
            self._store.save("plans", plan.plan_id, plan)
        outcome.plan_ids.extend(plan.plan_id for plan in plans)
        save_checkpoint(self._store, self._checkpoint(outcome, "planning"))
        return PlanningStageResult(candidates=candidates, plans=plans)

    @staticmethod
    def _controller(
        sender: HttpSender,
        reset: ResetStrategy,
        backends: dict[str, SchedulerBackend],
        graph: WorkflowGraph,
        templates: list[RequestTemplate],
        baseline: BaselineStageResult,
        *,
        tracker: BudgetTracker,
        session_id: str,
    ) -> ExperimentController:
        return ExperimentController(
            sender=sender,
            reset=reset,
            backends=backends,
            probes=graph.state_probes,
            normalizers=baseline.learner.normalizers,
            bindings=graph.variable_bindings,
            templates={t.template_id: t for t in templates},
            budget=tracker,
            session_id=session_id,
        )

    # -- trials and finding finalization -------------------------------------

    async def _execute_plans(
        self,
        controller: ExperimentController,
        plans: list[AttackPlan],
        profile: BaselineProfile,
        *,
        repetitions: int,
        require_state_evidence: bool,
        outcome: ScanOutcome,
        available_schedulers: list[str],
        statistics_rounds: int,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for plan in plans:
            candidate = self._store.load("candidates", plan.candidate_id, RaceCandidate)
            try:
                control = await controller.run_trial(plan, role="control")
            except StateBreakerError as exc:
                finding = _failed_plan_finding(candidate, plan, exc)
                self._store.save("findings", finding.finding_id, finding)
                findings.append(finding)
                continue
            self._store.save("trials", control.trial_id, control)
            outcome.trial_ids.append(control.trial_id)
            attacks = []
            for _ in range(repetitions):
                try:
                    attack = await controller.run_trial(plan, role="attack")
                except StateBreakerError as exc:
                    finding = _failed_plan_finding(candidate, plan, exc)
                    finding.evidence_refs.append(control.trial_id)
                    self._store.save("findings", finding.finding_id, finding)
                    findings.append(finding)
                    break
                self._store.save("trials", attack.trial_id, attack)
                outcome.trial_ids.append(attack.trial_id)
                attacks.append(attack)
            if len(attacks) < repetitions:
                continue
            finding = evaluate_candidate(
                candidate,
                profile.invariants,
                control,
                attacks,
                plan_id=plan.plan_id,
                require_state_evidence=require_state_evidence,
            )
            if finding.verdict == "confirmed":
                finding = await self._finalize_confirmed(
                    finding,
                    plan,
                    control,
                    attacks,
                    controller=controller,
                    profile=profile,
                    outcome=outcome,
                    available_schedulers=available_schedulers,
                    statistics_rounds=statistics_rounds,
                )
            self._store.save("findings", finding.finding_id, finding)
            findings.append(finding)
        return findings

    async def _finalize_confirmed(
        self,
        finding: Finding,
        plan: AttackPlan,
        control: ExecutionTrial,
        attacks: list[ExecutionTrial],
        *,
        controller: ExperimentController,
        profile: BaselineProfile,
        outcome: ScanOutcome,
        available_schedulers: list[str],
        statistics_rounds: int,
    ) -> Finding:
        """Minimize a confirmed plan, measure repeatability, emit reports."""
        control_summary = summarize_trial(control)
        invariants = profile.invariants

        async def fire(
            plan_variant: AttackPlan, scheduler_id: str | None = None
        ) -> tuple[bool, ExecutionTrial]:
            trial = await controller.run_trial(
                plan_variant, role="attack", scheduler_id=scheduler_id
            )
            self._store.save("trials", trial.trial_id, trial)
            outcome.trial_ids.append(trial.trial_id)
            evidence = evaluate_trial(invariants, control_summary, trial)
            return (evidence.state_anomaly or evidence.response_anomaly), trial

        async def triggers(plan_variant: AttackPlan) -> bool:
            # A failed trial means "does not trigger" for the search. Budget
            # exhaustion is the only failure that stops minimization.
            try:
                triggered, _ = await fire(plan_variant)
                return triggered
            except BudgetExhaustedError:
                raise
            except (StateBreakerError, OSError):
                return False

        best_plan = plan
        triggering_trials: list[ExecutionTrial] = []
        try:
            chosen = await simplest_scheduler(
                lambda scheduler: triggers(best_plan.model_copy(update={"scheduler": scheduler})),
                [s for s in available_schedulers if s in SIMPLICITY_ORDER],
            )
            if chosen is not None:
                best_plan = best_plan.model_copy(update={"scheduler": chosen})
            best_plan = await minimize_concurrency(triggers, best_plan)
            best_plan = await minimize_setup_steps(triggers, best_plan, max_tests=6)
        except BudgetExhaustedError:
            pass

        async def one_round() -> TrialSignal:
            triggered, trial = await fire(best_plan)
            if triggered:
                triggering_trials.append(trial)
            return TrialSignal(
                triggered=triggered,
                release_skew_ms=release_spread_ms(trial.timeline),
                elapsed_ms=(trial.completed_at_ns - trial.started_at_ns) / 1e6,
            )

        statistics = RunStatistics()
        try:
            statistics = await measure_run_statistics(one_round, rounds=statistics_rounds)
        except BudgetExhaustedError:
            if triggering_trials:
                statistics = RunStatistics(
                    rounds=len(triggering_trials),
                    successes=len(triggering_trials),
                    success_rate=1.0,
                )

        best_plan = best_plan.model_copy(update={"plan_id": f"{plan.plan_id}-min"})
        self._store.save("plans", best_plan.plan_id, best_plan)
        outcome.plan_ids.append(best_plan.plan_id)

        explanation = list(finding.explanation)
        explanation.append(
            f"minimized to {len(best_plan.action_instances)} instance(s) on "
            f"scheduler {best_plan.scheduler}; repeatability "
            f"{statistics.successes}/{statistics.rounds}"
        )
        evidence_refs = list(finding.evidence_refs) + [
            trial.trial_id for trial in triggering_trials
        ]
        finding = finding.model_copy(
            update={
                "minimized_plan_id": best_plan.plan_id,
                "minimized_plan": best_plan,
                "success_rate": statistics.success_rate or finding.success_rate,
                "minimum_concurrency": len(best_plan.action_instances),
                "best_scheduler": best_plan.scheduler,
                "statistics": statistics,
                "evidence_refs": evidence_refs,
                "explanation": explanation,
            }
        )
        poc_trial = triggering_trials[0] if triggering_trials else (attacks[0] if attacks else None)
        write_finding_reports(
            self._store,
            finding,
            best_plan,
            control=control,
            attacks=attacks + triggering_trials,
            poc_trial=poc_trial,
        )
        return finding

    # -- helpers --------------------------------------------------------------

    def _backends(
        self,
        sessions: SessionManager,
        scope: ScopeGuard,
        tracker: BudgetTracker,
    ) -> dict[str, SchedulerBackend]:
        backends: dict[str, SchedulerBackend] = {
            "async-http": AsyncHttpBackend(sessions, scope, budget=tracker),
            # Raw-socket precision gates need no sessions. Registration is cheap,
            # and unused backends never open a connection.
            "http1-last-byte": Http1LastByteBackend(scope, budget=tracker),
            "http2-stream-gate": Http2StreamGateBackend(scope, budget=tracker),
            **self._extra_backends,
        }
        return backends

    @staticmethod
    def _primary_session(project: ProjectConfig) -> str:
        return next(iter(project.sessions), "default")

    @staticmethod
    def _reset_strategy(
        project: ProjectConfig, sender: HttpSender, session_id: str
    ) -> ResetStrategy:
        config = project.reset
        if config is not None:
            if config.strategy == "api" and config.endpoint:
                return ApiResetStrategy(sender, config.endpoint, session_id=session_id)
            if config.strategy == "none":
                return NoResetStrategy()
        return FreshResourceResetStrategy()

    @staticmethod
    def _complete_outcome(outcome: ScanOutcome, tracker: BudgetTracker) -> None:
        outcome.status = "completed"
        outcome.stats = {
            "requests_used": tracker.requests_used,
            "trials_used": tracker.trials_used,
            "elapsed_seconds": round(tracker.elapsed_seconds(), 3),
            "confirmed_findings": len(outcome.finding_ids),
        }
        outcome.completed_at = utc_now()

    @staticmethod
    def _checkpoint(outcome: ScanOutcome, stage: str) -> ScanCheckpoint:
        completed = list(_STAGE_ORDER[: _STAGE_ORDER.index(stage) + 1])
        return ScanCheckpoint(
            scan_id=outcome.scan_id,
            stage=stage,
            completed_stages=completed,
            artifact_refs={
                "graph": outcome.graph_id,
                "baseline": outcome.baseline_id,
                "candidates": outcome.candidate_ids,
                "plans": outcome.plan_ids,
            },
        )


def _failed_plan_finding(
    candidate: RaceCandidate,
    plan: AttackPlan,
    exc: StateBreakerError,
) -> Finding:
    return Finding(
        finding_id=f"finding-{plan.plan_id}",
        verdict="inconclusive",
        confidence=0.0,
        candidate=candidate,
        explanation=[f"plan execution failed: {exc}"],
    )
