"""Small synthetic fixtures verify mechanics, not industrial model performance."""

from copy import deepcopy
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from rac_hpo.domain.schemas import PreparedDataset, TaskCancelled, TrainingResult
from rac_hpo.optimization.base import DEFAULT_SEARCH_SPACE, optimize, validate_search_space
from rac_hpo.training.lightgbm_trainer import train


def metric_result(error=1.0):
    return TrainingResult(params={}, metrics={
        "overall_mae": error, "regimes": {
            "day": {"mae": error, "samples": 10, "independent_groups": 2, "reliability": "insufficient_independent_groups"},
            "night": {"mae": error * 2, "samples": 5, "independent_groups": 1, "reliability": "insufficient_independent_groups"},
        }, "robust_objective": None, "worst_normalized_mae": None,
    }, duration_seconds=0.01)


def config(**kwargs):
    return {"algorithm": "tpe", "max_trials": 3, "timeout_seconds": 30, "seed": 42, **kwargs}


def test_runner_receives_trial_number_and_preserves_seed_in_record():
    seen = []
    result = optimize(config(seed=71), lambda params, number: (seen.append(number), metric_result())[1])
    assert seen == [0, 1, 2]
    assert [trial["seed"] for trial in result.trials] == [71, 71, 71]


def test_tpe_leaves_random_startup_after_ten_completed_trials():
    def runner(params, number):
        return metric_result(abs(params["learning_rate"] - 0.07) + params["num_leaves"] * 0.001)

    random = optimize(config(algorithm="random", max_trials=12), runner)
    tpe = optimize(config(algorithm="tpe", max_trials=12), runner)
    random_params = [trial["params"] for trial in random.trials]
    tpe_params = [trial["params"] for trial in tpe.trials]
    assert tpe_params[:10] == random_params[:10]
    assert tpe_params[10] != random_params[10]
    assert tpe_params[11] != random_params[11]


@pytest.mark.parametrize("algorithm", ["random", "tpe", "tpe_robust"])
def test_search_is_reproducible_and_stores_full_metrics(algorithm):
    cfg = config(algorithm=algorithm, baseline_errors={"day": 1, "night": 2}, weights={"day": 2, "night": 1})
    first = optimize(cfg, lambda params, number: metric_result(params["learning_rate"]))
    second = optimize(cfg, lambda params, number: metric_result(params["learning_rate"]))
    assert first.status == "completed"
    assert [trial["params"] for trial in first.trials] == [trial["params"] for trial in second.trials]
    assert first.best_params == second.best_params
    assert first.best_value == second.best_value
    assert all(trial["metrics"]["robust_objective"] is not None for trial in first.trials)
    best = first.trials[first.best_trial]
    objective = "robust_objective" if algorithm == "tpe_robust" else "overall_mae"
    assert first.best_value == best["metrics"][objective]


def test_failed_trial_is_recorded_and_next_real_trial_runs():
    def runner(params, number):
        if number == 0:
            raise ValueError("intentional unit fixture failure")
        return metric_result()

    events = []
    result = optimize(config(), runner, events.append)
    assert [trial["status"] for trial in result.trials] == ["failed", "completed", "completed"]
    assert result.trials[0]["objective"] is None
    assert "intentional unit fixture failure" in result.trials[0]["failure_reason"]
    assert len(events) == 3
    assert all(event["type"] == "trial_completed" for event in events)


