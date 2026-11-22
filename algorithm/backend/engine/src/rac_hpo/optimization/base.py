"""Common ask/tell loop for real training, failures, cancellation and budgets."""

from collections.abc import Callable
from copy import deepcopy
import math
from time import perf_counter

import optuna
from optuna.trial import TrialState

from rac_hpo.domain.schemas import OptimizationResult, TaskCancelled, TrainingResult
from rac_hpo.optimization.baselines import SEARCH_METHODS, RESOURCE_METHODS, grid_candidates, resource_pruner
from rac_hpo.optimization.robust_objective import apply_robust_metrics
from rac_hpo.training.lightgbm_trainer import DEFAULT_PARAMS, validate_params


DEFAULT_SEARCH_SPACE = {
    "learning_rate": {"type": "float", "low": 0.01, "high": 0.2, "log": True},
    "num_leaves": {"type": "int", "low": 8, "high": 64},
    "max_depth": {"type": "int", "low": 3, "high": 10},
    "min_child_samples": {"type": "int", "low": 20, "high": 150},
    "feature_fraction": {"type": "float", "low": 0.6, "high": 1.0},
    "lambda_l2": {"type": "float", "low": 1e-5, "high": 10.0, "log": True},
}


def validate_search_space(search_space: dict) -> None:
    if not isinstance(search_space, dict) or set(search_space) != set(DEFAULT_PARAMS):
        raise ValueError("search_space must define exactly the six documented parameters")
    for key, spec in search_space.items():
        if not isinstance(spec, dict) or set(spec) - {"type", "low", "high", "log", "step"}:
            raise ValueError(f"invalid search-space definition: {key}")
        required_type = "int" if key in {"num_leaves", "max_depth", "min_child_samples"} else "float"
        if spec.get("type") != required_type or "low" not in spec or "high" not in spec:
            raise ValueError(f"invalid search-space type or bounds: {key}")
        low, high = spec["low"], spec["high"]
        validate_params({key: low})
        validate_params({key: high})
        if low > high:
            raise ValueError(f"search-space low exceeds high: {key}")
        if key == "max_depth" and low < 1 and (low != -1 or high != -1):
            raise ValueError("max_depth search bounds must both be positive, or both be -1")
        if not isinstance(spec.get("log", False), bool):
            raise ValueError(f"search-space log must be boolean: {key}")
        if spec.get("log") and low <= 0:
            raise ValueError(f"log search requires positive lower bound: {key}")
        if "step" in spec:
            step = spec["step"]
            if isinstance(step, bool) or not isinstance(step, (int, float)) or not math.isfinite(step) or step <= 0:
                raise ValueError(f"step must be positive and finite: {key}")
            if required_type == "int" and not isinstance(step, int):
                raise ValueError(f"integer search requires an integer step: {key}")
            if spec.get("log") and (required_type == "float" or step != 1):
                raise ValueError(f"log search does not support this step: {key}")


def _suggest(trial, space):
    params = {}
    for name, spec in space.items():
        arguments = {key: value for key, value in spec.items() if key != "type"}
        suggest = trial.suggest_int if spec["type"] == "int" else trial.suggest_float
        params[name] = suggest(name, **arguments)
    return params


