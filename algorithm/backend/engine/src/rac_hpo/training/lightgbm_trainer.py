"""A deterministic LightGBM regression baseline on sealed dataset splits."""

from collections.abc import Callable
import math
from time import perf_counter

import lightgbm as lgb
import numpy as np

from rac_hpo.domain.schemas import PreparedDataset, TaskCancelled, TrainingResult
from rac_hpo.evaluation.evaluator import evaluate


DEFAULT_MODEL_CONFIG = {"n_estimators": 300, "early_stopping_rounds": 30, "n_jobs": 4}
DEFAULT_PARAMS = {
    "learning_rate": 0.1, "num_leaves": 31, "max_depth": -1,
    "min_child_samples": 20, "feature_fraction": 1.0, "lambda_l2": 0.0,
}


def validate_params(params: dict) -> None:
    unknown = set(params) - set(DEFAULT_PARAMS)
    if unknown:
        raise ValueError(f"unsupported model parameters: {sorted(unknown)}")
    for key, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"model parameter must be a finite number: {key}")
        if key in {"num_leaves", "max_depth", "min_child_samples"} and not isinstance(value, int):
            raise ValueError(f"model parameter must be an integer: {key}")
        if key == "max_depth" and (value == 0 or value < -1):
            raise ValueError("max_depth must be -1 or a positive integer")
        if key == "num_leaves" and value < 2:
            raise ValueError("num_leaves must be at least 2")
        if key in {"learning_rate", "min_child_samples"} and value <= 0:
            raise ValueError(f"model parameter must be positive: {key}")
        if key == "feature_fraction" and not 0 < value <= 1:
            raise ValueError("feature_fraction must be in (0, 1]")
        if key == "lambda_l2" and value < 0:
            raise ValueError("lambda_l2 must be nonnegative")


def build_regressor(
    params: dict,
    seed: int = 42,
    model_config: dict | None = None,
) -> lgb.LGBMRegressor:
    """Construct the same model for baseline, search and fixed-iteration refit.

    The caller controls fit data and callbacks. For final refit, pass the saved
    best iteration as model_config.n_estimators and omit early-stop callbacks.
    """
    validate_params(params)
    config = {**DEFAULT_MODEL_CONFIG, **(model_config or {})}
    if set(config) != set(DEFAULT_MODEL_CONFIG):
        raise ValueError("model_config supports only n_estimators, early_stopping_rounds and n_jobs")
    for key, value in config.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"model_config.{key} must be a positive integer")
    effective = {**DEFAULT_PARAMS, **params}
    # Use sklearn's canonical names to avoid ambiguous LightGBM alias precedence.
    constructor = {
        key: value for key, value in effective.items() if key not in {"feature_fraction", "lambda_l2"}
    }
    return lgb.LGBMRegressor(
        **constructor,
        colsample_bytree=effective["feature_fraction"], reg_lambda=effective["lambda_l2"],
        n_estimators=config["n_estimators"], n_jobs=config["n_jobs"],
        objective="regression", metric="mae", random_state=seed,
        deterministic=True, force_col_wise=True, verbosity=-1,
    )


def train(
    dataset: PreparedDataset,
    params: dict,
    seed: int = 42,
    model_config: dict | None = None,
    cancel_check: Callable[[], bool] | None = None,
    evaluation_config: dict | None = None,
    resource_callback: Callable[[int, float], None] | None = None,
) -> TrainingResult:
    """Train only on train and validate only on validation; never request test."""
    started = perf_counter()
    model = build_regressor(params, seed, model_config)
    config = {**DEFAULT_MODEL_CONFIG, **(model_config or {})}

    def acknowledge_cancel(_environment=None):
        if cancel_check is not None and cancel_check():
            raise TaskCancelled("training cancelled")

    acknowledge_cancel.order = 0
    acknowledge_cancel.before_iteration = True
    acknowledge_cancel()
    X_train, y_train, _, _ = dataset.split("train")
    X_valid, y_valid, regimes, groups = dataset.split("validation")
    if X_train.empty or X_valid.empty:
        raise ValueError("train and validation splits must be nonempty")
    if list(X_train.columns) != dataset.features or list(X_valid.columns) != dataset.features:
        raise ValueError("feature order differs from the fixed dataset contract")
    if not np.isfinite(np.asarray(y_train, dtype=float)).all():
        raise ValueError("training targets must be finite")
    def report_resource(environment):
        if resource_callback is not None:
            acknowledge_cancel()
            resource_callback(int(environment.iteration + 1), float(environment.evaluation_result_list[0][2]))

    report_resource.order = 20
    callbacks = [acknowledge_cancel, report_resource,
                 lgb.early_stopping(config["early_stopping_rounds"], verbose=False)]
    model.fit(
        X_train, y_train, eval_set=[(X_valid, y_valid)], eval_metric="mae",
        callbacks=callbacks,
    )
    acknowledge_cancel()
    predictions = model.predict(X_valid)
    metrics = evaluate(y_valid, predictions, regimes, groups,
                       group_kind=dataset.manifest.get("independent_unit_kind", "calendar_blocks"),
                       bootstrap_seed=seed, **(evaluation_config or {}))
    acknowledge_cancel()
    return TrainingResult(
        params={**DEFAULT_PARAMS, **params}, metrics=metrics, duration_seconds=perf_counter() - started,
        model=model, best_iteration=int(model.best_iteration_ or model.n_estimators_),
    )
