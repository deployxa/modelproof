"""Item Response Theory (IRT) difficulty and discrimination calibration (Tier 2).

Implements 1PL (Rasch) and 2PL estimation for task calibration against reference models.
Tasks solved by all or no reference models provide zero information; IRT difficulty scores
and discrimination parameters formalize benchmark measurability.
"""

from __future__ import annotations

import math
from typing import Any


def sigmoid(x: float) -> float:
    if x > 35:
        return 1.0
    if x < -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def fit_rasch_model(
    task_model_matrix: dict[str, dict[str, int]],
    max_iters: int = 100,
    tol: float = 1e-4,
) -> dict[str, Any]:
    """Fit 1PL (Rasch) IRT model: P(Y_ij = 1) = sigmoid(theta_j - beta_i).

    task_model_matrix: task_id -> {model_id -> 1 (pass) or 0 (fail)}.

    Returns:
      - task_difficulties: task_id -> beta_i (higher = harder)
      - model_abilities: model_id -> theta_j (higher = more capable)
      - task_diagnostics: informative vs zero-signal tasks
    """
    task_ids = sorted(task_model_matrix.keys())
    if not task_ids:
        return {"task_difficulties": {}, "model_abilities": {}, "task_diagnostics": {}}

    model_ids = sorted(list(task_model_matrix[task_ids[0]].keys()))
    if not model_ids:
        return {"task_difficulties": {}, "model_abilities": {}, "task_diagnostics": {}}

    n_tasks = len(task_ids)
    n_models = len(model_ids)

    # Calculate empirical solve rates
    solve_rates = {}
    for tid in task_ids:
        passes = sum(task_model_matrix[tid].get(mid, 0) for mid in model_ids)
        solve_rates[tid] = passes / n_models

    # Initial estimates
    # Beta ~ log((1 - p) / p), Theta ~ log(p / (1 - p))
    betas = {
        tid: -math.log(
            max(1e-3, min(1 - 1e-3, solve_rates[tid]))
            / (1 - max(1e-3, min(1 - 1e-3, solve_rates[tid])))
        )
        for tid in task_ids
    }
    thetas = {mid: 0.0 for mid in model_ids}

    # Alternate coordinate descent updates
    lr = 0.5
    for _ in range(max_iters):
        max_diff = 0.0

        # Update thetas
        for mid in model_ids:
            obs = sum(task_model_matrix[tid].get(mid, 0) for tid in task_ids)
            exp = sum(sigmoid(thetas[mid] - betas[tid]) for tid in task_ids)
            grad = obs - exp
            new_theta = thetas[mid] + lr * (grad / max(1.0, n_tasks))
            max_diff = max(max_diff, abs(new_theta - thetas[mid]))
            thetas[mid] = new_theta

        # Update betas
        for tid in task_ids:
            obs = sum(task_model_matrix[tid].get(mid, 0) for mid in model_ids)
            exp = sum(sigmoid(thetas[mid] - betas[tid]) for mid in model_ids)
            grad = exp - obs  # higher beta -> lower exp
            new_beta = betas[tid] + lr * (grad / max(1.0, n_models))
            max_diff = max(max_diff, abs(new_beta - betas[tid]))
            betas[tid] = new_beta

        # Mean-center betas for identifiability
        mean_beta = sum(betas.values()) / n_tasks
        for tid in task_ids:
            betas[tid] -= mean_beta
        for mid in model_ids:
            thetas[mid] -= mean_beta

        if max_diff < tol:
            break

    # Diagnostics
    diagnostics = {}
    for tid in task_ids:
        sr = solve_rates[tid]
        is_uninformative = sr == 0.0 or sr == 1.0
        diagnostics[tid] = {
            "solve_rate": sr,
            "difficulty": round(betas[tid], 4),
            "signal": "zero_signal" if is_uninformative else "informative",
            "tier": "easy" if sr >= 0.75 else ("hard" if sr <= 0.25 else "medium"),
        }

    return {
        "task_difficulties": {k: round(v, 4) for k, v in betas.items()},
        "model_abilities": {k: round(v, 4) for k, v in thetas.items()},
        "task_diagnostics": diagnostics,
    }
