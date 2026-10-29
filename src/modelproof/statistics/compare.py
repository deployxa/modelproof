"""Paired model comparisons and comparison validity (mandate §8.2–8.4).

Everything in this module is computed from *recorded run evidence* — it never
calls a model. Three responsibilities:

1. Paired comparison procedures for models evaluated on the SAME tasks
   (exact McNemar test on discordant pairs, paired bootstrap CI for the
   difference in pass rates). Ties are handled by definition: tasks both
   models failed are concordant failures and carry no McNemar weight; tasks
   with missing outcomes on either side are excluded from the paired
   analysis and the exclusion count is disclosed.

2. Comparison compatibility checking — two runs may only be compared when
   their protocols match (same suite content, same generation parameters,
   same execution mode, same engine/scoring versions). Incompatible runs
   produce a refusal with reasons, never a ranking.

3. Repeat-run variability — repeated runs on the same task set are NOT
   independent observations. Reported statistics say so.

Statistical caveats are part of the output, not documentation:
statistical significance is not practical superiority, multiple comparisons
inflate false-positive risk, and percentile bootstrap CIs carry the usual
resampling assumptions.

Reference values are pinned in tests/unit/test_compare.py.
"""

from __future__ import annotations

import math
import random
from statistics import mean, stdev

# Version of the comparison calculations — recorded in every comparison payload
# so results computed by different engine versions remain distinguishable.
COMPARISON_VERSION = "compare/1"


