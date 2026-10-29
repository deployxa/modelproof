"""Statistics correctness (Gate F) — pinned against independently computed
reference values and an alternative in-test implementation of the Wilson
interval."""

from __future__ import annotations

import math

import pytest

from modelproof.errors import InvalidInputError
from modelproof.statistics.stats import Z_95, aggregate, completion_rate, pass_rate, wilson_interval


def wilson_reference_implementation(k: int, n: int, z: float = Z_95):
    """Independent two-line implementation (agresti-style algebra) used ONLY
    for cross-checking; derived separately from the engine's formula."""
    if n == 0:
        return None
    phat = k / n
    denom = 1 + z**2 / n
    center = (phat + z**2 / (2 * n)) / denom
    spread = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return center - spread, center + spread


class TestWilsonReferenceValues:
    """Reference values computed independently (pocket-calculator algebra)."""

    @pytest.mark.parametrize(
        "k,n", [(0, 10), (5, 10), (10, 10), (1, 1), (3, 7), (81, 263), (0, 24), (18, 24)]
    )
    def test_matches_reference_implementation(self, k, n):
        got = wilson_interval(k, n - k)
        want = wilson_reference_implementation(k, n)
        assert got is not None and want is not None
        assert got[0] == pytest.approx(want[0], abs=1e-12)
        assert got[1] == pytest.approx(want[1], abs=1e-12)

    def test_zero_successes_of_ten(self):
        lo, hi = wilson_interval(0, 10)
        assert lo == 0.0
        assert hi == pytest.approx(0.2775, abs=5e-4)

    def test_five_of_ten(self):
        lo, hi = wilson_interval(5, 5)
        assert lo == pytest.approx(0.2366, abs=5e-4)
        assert hi == pytest.approx(0.7634, abs=5e-4)

    def test_all_successes_of_ten(self):
        lo, hi = wilson_interval(10, 0)
        assert lo == pytest.approx(0.7225, abs=5e-4)
        assert hi == 1.0

    def test_one_of_one(self):
        lo, hi = wilson_interval(1, 0)
        assert lo == pytest.approx(0.2065, abs=2e-3)
        assert hi == 1.0

    def test_intermediate(self):
        lo, hi = wilson_interval(18, 6)  # 18/24
        assert lo == pytest.approx(0.5510, abs=5e-4)
        assert hi == pytest.approx(0.8800, abs=5e-4)

    def test_empty_denominator_returns_none(self):
        assert wilson_interval(0, 0) is None


class TestWilsonInvalidInputs:
    def test_negative_successes(self):
        with pytest.raises(InvalidInputError):
            wilson_interval(-1, 5)

    def test_negative_failures(self):
        with pytest.raises(InvalidInputError):
            wilson_interval(5, -1)


class TestAggregation:
    def _cats(self, outcomes, default="bug_fix"):
        return {t: default for t in outcomes}

    def test_counts_and_rates(self):
        outcomes = {
            f"t{i}": s
            for i, s in enumerate(
                ["PASSED"] * 6
                + ["FAILED"] * 2
                + ["TIMEOUT", "INVALID_OUTPUT", "MODEL_ERROR", "SANDBOX_ERROR"]
            )
        }
        agg = aggregate(outcomes, self._cats(outcomes))
        # valid outcomes exclude the infrastructure (SANDBOX_ERROR) case
        assert agg["pass_rate_numerator"] == 6
        assert agg["pass_rate_denominator"] == 11
        assert agg["pass_rate"] == pytest.approx(6 / 11)
        assert agg["completion_rate"] == pytest.approx(1.0)
        assert agg["sandbox_errors"] == 1
        assert agg["wilson95"] == wilson_interval(6, 5)

    def test_infrastructure_failures_never_inflate_scores(self):
        outcomes = {"a": "SANDBOX_ERROR", "b": "SANDBOX_ERROR", "c": "PASSED"}
        agg = aggregate(outcomes, self._cats(outcomes))
        assert agg["pass_rate"] == pytest.approx(1.0)  # 1/1 valid
        assert agg["completion_rate"] == pytest.approx(1.0)  # all terminal
        assert agg["sandbox_errors"] == 2

    def test_category_rates(self):
        outcomes = {"b1": "PASSED", "b2": "FAILED", "f1": "PASSED", "f2": "PASSED"}
        cats = {"b1": "bug_fix", "b2": "bug_fix", "f1": "function_impl", "f2": "function_impl"}
        agg = aggregate(outcomes, cats)
        assert agg["category_pass_rates"]["bug_fix"] == pytest.approx(0.5)
        assert agg["category_pass_rates"]["function_impl"] == pytest.approx(1.0)

    def test_zero_valid_outcomes_gives_no_estimate(self):
        agg = aggregate({"a": "SANDBOX_ERROR"}, {"a": "bug_fix"})
        assert agg["pass_rate"] is None
        assert agg["wilson95"] is None

    def test_timeouts_count_as_model_attributable_failures(self):
        agg = aggregate({"a": "TIMEOUT"}, {"a": "edge_case"})
        assert agg["pass_rate"] == 0.0
        assert agg["pass_rate_denominator"] == 1


class TestRateHelpers:
    def test_pass_rate_none_on_empty(self):
        assert pass_rate(0, 0) is None

    def test_pass_rate_bounds(self):
        with pytest.raises(InvalidInputError):
            pass_rate(3, 2)
        with pytest.raises(InvalidInputError):
            pass_rate(-1, 2)

    def test_completion_rate(self):
        assert completion_rate(24, 24) == 1.0
        assert completion_rate(0, 24) == 0.0
        with pytest.raises(InvalidInputError):
            completion_rate(25, 24)
