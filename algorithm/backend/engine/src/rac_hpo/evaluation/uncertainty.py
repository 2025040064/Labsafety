"""Cluster resampling of actual independent units, never individual time slices."""
import numpy as np


def group_evidence(errors, groups, *, independent=False, minimum_groups=5, seed=42,
                   resamples=200):
    errors = np.asarray(errors, dtype=float)
    groups = np.asarray(groups).astype(str)
    records = []
    for group in sorted(set(groups)):
        values = errors[groups == group]
        records.append({"group_id": group, "samples": int(len(values)),
                        "absolute_error_sum": float(values.sum()), "mae": float(values.mean())})
    count = len(records)
    result = {"group_errors": records,
              "effective_independent_groups": count if independent else None,
              "mae_interval": None, "interval_method": None}
    if independent and count >= minimum_groups:
        rng = np.random.default_rng(seed)
        draws = rng.integers(0, count, size=(resamples, count))
        sums = np.array([item["absolute_error_sum"] for item in records])
        sizes = np.array([item["samples"] for item in records])
        distribution = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
        result.update(mae_interval=np.quantile(distribution, [0.025, 0.975]).tolist(),
                      interval_method="independent-unit percentile bootstrap; approximate, uncalibrated")
    return result
