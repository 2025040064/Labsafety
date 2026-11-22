from copy import deepcopy

import numpy as np
import pytest

from rac_hpo.domain.metrics import normalized_objective
from rac_hpo.domain.schemas import TrainingResult
from rac_hpo.optimization.base import DEFAULT_SEARCH_SPACE, optimize
from rac_hpo.optimization.rac_hpo.acquisition import acquisition_scores
from rac_hpo.optimization.rac_hpo.regime_priority import regime_priorities
from rac_hpo.optimization.rac_hpo.search import RACSearchPolicy, RACSettings
from rac_hpo.optimization.rac_hpo.surrogate import TrialSurrogate, decode, encode


def config(**overrides):
    return {"algorithm": "rac_hpo", "seed": 42, "max_trials": 3, "timeout_seconds": 30,
            "baseline_errors": {"a": 1.0, "b": 1.0}, "weights": {"a": 0.5, "b": 0.5}, **overrides}


def metric_result(params, number):
    a, b = 0.7 + params["learning_rate"], 0.8 + params["feature_fraction"] / 10
    return TrainingResult(params, {"overall_mae": (a + b) / 2, "regimes": {
        "a": {"mae": a, "samples": 10, "independent_groups": 2},
        "b": {"mae": b, "samples": 10, "independent_groups": 2}}}, 0.02)


def test_rac_warmup_reuses_real_runner_and_fixed_j():
    calls, events = [], []
    def runner(params, number):
        calls.append(number)
        return metric_result(params, number)
    result = optimize(config(), runner, events.append)
    assert calls == [0, 1, 2]
    assert result.status == "completed"
    assert all(trial["selection"]["reason"] == "insufficient_history" for trial in result.trials)
    assert all(trial["objective"] == trial["metrics"]["robust_objective"] for trial in result.trials)
    assert len(events) == 3


def test_encode_decode_preserves_configured_ranges():
    rng = np.random.default_rng(91)
    for _ in range(100):
        params = decode(rng.uniform(-1, 2, 6), DEFAULT_SEARCH_SPACE)
        vector = encode(params, DEFAULT_SEARCH_SPACE)
        assert (vector >= 0).all() and (vector <= 1).all()
        for key, value in params.items():
            assert DEFAULT_SEARCH_SPACE[key]["low"] <= value <= DEFAULT_SEARCH_SPACE[key]["high"]
            if DEFAULT_SEARCH_SPACE[key]["type"] == "int":
                assert isinstance(value, int)


def test_cost_and_credible_regime_priority_change_candidate_order():
    members = np.repeat(np.array([[[0.5, 1.9]], [[0.95, 1.0]]]), 4, axis=1)
    no_attention = acquisition_scores(members, [1, 1], 1.19, [1, 2], [0.9, 0.1], 0.1, [0, 0])
    attention = acquisition_scores(members, [1, 1], 1.19, [1, 2], [0.9, 0.1], 0.1, [0, 0.5])
    assert np.argmax(no_attention) == 0
    assert np.argmax(attention) == 1
    cost_penalty = acquisition_scores(np.repeat(members[:1], 2, axis=0), [1, 100], 1.19,
                                      [1, 2], [0.9, 0.1], 0.1, [0, 0])
    assert cost_penalty[0] > cost_penalty[1]
    assert np.allclose(normalized_objective(members, [0.9, 0.1], 0.1).mean(axis=1), [0.766, 0.9595])


def test_priority_never_treats_dates_or_two_tools_as_credible():
    metric = {"regimes": {"a": {"mae": 4, "effective_independent_groups": None},
                          "b": {"mae": 3, "effective_independent_groups": 2}}}
    result = regime_priorities(metric, {"a": 1, "b": 1}, {"a": 0.5, "b": 0.5})
    assert all(item["q"] == 0 for item in result.values())
    reliable = {"regimes": {"a": {"mae": 4, "effective_independent_groups": 8,
        "group_errors": [{"samples": 10, "absolute_error_sum": 40} for _ in range(8)]}}}
    before = deepcopy(reliable)
    result = regime_priorities(reliable, {"a": 1}, {"a": 1}, q_max=0.3)
    assert result["a"]["q"] == 0.3
    assert reliable == before


def history():
    result = optimize(config(max_trials=20), metric_result)
    return result.trials


def test_surrogate_validation_only_uses_earlier_trials():
    trials = history()
    model = TrialSurrogate(trials, DEFAULT_SEARCH_SPACE, {"a": 1, "b": 1}, {"a": 0.5, "b": 0.5},
                           0.3, 1e-8, RACSettings(), 42)
    diagnostics = model.forward_check()
    assert len(diagnostics["forward_splits"]) == 5
    assert all(split["training_trials"] == split["held_out_trial_position"] for split in diagnostics["forward_splits"])
    assert "uncalibrated" in diagnostics["uncertainty_kind"]
    model.fit()
    members, costs = model.predict(model.X[:3])
    assert members.shape == (3, 48, 2)
    assert (costs > 0).all()