# --------------------------------------------------------------------- McNemar
def _binom_pmf_ge_ceil(n: int, k: int) -> float:
    """P(X >= k) for X ~ Bin(n, 0.5), exact via symmetric CDF."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    total = 0.0
    for i in range(k, n + 1):
        total += math.comb(n, i)
    return total / (2**n)


def mcnemar_exact(b: int, c: int) -> float | None:
    """Exact two-sided McNemar p-value from discordant pairs.

    b = tasks where model A passed and model B failed;
    c = tasks where model A failed and model B passed.
    Under the null hypothesis of equal paired pass probabilities, the sign of
    each discordant pair is a fair coin flip, so p = 2·P(X ≤ min(b,c)) with
    X ~ Bin(b+c, 0.5), capped at 1.0.

    Returns None when there are no discordant pairs (no evidence of a
    difference — which is NOT evidence of equivalence).
    """
    if b < 0 or c < 0:
        raise ValueError("b and c must be non-negative")
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    p = 2.0 * sum(math.comb(n, i) for i in range(0, k + 1)) / (2**n)
    return min(1.0, p)


# ------------------------------------------------------------------- bootstrap
def paired_bootstrap_diff_ci(
    a_outcomes: list[bool],
    b_outcomes: list[bool],
    *,
    n_resamples: int = 2000,
    seed: int = 0,
    level: float = 0.95,
) -> dict:
    """Percentile bootstrap CI for (pass_rate_a − pass_rate_b), paired.

    Resamples task indices WITH replacement (preserving the pairing), so the
    procedure respects the shared-task design. Deterministic for a fixed seed.
    Missing outcomes must be dropped by the caller before calling; the caller
    discloses the drop count.
    """
    if len(a_outcomes) != len(b_outcomes):
        raise ValueError("paired outcome lists must have equal length")
    if len(a_outcomes) == 0:
        return {"ci": None, "note": "no paired tasks", "n": 0}
    if not (0 < level < 1):
        raise ValueError("level must be in (0, 1)")
    n = len(a_outcomes)
    diffs: list[float] = []
    rng = random.Random(seed)
    for _ in range(n_resamples):
        pa = pb = 0
        for _ in range(n):
            i = rng.randrange(n)
            pa += 1 if a_outcomes[i] else 0
            pb += 1 if b_outcomes[i] else 0
        diffs.append(pa / n - pb / n)
    diffs.sort()
    alpha = (1.0 - level) / 2.0
    lo_idx = max(0, int(math.floor(alpha * n_resamples)))
    hi_idx = min(n_resamples - 1, int(math.ceil((1.0 - alpha) * n_resamples)) - 1)
    point = sum(1 for x, y in zip(a_outcomes, b_outcomes) if x and not y)
    point_b = sum(1 for x, y in zip(a_outcomes, b_outcomes) if y and not x)
    ci = (diffs[lo_idx], diffs[hi_idx])
    return {
        "ci": ci,
        "point_estimate": (point - point_b) / n,
        "level": level,
        "n_resamples": n_resamples,
        "n_tasks": n,
        "seed": seed,
        "note": "percentile bootstrap over shared tasks; resampling preserves pairing",
    }


# ------------------------------------------------------------- compatibility
_COMPAT_FIELDS = (
    "suite_id",
    "suite_version",
    "suite_content_hash",
    "execution_mode",
    "prompt_template_version",
)


def comparison_compatibility(
    config_a: dict, config_b: dict, *, summary_a: dict | None = None, summary_b: dict | None = None
) -> dict:
    """Decide whether two runs are protocol-compatible for comparison (§8.3).

    Checked: suite identity + version + CONTENT hash, generation settings,
    execution mode, prompt template version, engine/scoring version.
    Returns {"compatible": bool, "incompatibilities": [...], "warnings": [...]}.
    A single content-hash or suite-version mismatch is a hard refusal.
    """
    incompat: list[str] = []
    warnings: list[str] = []
    for field in _COMPAT_FIELDS:
        va, vb = config_a.get(field), config_b.get(field)
        if va != vb:
            incompat.append(f"{field}: {va!r} != {vb!r}")
    ga, gb = config_a.get("generation") or {}, config_b.get("generation") or {}
    for key in sorted(set(ga) | set(gb)):
        if ga.get(key) != gb.get(key):
            incompat.append(f"generation.{key}: {ga.get(key)!r} != {gb.get(key)!r}")
    if summary_a and summary_b:
        ev_a = (summary_a.get("environment") or {}).get("modelproof_version")
        ev_b = (summary_b.get("environment") or {}).get("modelproof_version")
        if ev_a and ev_b and ev_a != ev_b:
            warnings.append(
                f"engine versions differ ({ev_a} vs {ev_b}); scoring/statistics code "
                "may differ — recorded per run for exactly this reason"
            )
    if config_a.get("is_fixture") or config_b.get("is_fixture"):
        warnings.append(
            "fixture runs are replays; a comparison involving a fixture run supports no claim about any model"
        )
    if config_a.get("task_ids") is not None and config_b.get("task_ids") is not None:
        shared = set(config_a["task_ids"]) & set(config_b["task_ids"])
        only_a = set(config_a["task_ids"]) - shared
        only_b = set(config_b["task_ids"]) - shared
        if only_a or only_b:
            warnings.append(
                f"task sets differ: {len(only_a)} task(s) only in A, {len(only_b)} only in B; "
                f"paired analysis restricted to {len(shared)} shared tasks"
            )
    return {"compatible": not incompat, "incompatibilities": incompat, "warnings": warnings}


# ---------------------------------------------------------------- repeats
def repeat_variability(per_repeat_passed: list[int], n_tasks: int) -> dict:
    """Summarize variability across repeat runs of the SAME tasks (§8.1).

    Repeats share their task set, so they are not independent samples; the
    spread shown here is protocol stability, not sampling uncertainty.
    """
    if not per_repeat_passed:
        return {"n_repeats": 0}
    rates = [p / n_tasks for p in per_repeat_passed]
    out: dict = {
        "n_repeats": len(rates),
        "n_tasks": n_tasks,
        "pass_rates": rates,
        "mean_pass_rate": mean(rates),
        "min_pass_rate": min(rates),
        "max_pass_rate": max(rates),
        "note": "repeats share the same tasks; variability reflects decoding "
        "non-determinism and protocol stability, NOT sampling error over new tasks",
    }
    if len(rates) > 1:
        out["stdev_pass_rate"] = stdev(rates)
    return out


# --------------------------------------------------------------- full compare
def compare_runs(
    *,
    run_a_id: str,
    run_b_id: str,
    config_a: dict,
    config_b: dict,
    cases_a: dict[str, str],
    cases_b: dict[str, str],
    summary_a: dict | None = None,
    summary_b: dict | None = None,
    n_resamples: int = 2000,
    seed: int = 0,
) -> dict:
    """Full paired comparison payload with validity gating and caveats.

    cases_* map task_id -> CaseStatus value. Tasks missing a terminal outcome
    on either side are excluded from the paired analysis and counted.
    """
    compat = comparison_compatibility(config_a, config_b, summary_a=summary_a, summary_b=summary_b)
    if not compat["compatible"]:
        return {
            "comparison_version": COMPARISON_VERSION,
            "run_a": run_a_id,
            "run_b": run_b_id,
            "compatible": False,
            "refusal": "runs are not protocol-compatible; no comparison computed",
            **compat,
        }

    valid = {"PASSED", "FAILED", "TIMEOUT", "INVALID_OUTPUT", "MODEL_ERROR"}
    shared = sorted(set(cases_a) & set(cases_b))
    paired = [t for t in shared if cases_a[t] in valid and cases_b[t] in valid]
    dropped = len(shared) - len(paired)

    a_pass = {t: cases_a[t] == "PASSED" for t in paired}
    b_pass = {t: cases_b[t] == "PASSED" for t in paired}
    both_pass = sum(1 for t in paired if a_pass[t] and b_pass[t])
    both_fail = sum(1 for t in paired if not a_pass[t] and not b_pass[t])
    a_only = [t for t in paired if a_pass[t] and not b_pass[t]]
    b_only = [t for t in paired if b_pass[t] and not a_pass[t]]

    p_value = mcnemar_exact(len(a_only), len(b_only))
    boot = paired_bootstrap_diff_ci(
        [a_pass[t] for t in paired],
        [b_pass[t] for t in paired],
        n_resamples=n_resamples,
        seed=seed,
    )
    rate_a = (sum(1 for t in paired if a_pass[t]) / len(paired)) if paired else None
    rate_b = (sum(1 for t in paired if b_pass[t]) / len(paired)) if paired else None

    caveats = [
        "statistical significance does not establish practical superiority; "
        "effect size and interval width matter",
        "paired analysis covers shared tasks only — it does not generalize to "
        "other task distributions",
        "repeated comparisons across many model pairs inflate false-positive "
        "risk (multiple comparisons); no correction is applied here — interpret "
        "p-values accordingly",
        "neither run's suite provenance is certified as independent of any model's training corpus",
    ]
    if dropped:
        caveats.append(
            f"{dropped} shared task(s) lacked a valid outcome on one side and were excluded"
        )

    return {
        "comparison_version": COMPARISON_VERSION,
        "run_a": run_a_id,
        "run_b": run_b_id,
        "compatible": True,
        "compatibility": compat,
        "n_paired_tasks": len(paired),
        "n_excluded_tasks": dropped,
        "pass_rate_a": rate_a,
        "pass_rate_b": rate_b,
        "both_passed": both_pass,
        "both_failed": both_fail,
        "a_only_passed": sorted(a_only),
        "b_only_passed": sorted(b_only),
        "discordant": {"a_passed_b_failed": len(a_only), "a_failed_b_passed": len(b_only)},
        "mcnemar_exact_p": p_value,
        "bootstrap": boot,
        "caveats": caveats,
    }
