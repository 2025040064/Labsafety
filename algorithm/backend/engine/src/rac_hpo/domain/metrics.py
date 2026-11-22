"""The one definition of the fixed, baseline-normalized robustness objective."""

from collections.abc import Mapping
import math
import numpy as np


def normalized_objective(values, weights, alpha):
    """The fixed J formula, vectorized along the last (regime) axis."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if values.ndim < 1 or values.shape[-1] != len(weights):
        raise ValueError("Objective regime axis must match fixed weights")
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Normalized errors must be finite and nonnegative")
    return (1 - alpha) * np.sum(values * weights, axis=-1) + alpha * np.max(values, axis=-1)


def robust_metrics(
    regime_metrics: Mapping[str, dict],
    baseline_errors: Mapping[str, float],
    alpha: float = 0.3,
    weights: Mapping[str, float] | None = None,
    epsilon: float = 1e-8,
) -> dict:
    """Compute J with a fixed regime set and explicit, normalized weights.

    When weights are omitted, validation sample proportions define p_g. This
    is descriptive weighting, not a claim that individual rows are independent.
    Zero baseline errors use the documented positive epsilon denominator.
    """
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("alpha must be finite and between 0 and 1")
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("epsilon must be positive and finite")
    regime_set = set(regime_metrics)
    if not regime_set or regime_set != set(baseline_errors):
        raise ValueError("baseline_errors must contain exactly the validation regimes")
    if weights is not None and set(weights) != regime_set:
        raise ValueError("weights must contain exactly the validation regimes")
    normalized_errors = {}
    raw_weights = {}
    near_zero = []
    for regime, metric in regime_metrics.items():
        baseline = float(baseline_errors[regime])
        error = float(metric["mae"])
        weight = float(weights[regime] if weights is not None else metric["samples"])
        if not math.isfinite(baseline) or baseline < 0:
            raise ValueError(f"baseline error must be nonnegative and finite: {regime}")
        if not math.isfinite(error) or error < 0:
            raise ValueError(f"regime MAE must be nonnegative and finite: {regime}")
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"regime weight must be nonnegative and finite: {regime}")
        ratio = error / (baseline + epsilon)
        if not math.isfinite(ratio):
            raise ValueError(f"normalized regime error overflowed: {regime}")
        normalized_errors[regime] = ratio
        raw_weights[regime] = weight
        if baseline <= epsilon:
            near_zero.append(regime)
    total = sum(raw_weights.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError("regime weights must have a positive finite sum")
    fixed_weights = {regime: value / total for regime, value in raw_weights.items()}
    worst = max(normalized_errors.values())
    objective = float(normalized_objective(list(normalized_errors.values()), list(fixed_weights.values()), alpha))
    return {
        "robust_objective": objective,
        "worst_normalized_mae": worst,
        "normalized_regime_mae": normalized_errors,
        "robustness": {
            "alpha": alpha, "epsilon": epsilon, "weights": fixed_weights,
            "weight_source": "explicit" if weights is not None else "validation_sample_proportions",
            "baseline_errors": dict(baseline_errors),
            "near_zero_baseline_regimes": near_zero,
        },
    }
