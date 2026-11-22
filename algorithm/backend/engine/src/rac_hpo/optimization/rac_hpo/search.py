"""RAC-HPO decisions; the common optimizer remains responsible for real trials."""
from dataclasses import asdict, dataclass
import math

import numpy as np

from rac_hpo.domain.metrics import normalized_objective
from .acquisition import acquisition_scores
from .regime_priority import regime_priorities
from .surrogate import TrialSurrogate, decode, encode


@dataclass(frozen=True)
class RACSettings:
    warmup_trials: int = 16
    minimum_history: int = 16
    forward_holdout: int = 5
    min_forward_train: int = 8
    surrogate_trees: int = 48
    candidates: int = 128
    refit_interval: int = 4
    exploration_fraction: float = 0.2
    local_fraction: float = 0.5
    beta: float = 0.5
    gamma: float = 0.5
    t0: float = 0.01
    q_max: float = 0.5
    shrinkage_k: float = 5.0
    weakness_threshold: float = 1.0
    minimum_rank_correlation: float = 0.0
    maximum_mae_ratio: float = 1.25
    maximum_overall_degradation: float = 0.1

    def __post_init__(self):
        for name in ("warmup_trials", "minimum_history", "forward_holdout", "min_forward_train", "surrogate_trees", "candidates", "refit_interval"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"RAC {name} must be a positive integer")
        if self.forward_holdout < 3 or self.min_forward_train < 5 or self.minimum_history < self.min_forward_train + self.forward_holdout:
            raise ValueError("RAC needs at least three forward holdouts after five training trials")
        if self.surrogate_trees < 8 or self.candidates < 8:
            raise ValueError("RAC needs at least eight trees and eight candidates")
        for name, value in asdict(self).items():
            if not math.isfinite(value):
                raise ValueError(f"RAC {name} must be finite")
        for name in ("exploration_fraction", "local_fraction", "q_max"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"RAC {name} must be within [0, 1]")
        if not 0 < self.exploration_fraction < 1:
            raise ValueError("RAC must retain a nonzero global exploration quota")
        if not -1 <= self.minimum_rank_correlation <= 1:
            raise ValueError("RAC ranking threshold must be within [-1, 1]")
        for name in ("beta", "gamma", "t0", "shrinkage_k", "weakness_threshold", "maximum_mae_ratio", "maximum_overall_degradation"):
            if getattr(self, name) < 0 or (name in {"t0", "shrinkage_k", "maximum_mae_ratio"} and getattr(self, name) == 0):
                raise ValueError(f"RAC invalid {name}")