def test_budget_is_checked_before_next_trial_and_overrun_is_explicit(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr("rac_hpo.optimization.base.perf_counter", lambda: elapsed[0])

    def runner(params, number):
        elapsed[0] = 6.0
        return metric_result()

    result = optimize(config(max_trials=20, timeout_seconds=5), runner)
    assert len(result.trials) == 1
    assert result.trials[0]["budget_overrun_seconds"] == 1
    assert result.duration_seconds == 6


def test_budget_exhausted_before_first_trial_cannot_report_completion(monkeypatch):
    moments = iter([0.0, 0.001, 0.001])
    monkeypatch.setattr("rac_hpo.optimization.base.perf_counter", lambda: next(moments))

    def runner(params, number):
        pytest.fail("exhausted budget must not start training")

    result = optimize(config(timeout_seconds=1e-9), runner)
    assert result.trials == []
    assert result.best_trial is None
    assert result.status == "failed"


def test_cancellation_is_acknowledged_and_does_not_look_completed():
    def runner(params, number):
        raise TaskCancelled("unit fixture cancellation")

    result = optimize(config(), runner)
    assert result.status == "cancelled"
    assert len(result.trials) == 1
    assert result.trials[0]["status"] == "cancelled"
    assert result.best_trial is None
    before_start = optimize(config(), runner, cancel_check=lambda: True)
    assert before_start.status == "cancelled"
    assert before_start.trials == []


def test_validation_regime_set_cannot_change_between_trials():
    def runner(params, number):
        result = metric_result()
        if number:
            result.metrics["regimes"]["day"]["samples"] += 1
        return result

    result = optimize(config(baseline_errors={"day": 1, "night": 2}), runner)
    assert [trial["status"] for trial in result.trials] == ["completed", "failed", "failed"]
    assert "samples changed" in result.trials[1]["failure_reason"]


@pytest.mark.parametrize("changes", [
    {"max_trials": 0}, {"max_trials": 1.5}, {"timeout_seconds": 0},
    {"timeout_seconds": float("nan")}, {"algorithm": "rac_hpo"},
    {"algorithm": "tpe_robust"}, {"seed": -1}, {"seed": True},
    {"baseline_errors": {}},
])
def test_search_configuration_is_checked_before_training(changes):
    with pytest.raises(ValueError):
        optimize(config(**changes), lambda params, number: pytest.fail("runner must not start"))


@pytest.mark.parametrize("name,spec", [
    ("learning_rate", {"type": "float", "low": 0, "high": 0.1, "log": True}),
    ("num_leaves", {"type": "int", "low": 1, "high": 8}),
    ("num_leaves", {"type": "float", "low": 2, "high": 8}),
    ("feature_fraction", {"type": "float", "low": 0.5, "high": 1.1}),
    ("lambda_l2", {"type": "float", "low": 1, "high": 0}),
    ("learning_rate", {"type": "float", "low": 0.01, "high": 0.1, "log": True, "step": 0.01}),
    ("max_depth", {"type": "int", "low": -1, "high": 3}),
])
def test_search_space_rejects_illegal_ranges(name, spec):
    space = deepcopy(DEFAULT_SEARCH_SPACE)
    space[name] = spec
    with pytest.raises(ValueError):
        validate_search_space(space)


@pytest.fixture
def small_dataset():
    rng = np.random.default_rng(71)
    X = pd.DataFrame({"known_input": rng.normal(size=240), "calendar_hour": np.arange(240) % 24})
    y = pd.Series(4 * X["known_input"] + 0.1 * X["calendar_hour"] + rng.normal(0, 0.1, 240))
    return PreparedDataset(
        dataset_id="unit_synthetic", source_path="unit_fixture", source_sha256="unit_fixture",
        features=list(X.columns), target="target", target_unit="unit", X=X, y=y,
        regimes=pd.Series(np.where(np.arange(240) % 24 < 12, "day", "night")),
        groups=pd.Series((np.arange(240) // 24).astype(str)),
        splits={"train": list(range(144)), "validation": list(range(144, 192)), "test": list(range(192, 240))},
        manifest={"purpose": "unit fixture only"},
    )


def test_real_lightgbm_training_and_search_do_not_access_test(small_dataset, monkeypatch):
    accessed = []
    original_split = small_dataset.split

    def guarded_split(name):
        accessed.append(name)
        assert name != "test", "the search must never access test"
        return original_split(name)

    monkeypatch.setattr(small_dataset, "split", guarded_split)
    model_config = {"n_estimators": 35, "early_stopping_rounds": 8, "n_jobs": 1}
    baseline = train(small_dataset, {}, model_config=model_config)
    assert baseline.status == "completed"
    assert baseline.model is not None
    assert 0 < baseline.best_iteration <= 35
    assert np.isfinite(baseline.metrics["overall_mae"])
    assert len(baseline.model.predict(small_dataset.X.iloc[144:192])) == 48
    baselines = {regime: metric["mae"] for regime, metric in baseline.metrics["regimes"].items()}
    result = optimize(config(max_trials=2, baseline_errors=baselines),
                      lambda params, number: train(small_dataset, params, seed=42, model_config=model_config))
    assert len(result.trials) == 2
    assert all(trial["status"] == "completed" for trial in result.trials)
    assert accessed == ["train", "validation"] * 3


def test_training_cancels_from_iteration_callback(small_dataset):
    calls = [0]

    def cancelled_after_start():
        calls[0] += 1
        return calls[0] >= 3

    with pytest.raises(TaskCancelled):
        train(small_dataset, {}, cancel_check=cancelled_after_start)
    assert calls[0] == 3


def test_final_refit_reuses_baseline_constructor_without_parameter_drift(small_dataset):
    from rac_hpo.training.lightgbm_trainer import build_regressor

    params = {"feature_fraction": 0.8, "lambda_l2": 1.5}
    config = {"n_estimators": 35, "early_stopping_rounds": 8, "n_jobs": 1}
    baseline = train(small_dataset, params, seed=71, model_config=config)
    refit = build_regressor(params, seed=71, model_config={**config, "n_estimators": baseline.best_iteration})
    original = baseline.model.get_params()
    final = refit.get_params()
    assert final["n_estimators"] == baseline.best_iteration
    for key, value in original.items():
        if key != "n_estimators":
            assert final[key] == value
    assert final["colsample_bytree"] == 0.8
    assert final["reg_lambda"] == 1.5
    assert "feature_fraction" not in final
    assert "lambda_l2" not in final


def test_exported_model_loads_from_chinese_path_without_prediction_drift(small_dataset, tmp_path):
    trained = train(small_dataset, {}, model_config={"n_estimators": 12, "n_jobs": 1})
    path = tmp_path / "训练模型" / "最佳模型.txt"
    path.parent.mkdir()
    path.write_text(trained.model.booster_.model_to_string(), encoding="utf-8")

    from rac_hpo.training.model_export import load_model

    restored = load_model(path)
    x_valid, _, _, _ = small_dataset.split("validation")
    np.testing.assert_allclose(restored.predict(x_valid), trained.model.predict(x_valid), rtol=0, atol=1e-12)
    assert restored.feature_name() == small_dataset.features


def test_core_imports_do_not_load_qt():
    result = subprocess.run([sys.executable, "-c", "import sys; import rac_hpo.training.lightgbm_trainer; import rac_hpo.optimization.base; assert not any(name.startswith('PySide6') for name in sys.modules)"],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
