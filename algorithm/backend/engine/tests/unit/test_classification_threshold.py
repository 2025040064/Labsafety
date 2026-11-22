"""Time-ordered decision thresholds must never learn from validation or test."""
from copy import deepcopy

import joblib
import numpy as np
import pandas as pd
import pytest

from rac_hpo.domain.schemas import PreparedDataset
from rac_hpo.training.classification import train
from rac_hpo.training.threshold import choose_time_oof_threshold


def temporal_fixture():
    count = 100
    labels = pd.Series([0, 1, 0, 0, 1] * 20, name="failure")
    frame = pd.DataFrame({"sensor": np.sin(np.arange(count)) + labels * 0.2,
                          "missing": np.where(np.arange(count) % 9, np.arange(count), np.nan)})
    timestamps = pd.date_range("2020-01-01", periods=count // 2, freq="h").repeat(2)
    return PreparedDataset(
        dataset_id="secom_fixture", source_path="fixture", source_sha256="fixture",
        features=list(frame), target="failure", target_unit="class", X=frame, y=labels,
        regimes=pd.Series(["overall"] * count), groups=pd.Series([f"entity-{i}" for i in range(count)]),
        splits={"train": list(range(60)), "validation": list(range(60, 80)), "test": list(range(80, 100))},
        manifest={"task_type": "classification", "independent_unit_kind": "production_entity",
                  "split_strategy": "stable measurement-time order", "timestamps": timestamps.astype(str).tolist()},
    )


def test_threshold_folds_are_chronological_and_keep_equal_times_together():
    data = temporal_fixture()
    calls = []
    indices = data.splits["train"]
    def fit_fold(past, future):
        calls.append((past.copy(), future.copy()))
        return np.where(data.y.iloc[np.asarray(indices)[future]] == 1, .18, .02)
    policy = choose_time_oof_threshold(data, indices, fit_fold, n_splits=3)
    assert len(calls) == 3
    for past, future in calls:
        past_times = {data.manifest["timestamps"][indices[i]] for i in past}
        future_times = {data.manifest["timestamps"][indices[i]] for i in future}
        assert max(past_times) < min(future_times)
        assert not past_times & future_times
    assert 0.01 <= policy["threshold"] <= .5
    assert policy["oof_positive_count"] >= 5
    assert policy["threshold_source"] == "train_time_oof"


@pytest.mark.parametrize("model_name", ["random_forest", "lightgbm", "xgboost"])
def test_trained_threshold_and_model_never_depend_on_validation_labels(model_name, tmp_path):
    if model_name == "xgboost":
        pytest.importorskip("xgboost")
    first = temporal_fixture()
    second = deepcopy(first)
    second.y.iloc[60:80] = 1 - second.y.iloc[60:80]
    second.y.iloc[80:] = 99
    for data in (first, second):
        original = data.split
        def split(name, original=original):
            assert name != "test"
            return original(name)
        data.split = split
    config = {"n_estimators": 8, "n_jobs": 1, "decision_mode": "time_oof"}
    a = train(first, model_name, seed=42, model_config=config)
    b = train(second, model_name, seed=42, model_config=config)
    assert a.metrics["decision_policy"]["threshold_source"] == "train_time_oof"
    assert a.metrics["decision_policy"]["threshold"] == b.metrics["decision_policy"]["threshold"]
    assert np.allclose(a.model.predict_proba(first.X.iloc[60:80]), b.model.predict_proba(first.X.iloc[60:80]))
    assert np.array_equal(a.model.predict(first.X.iloc[60:80]), b.model.predict(first.X.iloc[60:80]))
    assert a.metrics["overall_average_precision"] is not None
    assert a.best_iteration == 8
    path = tmp_path / f"{model_name}.joblib"
    joblib.dump(a.model, path)
    assert np.array_equal(joblib.load(path).predict(first.X.iloc[60:80]), a.model.predict(first.X.iloc[60:80]))


def test_time_oof_rejects_unknown_time_physical_groups_and_multiclass():
    data = temporal_fixture()
    data.manifest.pop("timestamps")
    with pytest.raises(ValueError, match="timestamp"):
        train(data, "lightgbm", model_config={"decision_mode": "time_oof"})
    data = temporal_fixture()
    data.manifest["independent_unit_kind"] = "physical_bearings"
    with pytest.raises(ValueError, match="physical"):
        train(data, "lightgbm", model_config={"decision_mode": "time_oof"})
    data = temporal_fixture()
    data.y = pd.Series([0, 1, 2, 0, 1] * 20, name="failure")
    with pytest.raises(ValueError, match="binary"):
        train(data, "lightgbm", model_config={"decision_mode": "time_oof"})


def test_time_oof_rejects_other_physical_groups_crossing_a_fold():
    data = temporal_fixture()
    data.manifest["independent_unit_kind"] = "physical_piece"
    data.groups.iloc[20] = data.groups.iloc[1]
    with pytest.raises(ValueError, match="group"):
        train(data, "lightgbm", model_config={"decision_mode": "time_oof", "n_estimators": 8, "n_jobs": 1})


def test_time_oof_rejects_group_overlap_with_outer_validation():
    data = temporal_fixture()
    data.manifest["independent_unit_kind"] = "physical_piece"
    data.groups.iloc[60] = data.groups.iloc[1]
    with pytest.raises(ValueError, match="group"):
        train(data, "lightgbm", model_config={"decision_mode": "time_oof", "n_estimators": 8, "n_jobs": 1})