def test_single_regime_surrogate_uses_one_dimensional_target_without_warning():
    import warnings
    from sklearn.exceptions import DataConversionWarning
    model = TrialSurrogate(history(), DEFAULT_SEARCH_SPACE, {"a": 1}, {"a": 1},
                           .3, 1e-8, RACSettings(), 42)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DataConversionWarning)
        model.forward_check()
        model.fit()
    members, costs = model.predict(model.X[:3])
    assert members.shape == (3, 48, 1)
    assert (costs > 0).all()


def test_surrogate_draws_include_forward_residual_error():
    trials = history()
    for index, trial in enumerate(trials):
        trial["params"] = deepcopy(trials[0]["params"])
        trial["metrics"]["regimes"]["a"]["mae"] = 0.2 + index % 3
        trial["metrics"]["regimes"]["b"]["mae"] = 0.3 + index % 4
    model = TrialSurrogate(trials, DEFAULT_SEARCH_SPACE, {"a": 1, "b": 1}, {"a": 0.5, "b": 0.5},
                           0.3, 1e-8, RACSettings(), 42)
    model.forward_check()
    model.fit()
    draws, _ = model.predict(model.X[:1])
    assert draws[0, :, 0].std() > 0.1, "Averaging trees alone cannot represent forward prediction error"


def test_bad_proxy_falls_back_without_claiming_rac_selection(monkeypatch):
    monkeypatch.setattr(TrialSurrogate, "forward_check", lambda self: {"passed": False, "reason": "surrogate_quality_failed"})
    policy = RACSearchPolicy(config(rac_hpo={"exploration_fraction": 1e-10}), DEFAULT_SEARCH_SPACE)
    params, diagnostic = policy.propose(history())
    assert params is None
    assert diagnostic["method"] == "tpe_fallback"
    assert diagnostic["reason"] == "surrogate_quality_failed"


def test_surrogate_exception_falls_back_to_real_runner(monkeypatch):
    def broken(self):
        raise RuntimeError("surrogate diagnostic failed")
    monkeypatch.setattr(TrialSurrogate, "forward_check", broken)
    result = optimize(config(max_trials=20, rac_hpo={"exploration_fraction": 1e-10}), metric_result)
    assert result.status == "completed"
    assert all(trial["selection"]["reason"] == "surrogate_error" for trial in result.trials[16:])


def test_rac_cancellation_retains_completed_history():
    cancelled = [False]
    def progress(event):
        if event["trial"]["number"] == 17:
            cancelled[0] = True
    result = optimize(config(max_trials=50), metric_result, progress, lambda: cancelled[0])
    assert result.status == "cancelled"
    assert len(result.trials) == 18
    assert result.best_trial is not None


def test_good_proxy_really_ranks_candidates_and_preserves_fixed_objective(monkeypatch):
    monkeypatch.setattr(TrialSurrogate, "forward_check", lambda self: {"passed": True, "reason": "passed"})
    policy = RACSearchPolicy(config(rac_hpo={"exploration_fraction": 1e-10}), DEFAULT_SEARCH_SPACE)
    params, diagnostic = policy.propose(history())
    assert params is not None
    assert diagnostic["method"] == "rac_acquisition"
    assert diagnostic["predicted_cost_seconds"] > 0
    assert all(item["q"] == 0 for item in diagnostic["regime_priorities"].values())


def test_surrogate_is_reused_for_four_trials_with_recorded_age(monkeypatch):
    monkeypatch.setattr(TrialSurrogate, "forward_check", lambda self: {"passed": True, "reason": "passed"})
    original = TrialSurrogate.fit
    fits = []
    def counted(self):
        fits.append(len(self.X))
        return original(self)
    monkeypatch.setattr(TrialSurrogate, "fit", counted)
    trials = history()
    fits.clear()
    policy = RACSearchPolicy(config(rac_hpo={"exploration_fraction": 1e-10}), DEFAULT_SEARCH_SPACE)
    policy.propose(trials[:16])
    _, diagnostic = policy.propose(trials[:17])
    assert fits == [16]
    assert diagnostic["surrogate_age_trials"] == 1
    policy.propose(trials[:20])
    assert fits == [16, 20]


@pytest.mark.parametrize("options", [{"candidates": 0}, {"exploration_fraction": 0},
    {"forward_holdout": 2}, {"minimum_history": 5}, {"gamma": -1}, {"t0": 0},
    {"q_max": 2}, {"minimum_rank_correlation": 2}])
def test_rac_settings_reject_invalid_values(options):
    with pytest.raises(ValueError):
        RACSettings(**options)
