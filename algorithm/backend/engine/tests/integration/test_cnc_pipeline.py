from pathlib import Path
import shutil

import pytest

from rac_hpo.application.experiment_service import ApplicationService

ROOT = Path(__file__).resolve().parents[3].parent / 'rac-data'
DATA = ROOT / "datasets/raw/cnc_milling_2025/FeatureAndMetadata_Milling.csv"


@pytest.mark.skipif(not DATA.exists(), reason="Fetch real CNC measurements first")
def test_author_cnc_real_baseline_and_robust_search(tmp_path):
    (tmp_path / "configs").mkdir()
    shutil.copy2(ROOT / "configs/default.yaml", tmp_path / "configs/default.yaml")
    service = ApplicationService(tmp_path)
    summary = service.prepare_dataset(str(DATA))
    assert summary["dataset_id"] == "cnc_milling_2025"
    assert summary["row_count"] == 968
    result = service.run_experiment("tpe_robust", str(DATA), {
        "model": {"n_estimators": 25, "n_jobs": 1}, "optimization": {"max_trials": 3},
    })
    assert result["status"] == "completed", result
    assert result["target_unit"] == "cycles"
    assert result["config"]["data"]["dataset_id"] == "cnc_milling_2025"
    assert result["metrics"]["robust_objective"] > 0
    assert all(metric["effective_independent_groups"] == 2 and metric["mae_interval"] is None
               for metric in result["metrics"]["regimes"].values())
    assert "test_evaluation" not in result
    assert len(result["trials"]) == 4
    exported = Path(service.export_results(result["id"], tmp_path / "exports"))
    text = (exported / "report.md").read_text(encoding="utf-8")
    assert "Validation metrics (cycles)" in text
    assert "Date blocks are temporal groups" not in text


@pytest.mark.skipif(not DATA.exists(), reason="Fetch real CNC measurements first")
def test_real_rac_selects_candidates_preserves_evidence_and_never_reads_test(tmp_path, monkeypatch):
    from rac_hpo.domain.schemas import PreparedDataset
    original = PreparedDataset.split
    accessed = []
    def sealed(dataset, name):
        accessed.append(name)
        assert name != "test", "RAC must not access sealed-test labels"
        return original(dataset, name)
    monkeypatch.setattr(PreparedDataset, "split", sealed)
    (tmp_path / "configs").mkdir()
    shutil.copy2(ROOT / "configs/default.yaml", tmp_path / "configs/default.yaml")
    result = ApplicationService(tmp_path).run_experiment("rac_hpo", str(DATA), {
        "optimization": {"max_trials": 24, "timeout_seconds": 30},
    })
    assert result["status"] == "completed", result.get("failure_reason")
    assert set(accessed) == {"train", "validation"}
    decisions = [trial["selection"] for trial in result["trials"] if trial.get("selection", {}).get("method") == "rac_acquisition"]
    assert decisions, result["search_diagnostics"]
    assert any(decision["acquisition"] > 0 for decision in decisions)
    assert all(item["q"] == 0 for decision in decisions for item in decision["regime_priorities"].values())
    assert all(decision["predicted_cost_seconds"] > 0 for decision in decisions)
    assert all(decision["surrogate_age_trials"] < 4 for decision in decisions)
    assert result["best_value"] == result["metrics"]["robust_objective"]


@pytest.mark.skipif(not DATA.exists(), reason="Fetch real CNC measurements first")
@pytest.mark.parametrize("algorithm", ["grid", "successive_halving", "hyperband"])
def test_summary_baselines_save_real_models_and_audit_without_test(tmp_path, monkeypatch, algorithm):
    import json
    from rac_hpo.domain.schemas import PreparedDataset
    original = PreparedDataset.split
    def sealed(dataset, name):
        assert name != "test"
        return original(dataset, name)
    monkeypatch.setattr(PreparedDataset, "split", sealed)
    (tmp_path / "configs").mkdir()
    shutil.copy2(ROOT / "configs/default.yaml", tmp_path / "configs/default.yaml")
    service = ApplicationService(tmp_path)
    run = service.run_experiment(algorithm, str(DATA), {
        "model": {"n_estimators": 30, "n_jobs": 1},
        "optimization": {"max_trials": 8, "timeout_seconds": 30, "min_resource": 3}})
    assert run["status"] == "completed", run.get("failure_reason")
    assert Path(run["artifacts"]["model"]).is_file()
    audit = json.loads(Path(run["artifacts"]["quality_audit"]).read_text())
    assert audit["audited_rows"] == 842
    assert audit["policy"] == "flag_only_preserve_rows"
    assert len(run["trials"]) == 9
    if algorithm != "grid":
        trials = run["trials"][1:]
        assert all(trial["resource"]["last_step"] > 0 for trial in trials)
        assert any(trial["status"] == "pruned" for trial in trials)
    assert "test_evaluation" not in run
    exported = Path(service.export_results(run["id"]))
    assert (exported / "quality_audit.json").is_file()
