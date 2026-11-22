"""Classification contracts use small fixtures, never industrial evidence."""
import joblib
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest

from rac_hpo.domain.schemas import PreparedDataset, TaskCancelled
from rac_hpo.evaluation.classification import evaluate
from rac_hpo.optimization.classification import optimize
from rac_hpo.training.classification import train


def fixture_dataset():
    rng = np.random.default_rng(31)
    count = 72
    frame = pd.DataFrame({"signal": rng.normal(size=count), "mode": ["a", "b"] * 36})
    frame.loc[2, "signal"] = np.nan
    frame.loc[50:, "mode"] = "validation_only"
    labels = pd.Series(([0, 0, 1] * 24), name="failure")
    return PreparedDataset(
        dataset_id="unit_classification", source_path="fixture", source_sha256="fixture",
        features=list(frame), target="failure", target_unit="class", X=frame, y=labels,
        regimes=pd.Series(["low", "high"] * 36), groups=pd.Series([f"bearing_{i // 3}" for i in range(count)]),
        splits={"train": list(range(48)), "validation": list(range(48, 66)), "test": list(range(66, 72))},
        manifest={"task_type": "classification", "independent_unit_kind": "physical_bearings", "synthetic": True},
    )


def test_evaluator_sparse_classes_and_independent_groups():
    result = evaluate([0, 0, 1, 1], [0, 1, 1, 1], [[.9, .1], [.2, .8], [.1, .9], [.1, .9]],
                      ["a", "a", "b", "b"], ["u1", "u2", "u3", "u4"], class_labels=[0, 1])
    assert result["overall_balanced_accuracy"] == .75
    assert result["overall_error"] == .25
    assert result["regimes"]["a"]["missing_classes"] == [1]
    assert result["regimes"]["a"]["class_recall"]["1"] is None
    assert result["regimes"]["a"]["sparse_class_warning"]
    assert result["regimes"]["a"]["effective_independent_groups"] is None
    assert result["regimes"]["a"]["samples"] == 2
    assert "mae" not in str(result).lower()


@pytest.mark.parametrize("model_name", ["random_forest", "lightgbm"])
def test_train_never_reads_test_and_fits_preprocessing_on_train(model_name, tmp_path):
    dataset = fixture_dataset()
    requested = []
    original_split = dataset.split
    def split(name):
        requested.append(name)
        assert name != "test"
        return original_split(name)
    dataset.split = split
    dataset.y.iloc[dataset.splits["test"]] = 99
    result = train(dataset, model_name, model_config={"n_estimators": 10, "early_stopping_rounds": 3, "n_jobs": 1})
    assert requested == ["train", "validation"]
    assert 0 <= result.metrics["overall_balanced_accuracy"] <= 1
    assert result.metrics["task_type"] == "classification"
    assert "validation_only" not in str(result.metrics["preprocessing"]["categories"])
    model_path = tmp_path / "model.joblib"
    joblib.dump(result.model, model_path)
    loaded = joblib.load(model_path)
    assert np.array_equal(loaded.predict(dataset.X.iloc[:5]), result.model.predict(dataset.X.iloc[:5]))
    assert "mae" not in str(result.metrics).lower()


def test_unknown_validation_class_and_cancellation_fail_explicitly():
    dataset = fixture_dataset()
    dataset.y.iloc[48] = 5
    with pytest.raises(ValueError, match="unseen"):
        train(dataset, "random_forest", model_config={"n_estimators": 5})
    with pytest.raises(TaskCancelled):
        train(fixture_dataset(), "random_forest", cancel_check=lambda: True)


def test_hpo_freezes_baseline_and_records_all_trials():
    dataset = fixture_dataset()
    events = []
    result = optimize(dataset, "random_forest", "random", max_trials=2, timeout_seconds=30,
                      model_config={"n_estimators": 5, "early_stopping_rounds": 2, "n_jobs": 1},
                      progress_callback=events.append)
    assert [trial["number"] for trial in result["trials"]] == [-1, 0, 1]
    assert result["status"] == "completed"
    assert result["best_result"].metrics["robustness"]["baseline_errors"] == result["baseline_errors"]
    assert len(events) == 3
    assert "mae" not in str({key: value for key, value in result.items() if not key.endswith("result")}).lower()


