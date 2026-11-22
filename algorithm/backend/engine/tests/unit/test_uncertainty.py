import numpy as np

from rac_hpo.evaluation.evaluator import evaluate


def test_correlated_date_blocks_do_not_produce_independent_confidence():
    metric = evaluate(np.zeros(10), np.arange(10), ["a"] * 10, list(range(10)), group_kind="calendar_blocks")
    assert metric["regimes"]["a"]["effective_independent_groups"] is None
    assert metric["regimes"]["a"]["mae_interval"] is None


def test_bootstrap_resamples_tools_and_small_tools_have_no_interval():
    metric = evaluate(np.zeros(20), np.arange(20), ["a"] * 20, np.repeat(range(5), 4), group_kind="physical_tool")
    item = metric["regimes"]["a"]
    assert item["effective_independent_groups"] == 5
    assert len(item["group_errors"]) == 5
    assert item["mae_interval"][0] < item["mae"] < item["mae_interval"][1]
    small = evaluate([0, 0, 0], [1, 2, 3], ["a"] * 3, [1, 1, 2], group_kind="physical_tool")
    assert small["regimes"]["a"]["reliability"] == "insufficient_independent_groups"
    assert small["regimes"]["a"]["mae_interval"] is None
