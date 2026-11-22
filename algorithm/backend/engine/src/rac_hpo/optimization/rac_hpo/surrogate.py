"""ExtraTrees trial-history models and strictly chronological forward checks."""
import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import ExtraTreesRegressor

from rac_hpo.domain.metrics import normalized_objective


def encode(params, space):
    values = []
    for name, specification in space.items():
        value, low, high = params[name], specification["low"], specification["high"]
        if specification.get("log"):
            value, low, high = np.log(value), np.log(low), np.log(high)
        values.append(0.0 if high == low else (value - low) / (high - low))
    return np.array(values, dtype=float)


def decode(vector, space):
    result = {}
    for position, (name, specification) in enumerate(space.items()):
        low, high = specification["low"], specification["high"]
        fraction = float(np.clip(vector[position], 0, 1))
        value = np.exp(np.log(low) + fraction * (np.log(high) - np.log(low))) if specification.get("log") else low + fraction * (high - low)
        if "step" in specification:
            value = low + round((value - low) / specification["step"]) * specification["step"]
        if specification["type"] == "int":
            value = int(round(value))
        result[name] = max(low, min(high, value))
    return result


class TrialSurrogate:
    def __init__(self, history, space, baseline_errors, weights, alpha, epsilon, settings, seed):
        self.regimes = sorted(baseline_errors)
        self.baselines = np.array([baseline_errors[regime] + epsilon for regime in self.regimes])
        raw_weights = np.array([weights[regime] for regime in self.regimes], dtype=float)
        self.weights = raw_weights / raw_weights.sum()
        self.alpha = alpha
        self.X = np.array([encode(trial["params"], space) for trial in history])
        self.Y = np.array([[trial["metrics"]["regimes"][regime]["mae"] for regime in self.regimes]
                           for trial in history]) / self.baselines
        self.costs = np.log(np.maximum([trial["duration_seconds"] for trial in history], 1e-6))
        self.settings, self.seed = settings, seed
        self.model = None
        self.cost_model = None
        self.forward_residuals = None

    def _model(self, trees=None):
        return ExtraTreesRegressor(n_estimators=trees or self.settings.surrogate_trees,
                                   min_samples_leaf=2, random_state=self.seed, n_jobs=1)

    def forward_check(self):
        predicted, truth, naive, coverages, prefixes = [], [], [], [], []
        first = max(self.settings.min_forward_train, len(self.X) - self.settings.forward_holdout)
        for index in range(first, len(self.X)):
            target = self.Y[:index, 0] if self.Y.shape[1] == 1 else self.Y[:index]
            model = self._model(trees=24).fit(self.X[:index], target)
            members = np.array([np.atleast_1d(tree.predict(self.X[index:index + 1])[0]) for tree in model.estimators_])
            mean, spread = members.mean(axis=0), members.std(axis=0)
            predicted.append(mean)
            truth.append(self.Y[index])
            naive.append(self.Y[:index].mean(axis=0))
            coverages.append(np.abs(self.Y[index] - mean) <= 1.96 * spread)
            prefixes.append({"training_trials": index, "held_out_trial_position": index})
        if len(predicted) < 3:
            return {"passed": False, "reason": "insufficient_forward_holdouts", "forward_splits": prefixes}
        predicted, truth, naive = np.array(predicted), np.array(truth), np.array(naive)
        self.forward_residuals = truth - predicted
        error = float(np.abs(predicted - truth).mean())
        naive_error = float(np.abs(naive - truth).mean())
        scores = normalized_objective(predicted, self.weights, self.alpha)
        observed = normalized_objective(truth, self.weights, self.alpha)
        correlation = float(spearmanr(scores, observed).statistic) if np.ptp(scores) and np.ptp(observed) else None
        passed = (correlation is not None and np.isfinite(correlation)
                  and correlation >= self.settings.minimum_rank_correlation
                  and error <= self.settings.maximum_mae_ratio * max(naive_error, 1e-12))
        return {"passed": bool(passed), "reason": "passed" if passed else "surrogate_quality_failed",
                "normalized_mae": error, "naive_normalized_mae": naive_error,
                "rank_correlation": correlation, "approximate_tree_band_coverage": float(np.mean(coverages)),
                "uncertainty_kind": "uncalibrated tree dispersion; coverage is diagnostic only",
                "forward_normalized_residuals": self.forward_residuals.tolist(),
                "forward_splits": prefixes}

    def fit(self):
        target = self.Y[:, 0] if self.Y.shape[1] == 1 else self.Y
        self.model = self._model().fit(self.X, target)
        self.cost_model = self._model().fit(self.X, self.costs)
        return self

    def predict(self, candidates):
        # Shape: candidates x trees x regimes. Tree dispersion is an approximation.
        members = np.stack([np.atleast_2d(tree.predict(candidates)).reshape(len(candidates), -1)
                            for tree in self.model.estimators_], axis=1)
        if self.forward_residuals is not None:
            # Search-only predictive draws: paired regime residual vectors retain
            # observed cross-regime dependence, without claiming calibrated CIs.
            rng = np.random.default_rng(self.seed + len(self.X))
            indices = rng.integers(0, len(self.forward_residuals), size=members.shape[:2])
            residuals = self.forward_residuals - self.forward_residuals.mean(axis=0)
            members = np.maximum(0, members + residuals[indices])
        costs = np.exp(np.clip(self.cost_model.predict(candidates), -20, 20))
        return members, costs
