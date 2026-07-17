from __future__ import annotations

from statebreaker_learner_delta.proposers import (
    MaxDeltaProposer,
    MinValueProposer,
    StateTransitionProposer,
)
from statebreaker_learner_delta.sampling import NormalSample, ProbePair

PAIR = ProbePair(before_step="state-before", after_step="state-after")


def _numeric_samples(deltas: list[int], start: int = 0) -> list[NormalSample]:
    return [
        NormalSample(
            index=i,
            probes={
                "state-before": {"discount_yuan": start},
                "state-after": {"discount_yuan": start + delta},
            },
        )
        for i, delta in enumerate(deltas)
    ]


def test_max_delta_proposer_reports_observed_bound_and_confidence() -> None:
    samples = _numeric_samples([50, 50, 50, 48])

    invariant = MaxDeltaProposer().propose(key="$.discount_yuan", pair=PAIR, samples=samples)

    assert invariant is not None
    assert invariant.id == "learned-max-delta-discount-yuan"
    assert invariant.parameters["max_delta"] == 50
    assert invariant.parameters["bound_source"] == "observed_maximum"
    assert invariant.parameters["confidence"] == 0.75
    assert invariant.before_probe == "state-before"
    assert invariant.after_probe == "state-after"


def test_max_delta_proposer_skips_field_that_never_moves() -> None:
    samples = _numeric_samples([0, 0, 0, 0], start=50)

    assert MaxDeltaProposer().propose(key="$.discount_yuan", pair=PAIR, samples=samples) is None


def test_max_delta_proposer_requires_minimum_sample_support() -> None:
    samples = _numeric_samples([50, 50])

    assert MaxDeltaProposer().propose(key="$.discount_yuan", pair=PAIR, samples=samples) is None


def test_max_delta_proposer_ignores_boolean_fields() -> None:
    samples = [
        NormalSample(
            index=i,
            probes={
                "state-before": {"coupon_used": False},
                "state-after": {"coupon_used": True},
            },
        )
        for i in range(4)
    ]

    assert MaxDeltaProposer().propose(key="$.coupon_used", pair=PAIR, samples=samples) is None


def test_min_value_proposer_flags_zero_floor() -> None:
    samples = _numeric_samples([50, 50, 50])

    invariant = MinValueProposer().propose(key="$.discount_yuan", pair=PAIR, samples=samples)

    assert invariant is not None
    assert invariant.parameters["min_value"] == 0


def test_min_value_proposer_skips_nonzero_floor() -> None:
    samples = _numeric_samples([50, 50, 50], start=10)

    assert MinValueProposer().propose(key="$.discount_yuan", pair=PAIR, samples=samples) is None


def test_state_transition_proposer_reports_stable_toggle() -> None:
    samples = [
        NormalSample(
            index=i,
            probes={
                "state-before": {"coupon_used": False},
                "state-after": {"coupon_used": True},
            },
        )
        for i in range(5)
    ]

    invariant = StateTransitionProposer().propose(
        key="$.coupon_used", pair=PAIR, samples=samples
    )

    assert invariant is not None
    assert invariant.id == "learned-state-transition-coupon-used"
    assert invariant.parameters["from"] is False
    assert invariant.parameters["to"] is True
    assert invariant.parameters["confidence"] == 1.0


def test_state_transition_proposer_skips_field_that_does_not_change() -> None:
    samples = [
        NormalSample(
            index=i,
            probes={
                "state-before": {"coupon_code": "BUG50"},
                "state-after": {"coupon_code": "BUG50"},
            },
        )
        for i in range(4)
    ]

    assert (
        StateTransitionProposer().propose(key="$.coupon_code", pair=PAIR, samples=samples) is None
    )


def test_state_transition_proposer_skips_inconsistent_values() -> None:
    samples = [
        NormalSample(
            index=0,
            probes={"state-before": {"status": "a"}, "state-after": {"status": "b"}},
        ),
        NormalSample(
            index=1,
            probes={"state-before": {"status": "a"}, "state-after": {"status": "c"}},
        ),
        NormalSample(
            index=2,
            probes={"state-before": {"status": "a"}, "state-after": {"status": "b"}},
        ),
    ]

    assert StateTransitionProposer().propose(key="$.status", pair=PAIR, samples=samples) is None
