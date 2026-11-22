"""Classification metrics, with fixed class identities and explicit group evidence."""
from collections.abc import Sequence

import numpy as np
from sklearn.metrics import average_precision_score, confusion_matrix, f1_score, log_loss


PHYSICAL_BEARING_KINDS = {"physical_bearings", "physical_bearing", "bearing", "bearings"}


def _scalar(value):
    return value.item() if isinstance(value, np.generic) else value


def _metrics(truth, predictions, probabilities, labels):
    matrix = confusion_matrix(truth, predictions, labels=labels)
    counts = matrix.sum(axis=1)
    predicted_counts = matrix.sum(axis=0)
    present = counts > 0
    balanced_accuracy = float(np.mean(np.diag(matrix)[present] / counts[present]))
    average_precision = (float(average_precision_score((truth == labels[1]).astype(int), probabilities[:, 1]))
                         if len(labels) == 2 and np.count_nonzero(present) == 2 else None)
    return {
        "balanced_accuracy": balanced_accuracy,
        "f1_macro": float(f1_score(truth, predictions, labels=labels, average="macro", zero_division=0)),
        "log_loss": float(log_loss(truth, probabilities, labels=labels)),
        "average_precision": average_precision,
        "error": 1.0 - balanced_accuracy,
        "samples": int(len(truth)),
        "class_counts": {str(_scalar(label)): int(count) for label, count in zip(labels, counts)},
        "class_recall": {str(_scalar(label)): float(matrix[index, index] / count) if count else None
                         for index, (label, count) in enumerate(zip(labels, counts))},
        "class_precision": {str(_scalar(label)): float(matrix[index, index] / count) if count else None
                            for index, (label, count) in enumerate(zip(labels, predicted_counts))},
        "class_false_negative_rate": {str(_scalar(label)): float(1 - matrix[index, index] / count) if count else None
                                      for index, (label, count) in enumerate(zip(labels, counts))},
        "class_false_positive_rate": {
            str(_scalar(label)): float((predicted_counts[index] - matrix[index, index]) / (len(truth) - count))
            if len(truth) > count else None for index, (label, count) in enumerate(zip(labels, counts))},
        "missing_classes": [_scalar(label) for label, count in zip(labels, counts) if count == 0],
        "minority_class_samples": int(counts.min()),
        "sparse_class_warning": "at least one fixed class has fewer than five samples; descriptive evaluation only" if (counts < 5).any() else None,
        "balanced_accuracy_scope": "classes observed in this evaluation subset",
        "confusion_matrix": matrix.tolist(),
    }


def evaluate(y_true, predictions, probabilities, regimes=None, groups=None, *,
             class_labels: Sequence | None = None, group_kind="unknown", minimum_groups=5,
             bootstrap_seed=42) -> dict:
    """Evaluate fixed train classes; missing validation classes remain visible.

    Group records preserve actual bearing identities. For RAC attention only,
    fixed per-class weights turn balanced error into a mean of weighted row
    errors. Resampling those group sums is an uncalibrated approximation and
    never treats windows or synthetic record IDs as independent bearings.
    """
    truth = np.asarray(y_true)
    predictions = np.asarray(predictions)
    probabilities = np.asarray(probabilities, dtype=float)
    labels = np.asarray(class_labels if class_labels is not None else np.unique(truth))
    if truth.ndim != 1 or not len(truth) or predictions.shape != truth.shape:
        raise ValueError("classification targets and predictions must be nonempty matching vectors")
    if len(labels) < 2 or len(set(labels.tolist())) != len(labels):
        raise ValueError("classification requires at least two distinct fixed classes")
    if not set(truth.tolist()) <= set(labels.tolist()) or not set(predictions.tolist()) <= set(labels.tolist()):
        raise ValueError("evaluation contains a class unseen in training")
    if probabilities.shape != (len(truth), len(labels)) or not np.isfinite(probabilities).all():
        raise ValueError("probabilities must be finite with one column per fixed training class")
    if (probabilities < 0).any() or (probabilities > 1).any() or not np.allclose(probabilities.sum(axis=1), 1):
        raise ValueError("each probability row must sum to one and lie in [0, 1]")
    # XGBoost emits float32 probabilities; preserve their probabilities while
    # removing sub-ulp row-sum drift before sklearn's float64 loss calculation.
    probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
    regimes = np.asarray(regimes if regimes is not None else ["all"] * len(truth)).astype(str)
    groups = np.asarray(groups if groups is not None else ["unknown"] * len(truth)).astype(str)
    if regimes.shape != truth.shape or groups.shape != truth.shape:
        raise ValueError("classification regimes and groups must match target length")
    overall = _metrics(truth, predictions, probabilities, labels)
    result = {f"overall_{key}": overall[key] for key in ("balanced_accuracy", "f1_macro", "log_loss", "average_precision", "error")}
    result.update(task_type="classification", class_labels=[_scalar(label) for label in labels],
                  samples=len(truth), class_counts=overall["class_counts"],
                  missing_classes=overall["missing_classes"], confusion_matrix=overall["confusion_matrix"],
                  class_recall=overall["class_recall"], class_precision=overall["class_precision"],
                  class_false_negative_rate=overall["class_false_negative_rate"],
                  class_false_positive_rate=overall["class_false_positive_rate"],
                  minority_class_samples=overall["minority_class_samples"], sparse_class_warning=overall["sparse_class_warning"],
                  balanced_accuracy_scope=overall["balanced_accuracy_scope"], regimes={})
    for regime in sorted(set(regimes)):
        mask = regimes == regime
        target, predicted = truth[mask], predictions[mask]
        metric = _metrics(target, predicted, probabilities[mask], labels)
        present_classes, class_counts = np.unique(target, return_counts=True)
        # The average of these fixed-class-weight row losses equals balanced error.
        error_weights = {label: len(target) / (len(present_classes) * count)
                         for label, count in zip(present_classes, class_counts)}
        row_errors = np.array([float(a != b) * error_weights[a] for a, b in zip(target, predicted)])
        group_ids = groups[mask]
        observed_groups = sorted(set(group_ids))
        independent = group_kind in PHYSICAL_BEARING_KINDS and "unknown" not in observed_groups
        records = []
        for group in observed_groups if independent else []:
            selected = group_ids == group
            group_truth, group_predictions = target[selected], predicted[selected]
            accuracy = np.mean([np.mean(group_predictions[group_truth == label] == label)
                                for label in np.unique(group_truth)])
            records.append({"group_id": group, "samples": int(selected.sum()),
                            "balanced_error": float(1 - accuracy),
                            "class_weighted_error_sum": float(row_errors[selected].sum())})
        metric.update(group_errors=records, group_kind=group_kind,
                      observed_group_count=len(observed_groups),
                      group_records_omitted_reason=None if independent else "groups are not verified independent physical bearings",
                      effective_independent_groups=len(records) if independent else None,
                      balanced_error_interval=None, interval_method=None,
                      independent_evidence_kind="physical bearings" if independent else "unverified or dependent groups")
        if independent and len(records) >= minimum_groups:
            rng = np.random.default_rng(bootstrap_seed)
            draws = rng.integers(0, len(records), size=(200, len(records)))
            sums = np.array([record["class_weighted_error_sum"] for record in records])
            sizes = np.array([record["samples"] for record in records])
            bootstrap = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
            metric.update(balanced_error_interval=np.quantile(bootstrap, [.025, .975]).tolist(),
                          interval_method="physical-bearing bootstrap of fixed-class-weight error; approximate, uncalibrated")
        result["regimes"][regime] = metric
    return result
