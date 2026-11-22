"""Fair benchmark summaries must never select a convenient seed subset."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


_path = Path(__file__).resolve().parents[2] / "scripts/run_industrial_benchmarks.py"
_spec = importlib.util.spec_from_file_location("industrial_benchmark", _path)
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)


def campaign_fixture():
    config = {"datasets": ["secom"], "seeds": [42, 71], "trials": 2, "seconds": 30,
              "estimators": 8, "threads": 1, "skip_baselines": True}
    resources = {"n_estimators": 8, "n_jobs": 1, "early_stopping_rounds": 20}
    runs = []
    for seed in config["seeds"]:
        for algorithm in ("random", "tpe", "rac_hpo"):
            runs.append({
                "id": f"fixture-{algorithm}-{seed}", "dataset_key": "secom", "task_type": "classification",
                "model_name": "lightgbm", "algorithm": algorithm, "seed": seed,
                "status": "completed", "data_sha256": "source-hash", "split_fingerprint": "split-hash",
                "completed_trials": 2, "duration_seconds": 1.0, "search_seconds": .8,
                "config": {"model_config": deepcopy(resources), "seed": seed, "max_trials": 2,
                           "timeout_seconds": 30, "evaluation_partition": "validation", "test_accessed": False},
                "budget": {"max_trials": 2, "timeout_seconds": 30, "model_resources": deepcopy(resources),
                           "baseline_in_search_budget": False, "running_trial_can_overrun_deadline": True},
                "objective_definition": {"name": "fixed_baseline_normalized_j", "alpha": .3},
                "search_space": {"learning_rate": {"low": .01, "high": .2}},
                "baseline_errors": {"all": .4 + seed / 1000}, "weights": {"all": 1.0},
                "metrics": {"overall_balanced_accuracy": .7, "overall_f1_macro": .6,
                            "overall_log_loss": .5, "overall_error": .3, "robust_objective": .8},
            })
    return {"config": config, "runs": runs, "failures": []}


def test_complete_paired_campaign_is_fair_and_baseline_is_separate():
    campaign = campaign_fixture()
    baseline = deepcopy(campaign["runs"][0])
    baseline.update(algorithm="baseline", objective_definition="balanced error", completed_trials=0)
    campaign["runs"].append(baseline)
    result = benchmark.summarize_campaign(campaign)
    assert len(result["fair_comparisons"]) == 1
    assert result["fair_comparisons"][0]["seeds"] == [42, 71]
    assert len(result["summary"]) == 3
    assert all(entry["descriptive_only"] for entry in result["summary"])
    assert len(result["baseline_summary"]) == 1
    assert not result["noncomparable"]


@pytest.mark.parametrize("change,code", [
    ("missing_seed", "missing_run"), ("failed_seed", "run_not_completed"),
    ("short_budget", "incomplete_trial_budget"), ("duplicate_seed", "duplicate_run"),
    ("source", "source_mismatch"), ("split", "split_mismatch"),
    ("budget", "budget_mismatch"), ("resources", "resources_mismatch"),
    ("objective", "objective_mismatch"), ("search_space", "search_space_mismatch"),
    ("baseline", "baseline_mismatch"), ("missing_metrics", "missing_or_invalid_metrics"),
])
def test_missing_changed_or_failed_evidence_never_generates_fair_means(change, code):
    campaign = campaign_fixture()
    last = campaign["runs"][-1]
    if change == "missing_seed":
        campaign["runs"].pop()
    elif change == "failed_seed":
        last["status"] = "failed"
    elif change == "short_budget":
        last["completed_trials"] = 1
    elif change == "duplicate_seed":
        campaign["runs"].append(deepcopy(last))
    elif change == "source":
        last["data_sha256"] = "changed"
    elif change == "split":
        last["split_fingerprint"] = "changed"
    elif change == "budget":
        last["budget"]["timeout_seconds"] = 31
    elif change == "resources":
        last["budget"]["model_resources"]["n_jobs"] = 2
    elif change == "objective":
        last["objective_definition"]["alpha"] = .9
    elif change == "search_space":
        last["search_space"]["learning_rate"]["high"] = .8
    elif change == "baseline":
        last["baseline_errors"]["all"] += .1
    elif change == "missing_metrics":
        last["metrics"].pop("robust_objective")
    result = benchmark.summarize_campaign(campaign)
    assert result["fair_comparisons"] == []
    assert code in {reason["code"] for item in result["noncomparable"] for reason in item["reasons"]}
    assert all(entry["descriptive_only"] for entry in result["summary"])


def test_audit_failure_and_absent_runs_are_explicitly_noncomparable():
    campaign = campaign_fixture()
    campaign["runs"] = []
    campaign["failures"] = [{"dataset_key": "secom", "stage": "audit", "error": "missing data"}]
    result = benchmark.summarize_campaign(campaign)
    assert not result["fair_comparisons"]
    assert result["noncomparable"]
    assert "acquisition_or_audit_failure" in {reason["code"] for item in result["noncomparable"] for reason in item["reasons"]}


def test_campaign_row_keeps_actual_controls_and_excludes_baseline_from_trial_budget():
    detail = deepcopy(campaign_fixture()["runs"][0])
    detail.update(manifest={"split_fingerprint": "split-hash", "data_origin": "test_fixture"},
                  search_duration_seconds=.8,
                  trials=[{"number": -1, "status": "completed"},
                          {"number": 0, "status": "completed"}, {"number": 1, "status": "completed"}])
    row = benchmark.campaign_row(detail)
    assert row["completed_trials"] == 2
    assert row["completed_trial_numbers"] == [0, 1]
    assert row["budget"] == detail["budget"]
    assert row["config"] == detail["config"]
    assert row["objective_definition"] == detail["objective_definition"]
    detail["budget"]["model_resources"]["n_jobs"] = 9
    assert row["budget"]["model_resources"]["n_jobs"] == 1


def test_main_persists_verified_fair_summary_from_complete_service_records(tmp_path, monkeypatch):
    campaign = campaign_fixture()
    class Service:
        def __init__(self, root):
            assert root == tmp_path
        def audit(self, dataset_key):
            return {"dataset_key": dataset_key, "data_origin": "test_fixture"}
        def run(self, dataset_key, **kwargs):
            detail = deepcopy(next(run for run in campaign["runs"] if run["algorithm"] == kwargs["algorithm"]
                                   and run["seed"] == kwargs["seed"]))
            detail.update(manifest={"split_fingerprint": "split-hash", "data_origin": "test_fixture"},
                          trials=[{"number": -1, "status": "completed"},
                                  {"number": 0, "status": "completed"}, {"number": 1, "status": "completed"}])
            return detail
    monkeypatch.setattr(benchmark, "IndustrialService", Service)
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    monkeypatch.setattr(benchmark.sys, "argv", ["benchmark", "--datasets", "secom", "--seeds", "42", "71",
                        "--trials", "2", "--seconds", "30", "--estimators", "8", "--threads", "1", "--skip-baselines"])
    assert benchmark.main() == 0
    import json
    summary = json.loads(next((tmp_path / "workspace/industrial_benchmarks").glob("campaign-*/summary.json")).read_text())
    assert len(summary["fair_comparisons"]) == 1
    assert summary["fair_comparisons"][0]["budget"]["model_resources"]["early_stopping_rounds"] == 20
    assert not summary["noncomparable"]
