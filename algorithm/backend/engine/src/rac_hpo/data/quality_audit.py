"""Descriptive flags only: extreme measurements remain available to training."""
import numpy as np


def audit_features(frame, train_indices, audit_indices, source_rows=None):
    """Fit IQR bounds on train; inspect only explicitly permitted partitions.

    No target is used, no rows are removed and no model features are appended.
    Constant/sparse columns cannot justify IQR-based extreme-value judgments.
    """
    train = frame.iloc[train_indices]
    observed = frame.iloc[audit_indices]
    mapping = list(range(len(frame))) if source_rows is None else source_rows
    features, flagged = {}, set()
    for column in frame.select_dtypes(include="number").columns:
        reference = train[column].to_numpy(dtype=float)
        reference = reference[np.isfinite(reference)]
        values = observed[column].to_numpy(dtype=float)
        invalid = ~np.isfinite(values)
        lower = upper = None
        extreme = np.zeros(len(values), dtype=bool)
        if len(reference) >= 4:
            q1, q3 = np.quantile(reference, [.25, .75])
            if q3 > q1:
                lower, upper = float(q1 - 1.5 * (q3 - q1)), float(q3 + 1.5 * (q3 - q1))
                extreme = np.isfinite(values) & ((values < lower) | (values > upper))
        rows = [int(mapping[audit_indices[index]]) for index in np.flatnonzero(invalid | extreme)]
        flagged.update(rows)
        features[str(column)] = {"lower": lower, "upper": upper,
                                 "nonfinite_count": int(invalid.sum()), "extreme_count": int(extreme.sum()),
                                 "flagged_source_rows": rows}
    return {"policy": "flag_only_preserve_rows", "fit_partition": "train",
            "method": "train_feature_IQR_1.5", "audited_rows": len(audit_indices),
            "flagged_source_rows": sorted(flagged), "features": features,
            "limitations": "Statistical flags are not confirmed faults; no deletion, clipping or automatic feature injection."}


def audit_dataset(dataset):
    return audit_features(dataset.X, dataset.splits["train"],
                          dataset.splits["train"] + dataset.splits["validation"],
                          dataset.manifest.get("source_row_indices"))