class RACSearchPolicy:
    def __init__(self, config, space):
        self.config, self.space = config, space
        self.settings = RACSettings(**config.get("rac_hpo", {}))
        self.seed = config.get("seed", 42)
        self.rng = np.random.default_rng(self.seed)
        self.baseline = config["baseline_errors"]
        self.regimes = sorted(self.baseline)
        weights = config.get("weights")
        if not weights:
            raise ValueError("RAC requires frozen regime weights before search")
        self.weights = weights
        self._cached_at = None
        self._cached_surrogate = None
        self._cached_forward = None

    def propose(self, history, cancel_check=None):
        completed = [trial for trial in history if trial["status"] == "completed"]
        diagnostics = {"method": "tpe_fallback", "completed_history": len(completed),
                       "algorithm_version": "rac-hpo-0.2.0", "predicted_cost_seconds": None}
        if len(completed) < max(self.settings.warmup_trials, self.settings.minimum_history):
            return None, {**diagnostics, "reason": "insufficient_history"}
        candidate_seed = int(self.rng.integers(0, 2**31 - 1))
        rng = np.random.default_rng(candidate_seed)
        if self.rng.random() < self.settings.exploration_fraction:
            candidate = decode(rng.random(len(self.space)), self.space)
            return candidate, {**diagnostics, "method": "global_exploration", "reason": "global_quota",
                               "candidate_seed": candidate_seed}
        if cancel_check and cancel_check():
            return None, {**diagnostics, "reason": "cancellation_requested"}
        if self._cached_at is None or len(completed) - self._cached_at >= self.settings.refit_interval:
            surrogate = TrialSurrogate(completed, self.space, self.baseline, self.weights,
                                       self.config.get("alpha", 0.3), self.config.get("epsilon", 1e-8),
                                       self.settings, self.seed)
            forward = surrogate.forward_check()
            self._cached_at, self._cached_forward = len(completed), forward
            self._cached_surrogate = surrogate.fit() if forward["passed"] else None
        forward = self._cached_forward
        surrogate = self._cached_surrogate
        diagnostics.update(forward_validation=forward, candidate_seed=candidate_seed,
                           surrogate_fit_history=self._cached_at,
                           surrogate_age_trials=len(completed) - self._cached_at)
        if not forward["passed"]:
            return None, {**diagnostics, "reason": forward["reason"]}
        incumbent = min(completed, key=lambda trial: trial["objective"])
        priority = regime_priorities(incumbent["metrics"], self.baseline, self.weights,
                                    minimum_groups=self.config.get("minimum_independent_groups", 5),
                                    shrinkage_k=self.settings.shrinkage_k,
                                    threshold=self.settings.weakness_threshold, q_max=self.settings.q_max,
                                    epsilon=self.config.get("epsilon", 1e-8), seed=self.seed)
        pool = rng.random((self.settings.candidates, len(self.space)))
        local_count = int(self.settings.candidates * self.settings.local_fraction)
        good_trials = sorted(completed, key=lambda trial: trial["objective"])[:5]
        for index in range(local_count):
            center = encode(good_trials[index % len(good_trials)]["params"], self.space)
            pool[index] = np.clip(center + rng.normal(0, 0.15, len(center)), 0, 1)
        candidates = [decode(vector, self.space) for vector in pool]
        encoded = np.array([encode(candidate, self.space) for candidate in candidates])
        members, costs = surrogate.predict(encoded)
        weights = np.array([self.weights[regime] for regime in self.regimes], dtype=float)
        weights /= weights.sum()
        current = [incumbent["metrics"]["regimes"][regime]["mae"] / surrogate.baselines[index]
                   for index, regime in enumerate(self.regimes)]
        attention = [priority[regime]["q"] for regime in self.regimes]
        scores = acquisition_scores(members, costs, incumbent["objective"], current,
                                    weights, self.config.get("alpha", 0.3), attention,
                                    self.settings.beta, self.settings.gamma, self.settings.t0)
        counts = np.array([incumbent["metrics"]["regimes"][regime]["samples"] for regime in self.regimes])
        sample_weights = counts / counts.sum()
        predicted_raw = members.mean(axis=1) * surrogate.baselines
        predicted_overall = predicted_raw @ sample_weights
        baseline_overall = self.config.get("baseline_overall_mae", float(surrogate.baselines @ sample_weights))
        allowed = predicted_overall <= baseline_overall * (1 + self.settings.maximum_overall_degradation)
        historical = {tuple(encode(trial["params"], self.space).round(12)) for trial in completed}
        allowed &= np.array([tuple(vector.round(12)) not in historical for vector in encoded])
        if not allowed.any():
            return None, {**diagnostics, "reason": "no_candidate_passed_predicted_overall_guard", "regime_priorities": priority}
        scores[~allowed] = -np.inf
        selected = int(np.argmax(scores))
        predicted_j = float(normalized_objective(members[selected].mean(axis=0), weights, self.config.get("alpha", 0.3)))
        return candidates[selected], {
            **diagnostics, "method": "rac_acquisition", "reason": "forward_quality_passed",
            "regime_priorities": priority, "candidate_count": len(candidates),
            "eligible_candidate_count": int(allowed.sum()), "acquisition": float(scores[selected]),
            "predicted_cost_seconds": float(costs[selected]), "predicted_objective": predicted_j,
            "predicted_overall_mae": float(predicted_overall[selected]),
            "predicted_regime_mae": {regime: float(predicted_raw[selected, index]) for index, regime in enumerate(self.regimes)},
            "overall_guard_kind": "predictive screening, not a calibrated hard guarantee",
            "predictive_draws": "uncalibrated tree dispersion plus centered forward-residual bootstrap",
        }
