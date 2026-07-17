from __future__ import annotations

from statebreaker_learner_delta.profiling import build_state_profile, flatten
from statebreaker_learner_delta.sampling import NormalSample


def test_flatten_produces_jsonpath_style_keys() -> None:
    assert flatten({"discount_yuan": 50, "nested": {"id": "abc"}}) == {
        "$.discount_yuan": 50,
        "$.nested.id": "abc",
    }


def test_flatten_handles_lists_and_empty_containers() -> None:
    assert flatten({"items": [{"x": 1}, {"x": 2}]}) == {
        "$.items[0].x": 1,
        "$.items[1].x": 2,
    }
    assert flatten({"empty_list": [], "empty_dict": {}}) == {
        "$.empty_list": [],
        "$.empty_dict": {},
    }


def _sample(index: int, run_id: str, discount_before: int, discount_after: int) -> NormalSample:
    return NormalSample(
        index=index,
        probes={
            "state-before": {
                "run_id": run_id,
                "coupon_code": "BUG50",
                "discount_yuan": discount_before,
                "coupon_used": False,
                "created_at": f"2024-01-01T00:00:{index:02d}+00:00",
            },
            "state-after": {
                "run_id": run_id,
                "coupon_code": "BUG50",
                "discount_yuan": discount_after,
                "coupon_used": True,
                "created_at": f"2024-01-01T00:00:{index:02d}+00:00",
            },
        },
    )


def test_build_state_profile_classifies_fields_correctly() -> None:
    samples = [
        _sample(i, run_id=f"run-{i}", discount_before=0, discount_after=50) for i in range(5)
    ]

    profile = build_state_profile("coupon-race-demo", samples)

    assert "$.discount_yuan" in profile.stable_fields
    assert "$.coupon_used" in profile.stable_fields
    assert "$.coupon_code" in profile.stable_fields
    assert "$.run_id" in profile.ignored_fields
    assert "$.created_at" in profile.ignored_fields
    assert len(profile.samples) == 5


def test_build_state_profile_does_not_confuse_shared_run_id_with_stability() -> None:
    # Regression guard: before/after share the same run_id within a round, which
    # must not make run_id look stable when pooled naively across probes.
    samples = [
        _sample(i, run_id=f"run-{i}", discount_before=0, discount_after=50) for i in range(8)
    ]

    profile = build_state_profile("coupon-race-demo", samples)

    assert "$.run_id" in profile.ignored_fields
    assert "$.run_id" not in profile.stable_fields