def optimize(
    task_config: dict,
    trial_runner: Callable[[dict, int], TrainingResult],
    progress_callback: Callable[[dict], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> OptimizationResult:
    """Check time budget before each trial; an already running trial may exceed it.

    Failed trials consume trial/time budget and are retained. Cancellation is
    acknowledged separately from successful completion. Resource methods require
    a three-argument runner(params, number, report), reporting validation MAE.
    """
    config = deepcopy(task_config)
    algorithm = config.get("algorithm", "tpe")
    if algorithm not in SEARCH_METHODS:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
    max_trials = config.get("max_trials", 20)
    timeout = config.get("timeout_seconds", 300)
    seed = config.get("seed", 42)
    if isinstance(max_trials, bool) or not isinstance(max_trials, int) or max_trials <= 0:
        raise ValueError("max_trials must be a positive integer")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer in [0, 2**32-1]")
    space = config.get("search_space", DEFAULT_SEARCH_SPACE)
    validate_search_space(space)
    baseline = config.get("baseline_errors")
    if algorithm in {"tpe_robust", "rac_hpo"} and not baseline:
        raise ValueError("Robust search requires fixed baseline_errors")
    if baseline is not None:
        # Validate fixed objective controls before expensive training starts.
        baseline_metrics = {key: {"mae": value, "samples": 1} for key, value in baseline.items()}
        apply_robust_metrics({"regimes": baseline_metrics}, baseline, config.get("alpha", 0.3),
                              config.get("weights"), config.get("epsilon", 1e-8))
    sampler = (optuna.samplers.RandomSampler(seed=seed) if algorithm in {"random", "grid"} | RESOURCE_METHODS
               else optuna.samplers.TPESampler(seed=seed))
    pruner = resource_pruner(algorithm, config) if algorithm in RESOURCE_METHODS else optuna.pruners.NopPruner()
    study = optuna.create_study(direction="minimize", sampler=sampler, pruner=pruner,
                                study_name=f"rac-hpo-{algorithm}-{seed}")
    grid = iter(grid_candidates(space)) if algorithm == "grid" else None
    policy = None
    if algorithm == "rac_hpo":
        from rac_hpo.optimization.rac_hpo.search import RACSearchPolicy
        policy = RACSearchPolicy(config, space)
    result = OptimizationResult(algorithm=algorithm, best_params={}, best_value=None, best_trial=None)
    fixed_weights = config.get("weights")
    fixed_samples = None
    started = perf_counter()
    for _ in range(max_trials):
        if cancel_check is not None and cancel_check():
            result.status = "cancelled"
            break
        if perf_counter() - started >= timeout:
            break
        selection, selection_seconds = None, 0.0
        if policy is not None:
            selection_started = perf_counter()
            try:
                candidate, selection = policy.propose(result.trials, cancel_check)
            except Exception as error:
                candidate = None
                selection = {"method": "tpe_fallback", "reason": "surrogate_error",
                             "error": f"{type(error).__name__}: {error}"}
            selection_seconds = perf_counter() - selection_started
            if cancel_check is not None and cancel_check():
                result.status = "cancelled"
                break
            if perf_counter() - started >= timeout:
                break
            if candidate is not None:
                study.enqueue_trial(candidate)
        if grid is not None:
            candidate = next(grid, None)
            if candidate is None:
                break
            study.enqueue_trial(candidate)
        optuna_trial = study.ask()
        params = _suggest(optuna_trial, space)
        record = {
            "number": optuna_trial.number, "params": params, "seed": seed,
            "status": "failed", "duration_seconds": 0.0, "metrics": {},
            "objective": None, "failure_reason": None,
        }
        if selection is not None:
            record.update(selection=selection, selection_duration_seconds=selection_seconds,
                          predicted_cost_seconds=selection.get("predicted_cost_seconds"))
        trial_started = perf_counter()
        resource = {"unit": "boosting_iterations", "last_step": 0, "intermediate_values": [],
                    "min_resource": config.get("min_resource", 10),
                    "max_resource": config.get("max_resource", 300),
                    "reduction_factor": config.get("reduction_factor", 3)}

        def report(step, value):
            if cancel_check is not None and cancel_check():
                raise TaskCancelled("training cancelled before resource decision")
            if (isinstance(step, bool) or not isinstance(step, int) or
                    not resource["last_step"] < step <= resource["max_resource"]):
                raise ValueError("resource steps must increase within max_resource")
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError("intermediate validation MAE must be nonnegative and finite")
            resource["last_step"] = step
            resource["intermediate_values"].append({"step": step, "validation_mae": value})
            optuna_trial.report(value, step)
            # A full-resource model is eligible for selection, never pruned at the cap.
            if step < resource["max_resource"] and optuna_trial.should_prune():
                raise optuna.TrialPruned(f"{algorithm} pruned at boosting iteration {step}")

        if algorithm in RESOURCE_METHODS:
            record["resource"] = resource
        try:
            trained = (trial_runner(params, optuna_trial.number, report) if algorithm in RESOURCE_METHODS
                       else trial_runner(params, optuna_trial.number))
            if algorithm in RESOURCE_METHODS and not resource["last_step"]:
                raise ValueError("resource runner did not report any validation iterations")
            record["duration_seconds"] = float(trained.duration_seconds)
            record["metrics"] = deepcopy(trained.metrics)
            if trained.status == "cancelled":
                raise TaskCancelled(trained.failure_reason or "trial cancelled")
            if trained.status != "completed":
                raise RuntimeError(trained.failure_reason or f"training status: {trained.status}")
            metrics = deepcopy(trained.metrics)
            if baseline is not None:
                samples = {key: value["samples"] for key, value in metrics["regimes"].items()}
                if fixed_samples is None:
                    fixed_samples = samples
                    if fixed_weights is None:
                        fixed_weights = samples
                elif samples != fixed_samples:
                    raise ValueError("validation regime samples changed within one experiment")
                metrics = apply_robust_metrics(metrics, baseline, config.get("alpha", 0.3),
                                                fixed_weights, config.get("epsilon", 1e-8))
            value = float(metrics["robust_objective"] if algorithm in {"tpe_robust", "rac_hpo"} else metrics["overall_mae"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("trial objective must be nonnegative and finite")
            record.update(status="completed", metrics=metrics, objective=value,
                          best_iteration=trained.best_iteration)
            study.tell(optuna_trial, value)
            if result.best_value is None or value < result.best_value:
                result.best_params = dict(params)
                result.best_value = value
                result.best_trial = optuna_trial.number
        except optuna.TrialPruned as error:
            record.update(status="pruned", failure_reason=None, pruning_reason=str(error))
            study.tell(optuna_trial, state=TrialState.PRUNED)
        except TaskCancelled as error:
            record.update(status="cancelled", failure_reason=str(error))
            study.tell(optuna_trial, state=TrialState.FAIL)
            result.status = "cancelled"
        except Exception as error:
            record["failure_reason"] = f"{type(error).__name__}: {error}"
            study.tell(optuna_trial, state=TrialState.FAIL)
        record["wall_duration_seconds"] = perf_counter() - trial_started
        if record["status"] != "completed":
            record["duration_seconds"] = record["wall_duration_seconds"]
        record["budget_overrun_seconds"] = max(0.0, perf_counter() - started - timeout)
        result.trials.append(record)
        if progress_callback is not None:
            progress_callback({"type": "trial_completed", "trial": deepcopy(record)})
        if result.status == "cancelled":
            break
    result.duration_seconds = perf_counter() - started
    if result.status != "cancelled" and result.best_trial is None:
        result.status = "failed"
    return result
