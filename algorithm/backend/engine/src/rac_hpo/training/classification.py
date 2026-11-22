"""Train-only preprocessing and three tabular classifiers on sealed splits."""
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
import math
from time import perf_counter

import lightgbm as lgb
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, OneHotEncoder

from rac_hpo.domain.schemas import PreparedDataset, TaskCancelled, TrainingResult
from rac_hpo.evaluation.classification import PHYSICAL_BEARING_KINDS, evaluate


DEFAULT_MODEL_CONFIG = {"n_estimators": 200, "early_stopping_rounds": 20, "n_jobs": 4}
DEFAULT_PARAMS = {
    "random_forest": {"max_depth": 12, "min_samples_leaf": 1, "max_features": .8,
                      "min_samples_split": 2, "ccp_alpha": 0.0},
    "lightgbm": {"learning_rate": .1, "num_leaves": 31, "max_depth": 7,
                 "min_child_samples": 20, "feature_fraction": 1.0, "lambda_l2": 0.0},
    "xgboost": {"learning_rate": .1, "max_depth": 6, "min_child_weight": 1.0,
                "subsample": 1.0, "colsample_bytree": 1.0, "reg_lambda": 1.0},
}


def _normalise_missing_categories(frame):
    """Canonical missing markers only; this step learns no dataset statistics."""
    result = frame.copy()
    for name in result:
        if not is_numeric_dtype(result[name]):
            result[name] = result[name].astype(object).where(result[name].notna(), np.nan)
    return result


@dataclass
class ClassificationModel:
    """A joblib-persistable predictor containing the fitted train preprocessing."""
    preprocessor: ColumnTransformer
    estimator: object
    label_encoder: LabelEncoder
    features: list[str]
    decision_threshold: float | None = None

    @property
    def classes_(self):
        return self.label_encoder.classes_

    def _transform(self, frame):
        if not isinstance(frame, pd.DataFrame) or list(frame.columns) != self.features:
            raise ValueError("prediction feature order differs from the saved model contract")
        matrix = self.preprocessor.transform(_normalise_missing_categories(frame))
        if isinstance(self.estimator, lgb.LGBMClassifier):
            return pd.DataFrame(matrix, columns=self.estimator.feature_name_, index=frame.index)
        return matrix

    def predict(self, frame):
        matrix = self._transform(frame)
        if self.decision_threshold is None:
            predicted = self.estimator.predict(matrix)
        else:
            predicted = (self.estimator.predict_proba(matrix)[:, 1] >= self.decision_threshold).astype(int)
        return self.label_encoder.inverse_transform(np.asarray(predicted, dtype=int))

    def predict_proba(self, frame):
        return self.estimator.predict_proba(self._transform(frame))


def validate_parameters(model_name, params=None, model_config=None):
    if model_name not in DEFAULT_PARAMS:
        raise ValueError(f"unsupported classification model: {model_name}")
    params = {} if params is None else params
    if not isinstance(params, dict):
        raise ValueError("classification parameters must be a dictionary")
    if set(params) - set(DEFAULT_PARAMS[model_name]):
        raise ValueError(f"unsupported {model_name} parameters: {sorted(set(params) - set(DEFAULT_PARAMS[model_name]))}")
    integer_keys = {"max_depth", "min_samples_leaf", "min_samples_split", "num_leaves", "min_child_samples"}
    for name, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"classification parameter must be finite numeric: {name}")
        if name in integer_keys and (not isinstance(value, int) or value < 1):
            raise ValueError(f"classification parameter must be a positive integer: {name}")
        if name in {"learning_rate", "min_child_weight"} and value <= 0:
            raise ValueError(f"classification parameter must be positive: {name}")
        if name in {"max_features", "feature_fraction", "subsample", "colsample_bytree"} and not 0 < value <= 1:
            raise ValueError(f"classification parameter must lie in (0,1]: {name}")
        if name in {"reg_lambda", "lambda_l2", "ccp_alpha"} and value < 0:
            raise ValueError(f"classification regularization must be nonnegative: {name}")
        if name in {"num_leaves", "min_samples_split"} and value < 2:
            raise ValueError(f"classification parameter must be at least two: {name}")
    if model_config is not None and not isinstance(model_config, dict):
        raise ValueError("classification model_config must be a dictionary")
    config = {**DEFAULT_MODEL_CONFIG, **(model_config or {})}
    if set(config) - {*DEFAULT_MODEL_CONFIG, "decision_mode", "oof_folds"}:
        raise ValueError("unsupported classification model_config field")
    if any(isinstance(config[key], bool) or not isinstance(config[key], int) or config[key] <= 0
           for key in DEFAULT_MODEL_CONFIG):
        raise ValueError("classification model resource values must be positive integers")
    mode = config.get("decision_mode", "native")
    if mode not in {"native", "time_oof"}:
        raise ValueError("decision_mode must be native or time_oof")
    if mode == "time_oof":
        config.setdefault("oof_folds", 3)
        if isinstance(config["oof_folds"], bool) or not isinstance(config["oof_folds"], int) or not 2 <= config["oof_folds"] <= 10:
            raise ValueError("oof_folds must be an integer between 2 and 10")
    elif "oof_folds" in config:
        raise ValueError("oof_folds requires decision_mode=time_oof")
    return {**DEFAULT_PARAMS[model_name], **params}, config


