"""Fair classification HPO, reusing RAC search through an internal loss adapter."""
from collections.abc import Callable
from copy import deepcopy
import math
from time import perf_counter
import traceback

import numpy as np
import optuna
from optuna.trial import TrialState

from rac_hpo.domain.metrics import normalized_objective
from rac_hpo.domain.schemas import PreparedDataset, TaskCancelled
from rac_hpo.optimization.rac_hpo.search import RACSearchPolicy
from rac_hpo.training.classification import train, validate_parameters


SEARCH_SPACES = {
    "random_forest": {
        "max_depth": {"type": "int", "low": 3, "high": 20},
        "min_samples_leaf": {"type": "int", "low": 1, "high": 12},
        "max_features": {"type": "float", "low": .3, "high": 1.0},
        "min_samples_split": {"type": "int", "low": 2, "high": 20},
        "ccp_alpha": {"type": "float", "low": 1e-6, "high": .02, "log": True},
    },
    "lightgbm": {
        "learning_rate": {"type": "float", "low": .01, "high": .2, "log": True},
        "num_leaves": {"type": "int", "low": 8, "high": 64},
        "max_depth": {"type": "int", "low": 3, "high": 10},
        "min_child_samples": {"type": "int", "low": 5, "high": 80},
        "feature_fraction": {"type": "float", "low": .6, "high": 1.0},
        "lambda_l2": {"type": "float", "low": 1e-5, "high": 10.0, "log": True},
    },
    "xgboost": {
        "learning_rate": {"type": "float", "low": .01, "high": .2, "log": True},
        "max_depth": {"type": "int", "low": 2, "high": 8},
        "min_child_weight": {"type": "float", "low": .5, "high": 15.0, "log": True},
        "subsample": {"type": "float", "low": .6, "high": 1.0},
        "colsample_bytree": {"type": "float", "low": .5, "high": 1.0},
        "reg_lambda": {"type": "float", "low": 1e-5, "high": 10.0, "log": True},
    },
}


def _score(metrics, baseline, weights, alpha=.3, epsilon=1e-8):
    metrics = deepcopy(metrics)
    if set(metrics["regimes"]) != set(baseline) or set(weights) != set(baseline):
        raise ValueError("validation regimes changed within one classification experiment")
    regimes = sorted(baseline)
    errors = np.array([metrics["regimes"][key]["error"] for key in regimes])
    denominators = np.array([baseline[key] + epsilon for key in regimes])
    fixed_weights = np.array([weights[key] for key in regimes], dtype=float)
    fixed_weights /= fixed_weights.sum()
    normalized = errors / denominators
    metrics.update(
        robust_objective=float(normalized_objective(normalized, fixed_weights, alpha)),
        normalized_regime_error={key: float(value) for key, value in zip(regimes, normalized)},
        worst_normalized_error=float(normalized.max()),
        mean_normalized_error=float(normalized @ fixed_weights),
        weighted_regime_error=float(errors @ fixed_weights), worst_regime_error=float(errors.max()),
        regime_error_std=float(np.std(errors)),
        robustness={"alpha": alpha, "epsilon": epsilon, "weights": dict(weights),
                    "baseline_errors": dict(baseline), "error_definition": "1 - balanced_accuracy",
                    "near_zero_baseline_regimes": [key for key in regimes if baseline[key] <= epsilon]},
    )
    return metrics


def _policy_history(trials):
    """Private scalar compatibility bridge for the existing generic RAC policy.

    RAC's historical dictionary names originate in regression. Its numerical
    policy operates on nonnegative per-regime loss, here balanced error. These
    temporary aliases never leave this function/policy and are never persisted.
    The group sums use fixed per-class weights, whose row mean is balanced
    error; bootstrap evidence is explicitly an uncalibrated approximation.
    """
    result = []
    for original in trials:
        if original["number"] < 0:
            continue
        trial = deepcopy(original)
        if trial["status"] == "completed":
            metrics = trial["metrics"]
            metrics["overall_mae"] = metrics["overall_error"]
            for metric in metrics["regimes"].values():
                metric["mae"] = metric["error"]
                metric["group_errors"] = [{
                    "group_id": record["group_id"], "samples": record["samples"],
                    "absolute_error_sum": record["class_weighted_error_sum"],
                    "mae": record["balanced_error"],
                } for record in metric["group_errors"]]
        result.append(trial)
    return result


