"""Threshold selection from strictly earlier, training-only time folds."""
import hashlib

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, balanced_accuracy_score, confusion_matrix
from sklearn.model_selection import TimeSeriesSplit

from rac_hpo.evaluation.classification import PHYSICAL_BEARING_KINDS


def _fingerprint(indices):
    return hashlib.sha256(np.asarray(indices, dtype="<i8").tobytes()).hexdigest()


def choose_time_oof_threshold(dataset, train_indices, fit_predict, n_splits=3):
    """Fit on past folds only; never request validation or test observations.

    ``fit_predict`` receives positional indices within the training partition.
    Identical timestamps stay together. Groups of physical bearings need a
    separate group-aware policy and are rejected rather than split as windows.
    """
    if isinstance(n_splits, bool) or not isinstance(n_splits, int) or not 2 <= n_splits <= 10:
        raise ValueError("time_oof needs between 2 and 10 folds")
    if dataset.manifest.get("independent_unit_kind") in PHYSICAL_BEARING_KINDS:
        raise ValueError("time_oof does not support physical bearing groups; group-aware validation is required")
    timestamps = dataset.manifest.get("timestamps")
    if timestamps is None or len(timestamps) != len(dataset.X):
        raise ValueError("time_oof requires an audited timestamp for each source row")
    strategy = str(dataset.manifest.get("split_strategy", "")).lower()
    if "time" not in strategy and "chronolog" not in strategy:
        raise ValueError("time_oof requires an explicitly chronological dataset split")
    row_indices = np.asarray(train_indices, dtype=int)
    labels = np.asarray(dataset.y.iloc[row_indices])
    group_ids = np.asarray(dataset.groups.iloc[row_indices].astype(str))
    classes = np.unique(labels)
    if len(classes) != 2:
        raise ValueError("time_oof requires a binary classification target")
    parsed = pd.to_datetime(np.asarray(timestamps)[row_indices], errors="raise", utc=True)
    if parsed.isna().any():
        raise ValueError("time_oof timestamps must not be missing")
    times = parsed.asi8
    unique_times = np.unique(times)
    if len(unique_times) <= n_splits:
        raise ValueError("time_oof has insufficient distinct training timestamps")
    records, all_labels, all_scores = [], [], []
    for number, (earlier, later) in enumerate(TimeSeriesSplit(n_splits=n_splits).split(unique_times)):
        past = np.flatnonzero(np.isin(times, unique_times[earlier]))
        future = np.flatnonzero(np.isin(times, unique_times[later]))
        if set(group_ids[past]) & set(group_ids[future]):
            raise ValueError(f"time_oof group overlap between past and future in fold {number}")
        if len(np.unique(labels[past])) != 2:
            raise ValueError(f"time_oof training fold {number} lacks a class")
        scores = np.asarray(fit_predict(past, future), dtype=float)
        if scores.shape != (len(future),) or not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any():
            raise ValueError("time_oof fold predictions must be finite probabilities")
        binary = (labels[future] == classes[1]).astype(int)
        all_labels.extend(binary.tolist())
        all_scores.extend(scores.tolist())
        records.append({"fold": number, "train_rows": len(past), "validation_rows": len(future),
                        "train_last_time": pd.Timestamp(unique_times[earlier][-1], unit="ns", tz="UTC").isoformat(),
                        "validation_first_time": pd.Timestamp(unique_times[later][0], unit="ns", tz="UTC").isoformat(),
                        "validation_positive_count": int(binary.sum()),
                        "train_indices_sha256": _fingerprint(row_indices[past]),
                        "validation_indices_sha256": _fingerprint(row_indices[future])})
    truth, probability = np.asarray(all_labels), np.asarray(all_scores)
    positive_count = int(truth.sum())
    negative_count = int(len(truth) - positive_count)
    if positive_count < 5 or negative_count < 5:
        raise ValueError("time_oof needs at least five positive and five negative held-out training observations")
    grid = np.linspace(.01, .5, 50)
    # Prefer the higher threshold on equal balanced accuracy, avoiding extra alarms.
    threshold = max(grid, key=lambda value: (balanced_accuracy_score(truth, probability >= value), value))
    predicted = (probability >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(truth, predicted, labels=[0, 1]).ravel()
    positive = classes[1].item() if isinstance(classes[1], np.generic) else classes[1]
    return {"mode": "time_oof", "threshold_source": "train_time_oof", "threshold": float(threshold),
            "positive_class": positive, "oof_folds": records, "oof_positive_count": positive_count,
            "oof_negative_count": negative_count, "oof_balanced_accuracy": float(balanced_accuracy_score(truth, predicted)),
            "oof_average_precision": float(average_precision_score(truth, probability)),
            "oof_positive_recall": float(tp / (tp + fn)), "oof_false_positive_rate": float(fp / (tn + fp)),
            "oof_positive_precision": float(tp / (tp + fp)) if tp + fp else None,
            "oof_confusion_matrix": [[int(tn), int(fp)], [int(fn), int(tp)]],
            "selection_rule": "maximize balanced accuracy on train-time OOF; ties prefer higher threshold",
            "threshold_grid": {"minimum": .01, "maximum": .5, "count": 50},
            "probability_calibration": "not calibrated", "test_used": False}
