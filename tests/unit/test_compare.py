"""Reference tests for paired comparisons and comparison validity.

Reference values are computed independently:
  * mcnemar_exact(1, 9): two-sided exact binomial → 2·(C(10,0)+C(10,1))/2^10 = 22/1024.
  * mcnemar_exact(3, 3): 2·sum_{i=0..3} C(6,i)/2^6 = 2·42/64 = 1.3125 → capped to 1.0.
  * Paired bootstrap with a fixed seed is deterministic; a maximally separated
    pair (all-pass vs all-fail) yields the degenerate CI [1.0, 1.0] for every
    resample, which is an exact property, not a tolerance check.
"""

from __future__ import annotations

import pytest

from modelproof.statistics.compare import (
    COMPARISON_VERSION,
    compare_runs,
    comparison_compatibility,
    mcnemar_exact,
    paired_bootstrap_diff_ci,
    repeat_variability,
)


def test_mcnemar_reference_values():
    assert mcnemar_exact(1, 9) == pytest.approx(22 / 1024)
    assert mcnemar_exact(9, 1) == pytest.approx(22 / 1024)  # symmetric
    assert mcnemar_exact(3, 3) == 1.0  # capped at 1
    assert mcnemar_exact(0, 10) == pytest.approx(2 / 1024)
    assert mcnemar_exact(0, 0) is None  # no discordant pairs
    with pytest.raises(ValueError):
        mcnemar_exact(-1, 3)


def test_bootstrap_deterministic_and_extreme():
    a = [True] * 20
    b = [False] * 20
    r1 = paired_bootstrap_diff_ci(a, b, n_resamples=500, seed=7)
    r2 = paired_bootstrap_diff_ci(a, b, n_resamples=500, seed=7)
    assert r1 == r2  # same seed → identical result
    assert r1["ci"] == (1.0, 1.0)  # every resample of perfect separation is perfect
    r3 = paired_bootstrap_diff_ci(a, b, n_resamples=500, seed=8)
    assert r3["ci"] == (1.0, 1.0)  # separation property is seed-independent

    equal = paired_bootstrap_diff_ci(
        [True, False] * 10, [False, True] * 10, n_resamples=500, seed=1
    )
    assert equal["point_estimate"] == 0.0
    assert equal["ci"][0] <= 0.0 <= equal["ci"][1] or equal["ci"][0] == equal["ci"][1] == 0.0

    empty = paired_bootstrap_diff_ci([], [])
    assert empty["ci"] is None and empty["note"]


def _cfg(**over):
    base = {
        "suite_id": "python-core",
        "suite_version": "1.0.0",
        "suite_content_hash": "abc123",
        "execution_mode": "fixture_replay",
        "prompt_template_version": "v1",
        "generation": {"temperature": 0.2, "max_tokens": 1024},
        "task_ids": ["t1", "t2", "t3"],
        "is_fixture": True,
    }
    base.update(over)
    return base


def test_compatibility_refuses_protocol_mismatch():
    a, b = _cfg(), _cfg(generation={"temperature": 0.7, "max_tokens": 1024})
    result = comparison_compatibility(a, b)
    assert not result["compatible"]
    assert any("generation.temperature" in i for i in result["incompatibilities"])

    result2 = comparison_compatibility(a, _cfg(suite_content_hash="different"))
    assert not result2["compatible"]

    same = comparison_compatibility(_cfg(), _cfg())
    assert same["compatible"] and not same["incompatibilities"]


def test_compare_runs_refuses_incompatible():
    out = compare_runs(
        run_a_id="a",
        run_b_id="b",
        config_a=_cfg(),
        config_b=_cfg(execution_mode="sandbox"),
        cases_a={"t1": "PASSED"},
        cases_b={"t1": "FAILED"},
    )
    assert out["compatible"] is False
    assert "refusal" in out and out.get("mcnemar_exact_p") is None


def test_compare_runs_paired_statistics():
    shared = [f"t{i}" for i in range(20)]
    cases_a = {t: ("PASSED" if i < 14 else "FAILED") for i, t in enumerate(shared)}
    # B passes 6 of the 14 A-passes, plus 3 A-failures → 9 passes, discordant (8, 3)
    b_pass_ids = set(shared[:6]) | {shared[14], shared[15], shared[16]}
    cases_b = {t: ("PASSED" if t in b_pass_ids else "FAILED") for t in shared}
    out = compare_runs(
        run_a_id="a",
        run_b_id="b",
        config_a=_cfg(),
        config_b=_cfg(),
        cases_a=cases_a,
        cases_b=cases_b,
        n_resamples=300,
        seed=3,
    )
    assert out["compatible"] and out["comparison_version"] == COMPARISON_VERSION
    assert out["n_paired_tasks"] == 20
    assert out["discordant"] == {"a_passed_b_failed": 8, "a_failed_b_passed": 3}
    assert out["pass_rate_a"] == pytest.approx(0.7)
    assert out["pass_rate_b"] == pytest.approx(0.45)
    assert out["mcnemar_exact_p"] == pytest.approx(mcnemar_exact(8, 3))
    assert len(out["caveats"]) >= 3


def test_compare_runs_excludes_missing_outcomes_and_discloses():
    cases_a = {"t1": "PASSED", "t2": "FAILED", "t3": "SANDBOX_ERROR"}
    cases_b = {"t1": "FAILED", "t2": "FAILED", "t3": "PASSED"}
    out = compare_runs(
        run_a_id="a",
        run_b_id="b",
        config_a=_cfg(),
        config_b=_cfg(),
        cases_a=cases_a,
        cases_b=cases_b,
    )
    assert out["n_paired_tasks"] == 2
    assert out["n_excluded_tasks"] == 1
    assert any("lacked a valid outcome" in c for c in out["caveats"])


def test_repeat_variability_honesty():
    rep = repeat_variability([20, 18, 19], 20)
    assert rep["n_repeats"] == 3
    assert rep["mean_pass_rate"] == pytest.approx((1.0 + 0.9 + 0.95) / 3)
    assert "NOT sampling error" in rep["note"] or "not independent" in rep["note"].lower()
    single = repeat_variability([5], 10)
    assert "stdev_pass_rate" not in single
    empty = repeat_variability([], 10)
    assert empty == {"n_repeats": 0}