def test_rac_reaches_proxy_or_explicit_forward_fallback():
    result = optimize(fixture_dataset(), "random_forest", "rac_hpo", max_trials=17,
                      timeout_seconds=60, model_config={"n_estimators": 3, "n_jobs": 1})
    selections = [trial.get("selection", {}) for trial in result["trials"] if trial["number"] >= 16]
    assert selections
    assert selections[0]["reason"] != "insufficient_history"
    assert "mae" not in str(selections).lower()


def test_split_overlap_and_invalid_parameters_rejected():
    dataset = fixture_dataset()
    dataset.splits["validation"].append(3)
    with pytest.raises(ValueError, match="overlap"):
        train(dataset, "random_forest")
    with pytest.raises(ValueError, match="dictionary"):
        train(fixture_dataset(), "lightgbm", params=["learning_rate"])
    with pytest.raises(ValueError, match="unsupported"):
        train(fixture_dataset(), "lightgbm", params={"target": 5})
    dataset = fixture_dataset()
    dataset.groups.iloc[48] = dataset.groups.iloc[0]
    with pytest.raises(ValueError, match="bearing groups overlap"):
        train(dataset, "random_forest")


def test_multiclass_and_empty_train_feature_column():
    dataset = fixture_dataset()
    dataset.y = pd.Series([0, 1, 2] * 24)
    dataset.X["empty"] = np.nan
    dataset.X.loc[48:, "empty"] = 12345
    dataset.features = list(dataset.X)
    result = train(dataset, "lightgbm", model_config={"n_estimators": 6, "n_jobs": 1})
    assert result.metrics["class_labels"] == [0, 1, 2]
    assert result.model.predict_proba(dataset.X.iloc[:3]).shape == (3, 3)
    assert result.model.preprocessor.named_transformers_["numeric"].statistics_[-1] == 0
    assert result.metrics["learning_curve"]


def test_failed_trials_keep_error_and_search_continues(monkeypatch):
    import rac_hpo.optimization.classification as optimizer
    baseline = train(fixture_dataset(), "random_forest", model_config={"n_estimators": 2, "n_jobs": 1})
    calls = 0
    def runner(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled unit failure")
        return deepcopy(baseline)
    monkeypatch.setattr(optimizer, "train", runner)
    events = []
    result = optimizer.optimize(fixture_dataset(), "random_forest", "tpe", max_trials=2,
                                progress_callback=events.append)
    assert result["trials"][1]["status"] == "failed"
    assert "controlled unit failure" in result["trials"][1]["failure_traceback"]
    assert result["trials"][2]["status"] == "completed"
    assert result["status"] == "completed"
    assert len(events) == 3


def test_cancellation_after_baseline_retains_completed_baseline():
    cancelled = False
    def callback(event):
        nonlocal cancelled
        cancelled = True
    result = optimize(fixture_dataset(), "random_forest", "tpe", max_trials=5,
                      model_config={"n_estimators": 2, "n_jobs": 1},
                      progress_callback=callback, cancel_check=lambda: cancelled)
    assert result["status"] == "cancelled"
    assert len(result["trials"]) == 1
    assert result["trials"][0]["number"] == -1
    assert result["best_result"] is None


def test_xgboost_curves_and_persistence(tmp_path):
    pytest.importorskip("xgboost")
    dataset = fixture_dataset()
    result = train(dataset, "xgboost", model_config={"n_estimators": 8, "early_stopping_rounds": 3, "n_jobs": 1})
    assert result.metrics["learning_curve"]
    path = tmp_path / "xgboost.joblib"
    joblib.dump(result.model, path)
    assert np.allclose(joblib.load(path).predict_proba(dataset.X.iloc[:6]), result.model.predict_proba(dataset.X.iloc[:6]))