def _preprocessor(frame):
    numeric = [column for column in frame if is_numeric_dtype(frame[column])]
    categorical = [column for column in frame if column not in numeric]
    transformers = []
    if numeric:
        transformers.append(("numeric", SimpleImputer(strategy="median", keep_empty_features=True), numeric))
    if categorical:
        transformers.append(("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="constant", fill_value="__missing__", keep_empty_features=True)),
            ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), categorical))
    return ColumnTransformer(transformers, remainder="drop", sparse_threshold=0), categorical


def _estimator(model_name, params, config, seed, classes, acknowledge_cancel, use_early_stopping):
    if model_name == "random_forest":
        return RandomForestClassifier(**params, n_estimators=config["n_estimators"],
                                      n_jobs=config["n_jobs"], random_state=seed, warm_start=True)
    if model_name == "lightgbm":
        constructor = {key: value for key, value in params.items() if key not in {"feature_fraction", "lambda_l2"}}
        return lgb.LGBMClassifier(**constructor, colsample_bytree=params["feature_fraction"],
                                  reg_lambda=params["lambda_l2"], n_estimators=config["n_estimators"],
                                  n_jobs=config["n_jobs"], random_state=seed, deterministic=True,
                                  force_col_wise=True, verbosity=-1)
    try:
        import xgboost as xgb
    except ImportError as error:
        raise RuntimeError("xgboost is required for the xgboost model") from error

    class CancelCallback(xgb.callback.TrainingCallback):
        def before_iteration(self, model, epoch, evals_log):
            acknowledge_cancel()
            return False

    loss = "logloss" if classes == 2 else "mlogloss"
    return xgb.XGBClassifier(**params, n_estimators=config["n_estimators"], n_jobs=config["n_jobs"],
                             random_state=seed, tree_method="hist", eval_metric=loss,
                             objective="binary:logistic" if classes == 2 else "multi:softprob",
                             early_stopping_rounds=config["early_stopping_rounds"] if use_early_stopping else None,
                             callbacks=[CancelCallback()], verbosity=0)


def _fit_forest(estimator, matrix, target, config, acknowledge_cancel):
    for trees in range(min(16, config["n_estimators"]), config["n_estimators"] + 16, 16):
        acknowledge_cancel()
        estimator.n_estimators = min(trees, config["n_estimators"])
        estimator.fit(matrix, target)
        if estimator.n_estimators == config["n_estimators"]:
            break


def _fold_probabilities(model_name, params, config, seed, frame, target, past, future, acknowledge_cancel):
    """Each fold fits its own preprocessing and model with a fixed tree budget."""
    acknowledge_cancel()
    transformer, _ = _preprocessor(frame.iloc[past])
    train_matrix = transformer.fit_transform(frame.iloc[past])
    valid_matrix = transformer.transform(frame.iloc[future])
    if not np.isfinite(train_matrix).all() or not np.isfinite(valid_matrix).all():
        raise ValueError("time_oof preprocessing produced non-finite features")
    estimator = _estimator(model_name, params, config, seed, 2, acknowledge_cancel, False)
    if model_name == "random_forest":
        _fit_forest(estimator, train_matrix, target[past], config, acknowledge_cancel)
    elif model_name == "lightgbm":
        estimator.fit(train_matrix, target[past], callbacks=[acknowledge_cancel])
        valid_matrix = pd.DataFrame(valid_matrix, columns=estimator.feature_name_)
    else:
        estimator.fit(train_matrix, target[past], verbose=False)
    acknowledge_cancel()
    return estimator.predict_proba(valid_matrix)[:, 1]


