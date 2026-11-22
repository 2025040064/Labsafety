"""Bounded search attention, gated by genuine independent-unit evidence."""
import numpy as np


def regime_priorities(metrics, baseline_errors, weights, *, minimum_groups=5, shrinkage_k=5.0,
                      threshold=1.0, q_max=0.5, epsilon=1e-8, seed=42):
    regimes = sorted(baseline_errors)
    ratios = {regime: metrics["regimes"][regime]["mae"] / (baseline_errors[regime] + epsilon)
              for regime in regimes}
    reference = sum(weights[regime] * ratios[regime] for regime in regimes) / sum(weights.values())
    result = {}
    for regime in regimes:
        metric = metrics["regimes"][regime]
        count = metric.get("effective_independent_groups")
        records = metric.get("group_errors", [])
        if count is None or count < minimum_groups or len(records) != count:
            result[regime] = {"q": 0.0, "reason": "insufficient_independent_unit_evidence",
                              "effective_groups": count, "raw_ratio": ratios[regime]}
            continue
        rng = np.random.default_rng(seed)
        sums = np.array([record["absolute_error_sum"] for record in records])
        sizes = np.array([record["samples"] for record in records])
        sampled = rng.integers(0, count, size=(200, count))
        bootstrap_ratios = (sums[sampled].sum(axis=1) / sizes[sampled].sum(axis=1)) / (baseline_errors[regime] + epsilon)
        probability = float(np.mean(bootstrap_ratios > threshold))
        shrinkage = count / (count + shrinkage_k)
        shrunk_ratio = shrinkage * ratios[regime] + (1 - shrinkage) * reference
        result[regime] = {"q": float(np.clip(max(0.0, shrunk_ratio - threshold) * probability, 0, q_max)),
                          "reason": "independent_unit_bootstrap_approximation", "effective_groups": count,
                          "raw_ratio": ratios[regime], "search_shrunk_ratio": float(shrunk_ratio),
                          "exceedance_probability": probability, "shrinkage": shrinkage}
    return result
