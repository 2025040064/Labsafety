"""Deterministic unit fixtures here are not manufacturing experiment results."""

import math

import pytest

from rac_hpo.domain.metrics import robust_metrics
from rac_hpo.evaluation.evaluator import evaluate
from rac_hpo.optimization.robust_objective import apply_robust_metrics


def test_overall_and_raw_regime_metrics():
    metrics = evaluate([1, 2, 3, 4], [0, 2, 1, 0], ["a", "a", "b", "b"], ["day1", "day1", "day2", "day3"])
    assert metrics["overall_mae"] == pytest.approx(1.75)
    assert metrics["regimes"]["a"]["mae"] == pytest.approx(0.5)
    assert metrics["regimes"]["b"]["mae"] == pytest.approx(3.0)
    assert metrics["regimes"]["a"]["independent_groups"] == 1
    assert metrics["regimes"]["a"]["samples"] == 2
    assert metrics["regimes"]["a"]["reliability"] == "insufficient_independent_groups"
    assert metrics["robust_objective"] is None


def test_fixed_objective_matches_hand_calculation():
    regimes = {"a": {"mae": 2.0, "samples": 3}, "b": {"mae": 6.0, "samples": 1}}
    result = robust_metrics(regimes, {"a": 1.0, "b": 2.0}, alpha=0.3, epsilon=1e-8)
    a, b = 2 / (1 + 1e-8), 6 / (2 + 1e-8)
    assert result["robust_objective"] == pytest.approx(0.7 * (0.75 * a + 0.25 * b) + 0.3 * b)
    assert result["worst_normalized_mae"] == pytest.approx(b)
    assert result["robustness"]["weights"] == {"a": 0.75, "b": 0.25}


def test_explicit_business_weights_are_normalized_and_formula_is_shared():
    raw = evaluate([2, 6], [0, 0], ["a", "b"], [1, 2])
    scored = apply_robust_metrics(raw, {"a": 1, "b": 2}, alpha=0.0, weights={"a": 1, "b": 3})
    assert raw["robust_objective"] is None
    assert scored["robust_objective"] == pytest.approx(2.75, abs=1e-7)
    direct = evaluate([2, 6], [0, 0], ["a", "b"], [1, 2], {"a": 1, "b": 2}, 0.0, {"a": 1, "b": 3})
    assert direct == scored


def test_zero_baseline_is_finite_and_flagged():
    result = robust_metrics({"a": {"mae": 1.0, "samples": 1}}, {"a": 0})
    assert math.isfinite(result["robust_objective"])
    assert result["robust_objective"] == pytest.approx(1e8)
    assert result["robustness"]["near_zero_baseline_regimes"] == ["a"]


@pytest.mark.parametrize("kwargs", [
    {"baseline_errors": {"b": 1}}, {"baseline_errors": {"a": -1}},
    {"baseline_errors": {"a": float("nan")}}, {"weights": {"b": 1}},
    {"weights": {"a": 0}}, {"weights": {"a": -1}}, {"alpha": -0.1},
    {"alpha": 1.1}, {"epsilon": 0}, {"epsilon": float("inf")},
])
def test_fixed_objective_rejects_invalid_contract(kwargs):
    arguments = {"baseline_errors": {"a": 1}, **kwargs}
    with pytest.raises(ValueError):
        robust_metrics({"a": {"mae": 1, "samples": 1}}, **arguments)


@pytest.mark.parametrize("truth,predictions,regimes,groups", [
    ([], [], [], []), ([1], [1, 2], ["a"], [1]),
    ([float("nan")], [1], ["a"], [1]), ([1], [float("inf")], ["a"], [1]),
    ([1], [1], [None], [1]), ([1], [1], ["a"], [None]),
    ([1], [1], [""], [1]), ([[1]], [[1]], ["a"], [1]),
])
def test_evaluation_rejects_missing_or_invalid_inputs(truth, predictions, regimes, groups):
    with pytest.raises(ValueError):
        evaluate(truth, predictions, regimes, groups)