def train(dataset: PreparedDataset, model_name: str, params: dict | None = None, seed: int = 42,
          model_config: dict | None = None, cancel_check: Callable[[], bool] | None = None) -> TrainingResult:
    """Never request test; early stopping and metrics use validation only."""
    started = perf_counter()
    effective, config = validate_parameters(model_name, params, model_config)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer in [0,2**32-1]")
    if dataset.manifest.get("task_type") != "classification":
        raise ValueError("classification training requires manifest.task_type=classification")

    def acknowledge_cancel(_environment=None):
        if cancel_check is not None and cancel_check():
            raise TaskCancelled("classification training cancelled")

    acknowledge_cancel.order = 0
    acknowledge_cancel.before_iteration = True
    acknowledge_cancel()
    train_indices, validation_indices = dataset.splits["train"], dataset.splits["validation"]
    if any(isinstance(index, bool) or not isinstance(index, (int, np.integer)) or index < 0 or index >= len(dataset.X)
           for index in [*train_indices, *validation_indices]):
        raise ValueError("classification split indices must be nonnegative in-range integers")
    if len(set(train_indices)) != len(train_indices) or len(set(validation_indices)) != len(validation_indices):
        raise ValueError("classification split indices must be unique")
    if set(train_indices) & set(validation_indices):
        raise ValueError("classification train and validation splits overlap")
    if (set(train_indices) | set(validation_indices)) & set(dataset.splits.get("test", [])):
        raise ValueError("classification train/validation overlaps the sealed test split")
    X_train, y_train, train_regimes, train_groups = dataset.split("train")
    X_valid, y_valid, regimes, groups = dataset.split("validation")
    if config.get("decision_mode") == "time_oof" and set(train_groups.astype(str)) & set(groups.astype(str)):
        raise ValueError("time_oof group overlap between train and validation")
    if dataset.manifest.get("independent_unit_kind") in PHYSICAL_BEARING_KINDS and set(train_groups) & set(groups):
        raise ValueError("physical bearing groups overlap between train and validation")
    if X_train.empty or X_valid.empty or not len(X_train.columns):
        raise ValueError("classification train and validation splits must be nonempty with features")
    if list(X_train) != dataset.features or list(X_valid) != dataset.features:
        raise ValueError("feature order differs from the fixed dataset contract")
    if y_train.isna().any() or y_valid.isna().any():
        raise ValueError("classification labels must not be missing")
    encoder = LabelEncoder().fit(y_train)
    if len(encoder.classes_) < 2:
        raise ValueError("classification training needs at least two classes in train")
    if not set(y_valid) <= set(encoder.classes_):
        raise ValueError("validation contains a class unseen in training")
    X_train = _normalise_missing_categories(X_train)
    X_valid = _normalise_missing_categories(X_valid)
    preprocessor, categorical = _preprocessor(X_train)
    train_matrix = preprocessor.fit_transform(X_train)
    valid_matrix = preprocessor.transform(X_valid)
    if not np.isfinite(train_matrix).all() or not np.isfinite(valid_matrix).all():
        raise ValueError("preprocessed classification features must be finite")
    target = encoder.transform(y_train)
    valid_target = encoder.transform(y_valid)
    history = {}
    curve = []
    mode = config.get("decision_mode", "native")
    decision_policy = {"mode": "native", "threshold_source": "model_default"}
    if mode == "time_oof":
        from rac_hpo.training.threshold import choose_time_oof_threshold
        decision_policy = choose_time_oof_threshold(
            dataset, train_indices,
            lambda past, future: _fold_probabilities(model_name, effective, config, seed, X_train,
                                                     target, past, future, acknowledge_cancel),
            n_splits=config["oof_folds"])
        timestamps = pd.to_datetime(dataset.manifest["timestamps"], errors="raise", utc=True)
        if timestamps[train_indices].max() >= timestamps[validation_indices].min():
            raise ValueError("time_oof requires validation timestamps strictly after training timestamps")
        decision_policy["fit_tree_budget"] = config["n_estimators"]
        decision_policy["early_stopping"] = "disabled for fixed-budget OOF and final fit"
    acknowledge_cancel()
    estimator = _estimator(model_name, effective, config, seed, len(encoder.classes_),
                           acknowledge_cancel, mode == "native")
    if model_name == "random_forest":
        # Check cancellation between bounded batches without inventing validation curves.
        _fit_forest(estimator, train_matrix, target, config, acknowledge_cancel)
        best_iteration = estimator.n_estimators
    elif model_name == "lightgbm":
        loss = "binary_logloss" if len(encoder.classes_) == 2 else "multi_logloss"
        callbacks = [acknowledge_cancel, lgb.record_evaluation(history)]
        if mode == "native":
            callbacks.append(lgb.early_stopping(config["early_stopping_rounds"], verbose=False))
        estimator.fit(train_matrix, target, eval_set=[(train_matrix, target), (valid_matrix, valid_target)],
                      eval_names=["train", "validation"], eval_metric=loss,
                      callbacks=callbacks)
        curve = [{"iteration": index + 1, "train_loss": float(a), "validation_loss": float(b)}
                 for index, (a, b) in enumerate(zip(history["train"][loss], history["validation"][loss]))]
        best_iteration = int(estimator.best_iteration_ or estimator.n_estimators_)
    else:
        loss = "logloss" if len(encoder.classes_) == 2 else "mlogloss"
        estimator.fit(train_matrix, target, eval_set=[(train_matrix, target), (valid_matrix, valid_target)], verbose=False)
        history = estimator.evals_result()
        curve = [{"iteration": index + 1, "train_loss": float(a), "validation_loss": float(b)}
                 for index, (a, b) in enumerate(zip(history["validation_0"][loss], history["validation_1"][loss]))]
        best_iteration = int(estimator.best_iteration + 1) if mode == "native" else config["n_estimators"]
        # Local callback classes cannot be joblib persisted; inference needs none.
        estimator.set_params(callbacks=None)
    acknowledge_cancel()
    model = ClassificationModel(preprocessor, estimator, encoder, list(dataset.features),
                                decision_policy.get("threshold"))
    evaluation_args = {"class_labels": encoder.classes_,
                       "group_kind": dataset.manifest.get("independent_unit_kind", "unknown"),
                       "bootstrap_seed": seed}
    metrics = evaluate(y_valid, model.predict(X_valid), model.predict_proba(X_valid), regimes, groups, **evaluation_args)
    metrics["train_metrics"] = evaluate(y_train, model.predict(X_train), model.predict_proba(X_train),
                                         train_regimes, train_groups, **evaluation_args)
    categories = {}
    if categorical:
        fitted_encoder = preprocessor.named_transformers_["categorical"].named_steps["encode"]
        categories = {name: [str(value) for value in values] for name, values in zip(categorical, fitted_encoder.categories_)}
    library = {"random_forest": "scikit-learn", "lightgbm": "lightgbm", "xgboost": "xgboost"}[model_name]
    metrics.update(model_name=model_name, model_version="classification-0.2.0" if mode == "time_oof" else "classification-0.1.0",
                   model_library=library, model_library_version=version(library),
                   seed=seed, learning_curve=curve,
                   decision_policy=decision_policy,
                   learning_curve_reason=None if curve else "random_forest does not expose a validation learning curve",
                   learning_curve_metric="log_loss" if curve else None,
                   preprocessing={"fit_split": "train", "numeric_imputation": "train median; empty train columns=0",
                                  "categorical_encoding": "train-only one-hot; unknown categories ignored",
                                  "categories": categories, "transformed_features": int(train_matrix.shape[1])},
                   data_origin=dataset.manifest.get("data_origin", dataset.manifest.get("synthetic", "unspecified")))
    acknowledge_cancel()
    return TrainingResult(params=effective, metrics=metrics, duration_seconds=perf_counter() - started,
                          model=model, best_iteration=best_iteration)
