"""Search minimization: shrink confirmed attacks to their simplest form.

Every function takes an async ``triggers`` predicate so the search logic is
testable without a live target; the scanner wires the predicate to real
trials through the experiment controller.
"""

from statebreaker.minimization.concurrency import minimize_concurrency, trim_plan
from statebreaker.minimization.schedule import SIMPLICITY_ORDER, simplest_scheduler
from statebreaker.minimization.statistics import TrialSignal, measure_run_statistics
from statebreaker.minimization.workflow import ddmin, minimize_setup_steps

__all__ = [
    "SIMPLICITY_ORDER",
    "TrialSignal",
    "ddmin",
    "measure_run_statistics",
    "minimize_concurrency",
    "minimize_setup_steps",
    "simplest_scheduler",
    "trim_plan",
]