def _public_diagnostics(value):
    names = {
        "predicted_overall_mae": "predicted_weighted_balanced_error",
        "predicted_regime_mae": "predicted_regime_balanced_error",
        "normalized_mae": "normalized_absolute_prediction_error",
        "naive_normalized_mae": "naive_normalized_absolute_prediction_error",
    }
    if isinstance(value, dict):
        return {names.get(key, key.replace("mae", "classification_loss")): _public_diagnostics(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_public_diagnostics(item) for item in value]
    return value


def optimize(dataset: PreparedDataset, model_name: str, algorithm: str, seed: int = 42,
             max_trials: int = 24, timeout_seconds: float = 300, model_config: dict | None = None,
             progress_callback: Callable[[dict], None] | None = None,
             cancel_check: Callable[[], bool] | None = None) -> dict:
    """Select every method on the same fixed J, split, space and model resources.

    The default baseline is trial -1 and outside the search budget. New trials
    start only before the wall-clock deadline; a running trial may overrun it.
    Failed and cancelled trials consume trial budget and retain traceback logs.
    All selections use validation only; no part of this function requests test.
    """
    _, resources = validate_parameters(model_name, model_config=model_config)
    if algorithm not in {"random", "tpe", "rac_hpo"}:
        raise ValueError("classification algorithm must be random, tpe or rac_hpo")
    if isinstance(max_trials, bool) or not isinstance(max_trials, int) or max_trials <= 0:
        raise ValueError("max_trials must be a positive integer")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer in [0,2**32-1]")
    space = deepcopy(SEARCH_SPACES[model_name])
    result = {
        "task_type": "classification", "model_name": model_name, "algorithm": algorithm,
        "status": "completed", "best_params": {}, "best_value": None, "best_trial": None,
        "best_result": None, "baseline_result": None, "trials": [], "duration_seconds": 0.0,
        "initialization_seconds": 0.0, "search_seconds": 0.0,
        "baseline_errors": {}, "weights": {}, "search_space": space,
        "objective_definition": {"name": "fixed_baseline_normalized_j", "alpha": .3, "epsilon": 1e-8,
                                 "error": "1 - balanced_accuracy", "weights": "validation sample proportions",
                                 "formula": "0.7 * weighted_mean(error_g / (baseline_error_g + 1e-8)) + 0.3 * max(error_g / (baseline_error_g + 1e-8))",
                                 "selection_metric_shared_by_all_methods": True},
        "budget": {"max_trials": max_trials, "timeout_seconds": timeout_seconds,
                   "baseline_in_search_budget": False, "model_resources": resources,
                   "running_trial_can_overrun_deadline": True},
        "rac_diagnostics": {"policy_version": "rac-hpo-0.2.0", "warmup_trials": 16,
                            "loss_adapter": "classification balanced error; private scalar compatibility only",
                            "attention_evidence": "physical bearings only; at least five independent groups",
                            "uncertainty": "uncalibrated search approximation"},
    }
    total_started = perf_counter()
    try:
        baseline = train(dataset, model_name, seed=seed, model_config=resources, cancel_check=cancel_check)
    except TaskCancelled:
        result.update(status="cancelled", duration_seconds=perf_counter() - total_started,
                      initialization_seconds=perf_counter() - total_started)
        return result
    result["initialization_seconds"] = perf_counter() - total_started
    baseline_errors = {key: float(metric["error"]) for key, metric in baseline.metrics["regimes"].items()}
    samples = {key: int(metric["samples"]) for key, metric in baseline.metrics["regimes"].items()}
    weights = {key: value / sum(samples.values()) for key, value in samples.items()}
    baseline.metrics = _score(baseline.metrics, baseline_errors, weights)
    baseline_record = {"number": -1, "role": "baseline", "params": baseline.params, "seed": seed,
                       "status": "completed", "duration_seconds": baseline.duration_seconds,
                       "metrics": deepcopy(baseline.metrics), "objective": baseline.metrics["robust_objective"],
                       "best_iteration": baseline.best_iteration, "failure_reason": None}
    result.update(baseline_result=baseline, baseline_errors=baseline_errors, weights=weights)
    result["trials"].append(baseline_record)
    if progress_callback:
        progress_callback({"type": "baseline_completed", "trial": deepcopy(baseline_record)})
    sampler = optuna.samplers.RandomSampler(seed=seed) if algorithm == "random" else optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="minimize", sampler=sampler)
    policy = None
    if algorithm == "rac_hpo":
        policy = RACSearchPolicy({"seed": seed, "baseline_errors": baseline_errors, "weights": weights,
                                  "baseline_overall_mae": sum(weights[key] * baseline_errors[key] for key in weights),
                                  "minimum_independent_groups": 5}, space)
    search_started = perf_counter()
    for _ in range(max_trials):
        if cancel_check and cancel_check():
            result["status"] = "cancelled"
            break
        if perf_counter() - search_started >= timeout_seconds:
            break
        selection, selection_seconds = None, 0.0
        if policy is not None:
            selection_started = perf_counter()
            try:
                candidate, selection = policy.propose(_policy_history(result["trials"]), cancel_check)
            except TaskCancelled:
                result["status"] = "cancelled"
                break
            except Exception as error:
                candidate, selection = None, {"method": "tpe_fallback", "reason": "surrogate_error",
                                               "error": f"{type(error).__name__}: {error}"}
            selection_seconds = perf_counter() - selection_started
            if cancel_check and cancel_check():
                result["status"] = "cancelled"
                break
            if perf_counter() - search_started >= timeout_seconds:
                break
            if candidate is not None:
                study.enqueue_trial(candidate)
        trial = study.ask()
        params = {}
        for name, spec in space.items():
            suggest = trial.suggest_int if spec["type"] == "int" else trial.suggest_float
            params[name] = suggest(name, **{key: value for key, value in spec.items() if key != "type"})
        record = {"number": trial.number, "params": params, "seed": seed, "status": "failed",
                  "duration_seconds": 0.0, "metrics": {}, "objective": None, "failure_reason": None,
                  "selection_duration_seconds": selection_seconds}
        if selection is not None:
            record["selection"] = _public_diagnostics(selection)
        trial_started = perf_counter()
        try:
            trained = train(dataset, model_name, params, seed, resources, cancel_check)
            if trained.status == "cancelled":
                raise TaskCancelled(trained.failure_reason or "classification trial cancelled")
            if trained.status != "completed":
                raise RuntimeError(trained.failure_reason or f"classification training status: {trained.status}")
            current_samples = {key: metric["samples"] for key, metric in trained.metrics["regimes"].items()}
            if current_samples != samples:
                raise ValueError("classification validation regime samples changed during HPO")
            trained.metrics = _score(trained.metrics, baseline_errors, weights)
            value = float(trained.metrics["robust_objective"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("classification objective must be finite and nonnegative")
            record.update(status="completed", duration_seconds=trained.duration_seconds,
                          metrics=deepcopy(trained.metrics), objective=value, best_iteration=trained.best_iteration)
            study.tell(trial, value)
            if result["best_value"] is None or value < result["best_value"]:
                result.update(best_params=dict(params), best_value=value, best_trial=trial.number, best_result=trained)
        except TaskCancelled as error:
            record.update(status="cancelled", failure_reason=str(error), failure_traceback=traceback.format_exc())
            study.tell(trial, state=TrialState.FAIL)
            result["status"] = "cancelled"
        except Exception as error:
            record.update(failure_reason=f"{type(error).__name__}: {error}", failure_traceback=traceback.format_exc())
            study.tell(trial, state=TrialState.FAIL)
        record["wall_duration_seconds"] = perf_counter() - trial_started
        if record["status"] != "completed":
            record["duration_seconds"] = record["wall_duration_seconds"]
        record["budget_overrun_seconds"] = max(0.0, perf_counter() - search_started - timeout_seconds)
        result["trials"].append(record)
        if progress_callback:
            progress_callback({"type": "trial_completed", "trial": deepcopy(record)})
        if result["status"] == "cancelled":
            break
    result.update(search_seconds=perf_counter() - search_started, duration_seconds=perf_counter() - total_started)
    if result["status"] != "cancelled" and result["best_trial"] is None:
        result["status"] = "failed"
    if policy:
        selections = [trial.get("selection", {}) for trial in result["trials"] if trial["number"] >= 0]
        reasons = {}
        for selection in selections:
            if selection.get("method") == "tpe_fallback":
                reason = selection.get("reason", "unspecified")
                reasons[reason] = reasons.get(reason, 0) + 1
        result["rac_diagnostics"].update(fallback_reasons=reasons,
                                         acquisition_trials=sum(selection.get("method") == "rac_acquisition" for selection in selections))
    return result
