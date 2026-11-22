"""Overall and regime-level validation metrics."""

import numpy as np
import pandas as pd

from rac_hpo.domain.metrics import robust_metrics
from rac_hpo.evaluation.uncertainty import group_evidence


def evaluate(
    y_true,
    predictions,
    regimes,
    groups,
    baseline_errors: dict | None = None,
    alpha: float = 0.3,
    weights: dict | None = None,
    epsilon: float = 1e-8,
    *,
    group_kind: str = "unknown",
    minimum_groups: int = 5,
    bootstrap_seed: int = 42,
) -> dict:
    truth = np.asarray(y_true, dtype=float)
    predicted = np.asarray(predictions, dtype=float)
    regime_values = np.asarray(regimes)
    group_values = np.asarray(groups)
    arrays = (truth, predicted, regime_values, group_values)
    if any(array.ndim != 1 for array in arrays):
        raise ValueError("truth, predictions, regimes and groups must be one-dimensional")
    if not len(truth) or any(len(array) != len(truth) for array in arrays):
        raise ValueError("evaluation inputs must be nonempty and have equal lengths")
    if not np.isfinite(truth).all() or not np.isfinite(predicted).all():
        raise ValueError("truth and predictions must be finite")
    if pd.isna(regime_values).any() or pd.isna(group_values).any():
        raise ValueError("regimes and independent groups must not contain missing values")
    labels = regime_values.astype(str)
    if any(not label.strip() for label in labels):
        raise ValueError("regime labels must not be empty")
    errors = np.abs(truth - predicted)
    regime_metrics = {}
    for regime in sorted(set(labels)):
        mask = labels == regime
        independent_groups = int(len(set(group_values[mask])))
        regime_metrics[regime] = {
            "mae": float(errors[mask].mean()),
            "samples": int(mask.sum()),
            "independent_groups": independent_groups,
            "reliability": (
                "descriptive_only" if independent_groups >= 5
                else "insufficient_independent_groups"
            ),
            "independent_unit_kind": group_kind,
        }
        evidence = group_evidence(errors[mask], group_values[mask],
                                  independent=group_kind in {"physical_tool", "independent_run"},
                                  minimum_groups=minimum_groups, seed=bootstrap_seed)
        regime_metrics[regime].update(evidence)
        if group_kind in {"physical_tool", "independent_run"}:
            regime_metrics[regime]["reliability"] = (
                "limited_group_bootstrap" if independent_groups >= minimum_groups
                else "insufficient_independent_groups"
            )
    metrics = {
        "overall_mae": float(errors.mean()),
        "regimes": regime_metrics,
        "robust_objective": None,
        "worst_normalized_mae": None,
    }
    if baseline_errors is not None:
        metrics.update(robust_metrics(regime_metrics, baseline_errors, alpha, weights, epsilon))
    return metrics
