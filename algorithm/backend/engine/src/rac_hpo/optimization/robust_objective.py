"""Fixed-objective adaptation, using the single domain metric definition."""

from rac_hpo.domain.metrics import robust_metrics


def apply_robust_metrics(metrics: dict, baseline_errors: dict, alpha=0.3, weights=None, epsilon=1e-8):
    """Return a new top-level metrics mapping; never modify the caller's values."""
    return {
        **metrics,
        **robust_metrics(metrics["regimes"], baseline_errors, alpha, weights, epsilon),
    }
