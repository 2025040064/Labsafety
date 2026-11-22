"""Search and audit contracts requested by the summary document."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from rac_hpo.domain.schemas import TrainingResult
from rac_hpo.optimization.base import DEFAULT_SEARCH_SPACE, optimize


def result(value=1):
    return TrainingResult(params={}, metrics={"overall_mae": value}, duration_seconds=.01)


def test_grid_exhausts_without_duplicates_and_obeys_steps():
    space = deepcopy(DEFAULT_SEARCH_SPACE)
    for spec in space.values():
        spec["high"] = spec["low"]
    space["num_leaves"] = {"type": "int", "low": 8, "high": 12, "step": 2}
    run = optimize({"algorithm": "grid", "search_space": space, "max_trials": 20},
                   lambda params, number: result(params["num_leaves"]))
    assert run.status == "completed"
    assert [item["params"]["num_leaves"] for item in run.trials] == [8, 10, 12]
    assert run.best_value == 8


@pytest.mark.parametrize("algorithm", ["successive_halving", "hyperband"])
def test_resource_search_prunes_and_repeats_deterministically(algorithm):
    def run():
        def runner(params, number, report):
            for step in range(1, 28):
                report(step, float(number + 1))
            return result(number + 1)
        return optimize({"algorithm": algorithm, "max_trials": 24,
                         "max_resource": 27, "min_resource": 3}, runner)
    first, second = run(), run()
    assert first.status == "completed"
    assert any(item["status"] == "pruned" for item in first.trials)
    assert [(x["params"], x["status"], x["resource"]) for x in first.trials] == [
        (x["params"], x["status"], x["resource"]) for x in second.trials]
    assert first.best_trial == 0
    for item in first.trials:
        if item["status"] == "pruned":
            assert item["objective"] is None
            assert item["resource"]["last_step"] < 27


def test_extreme_audit_fits_train_only_and_preserves_rows():
    from rac_hpo.data.quality_audit import audit_features
    frame = pd.DataFrame({"signal": [0., 1., 2., 3., 100., np.nan, 1e9]})
    before = frame.copy(deep=True)
    audit = audit_features(frame, [0, 1, 2, 3, 4], [4, 5], source_rows=list(range(10, 17)))
    assert audit["fit_partition"] == "train"
    assert audit["flagged_source_rows"] == [14, 15]
    assert audit["features"]["signal"]["upper"] == 6
    assert audit["policy"] == "flag_only_preserve_rows"
    pd.testing.assert_frame_equal(frame, before)


def test_classification_reports_fault_misses_and_false_alarms():
    from rac_hpo.evaluation.classification import evaluate
    metrics = evaluate([0, 0, 0, 1, 1], [0, 0, 1, 0, 1],
                       [[.8, .2], [.9, .1], [.3, .7], [.7, .3], [.2, .8]], class_labels=[0, 1])
    assert metrics["class_false_negative_rate"]["1"] == .5
    assert metrics["class_false_positive_rate"]["1"] == pytest.approx(1 / 3)
    assert metrics["regimes"]["all"]["class_false_negative_rate"]["1"] == .5


@pytest.mark.parametrize("algorithm", ["successive_halving", "hyperband"])
def test_resource_report_rejects_invalid_steps_and_acknowledges_cancellation(algorithm):
    from rac_hpo.domain.schemas import TaskCancelled
    def invalid(params, number, report):
        report(1000, 1.)
        return result()
    run = optimize({"algorithm": algorithm, "max_trials": 1, "max_resource": 30}, invalid)
    assert run.status == "failed"
    assert "resource steps" in run.trials[0]["failure_reason"]
    def cancelled(params, number, report):
        raise TaskCancelled("cancel fixture")
    run = optimize({"algorithm": algorithm, "max_trials": 2}, cancelled)
    assert run.status == "cancelled"
    assert len(run.trials) == 1


@pytest.mark.parametrize("controls", [{"min_resource": 0}, {"reduction_factor": 1},
                                      {"max_resource": 5, "min_resource": 10}])
def test_invalid_resource_controls_fail_before_training(controls):
    with pytest.raises(ValueError):
        optimize({"algorithm": "hyperband", **controls},
                 lambda *args: pytest.fail("invalid controls must not train"))
