import ast
import json
import shutil
from pathlib import Path

import pytest

from rac_hpo.application.experiment_service import ApplicationService


ROOT = Path(__file__).resolve().parents[3].parent / 'rac-data'
DATA = ROOT / "datasets/raw/steel_energy/Steel_industry_data.csv"


@pytest.fixture
def service(tmp_path):
    (tmp_path / "configs").mkdir()
    shutil.copy2(ROOT / "configs/default.yaml", tmp_path / "configs/default.yaml")
    return ApplicationService(tmp_path)


@pytest.mark.skipif(not DATA.exists(), reason="Official dataset not downloaded; run rac-hpo fetch")
@pytest.mark.parametrize("algorithm", ["tpe", "tpe_robust"])
def test_real_training_search_storage_and_export(service, tmp_path, algorithm):
    summary = service.prepare_dataset(DATA)
    assert summary["row_count"] == 35040
    assert summary["manifest"]["matches_official_sha256"]
    events = []
    detail = service.run_experiment(algorithm, str(DATA), {
        "model": {"n_estimators": 35, "early_stopping_rounds": 5},
        "optimization": {"max_trials": 3, "timeout_seconds": 30},
    }, events.append)
    assert detail["status"] == "completed", detail.get("failure_reason")
    assert detail["best_trial"] in {0, 1, 2}
    assert len(detail["trials"]) == 4  # One fixed baseline plus three search trials.
    best = next(trial for trial in detail["trials"] if trial["number"] == detail["best_trial"])
    assert detail["metrics"] == best["metrics"]
    objective_key = "robust_objective" if algorithm == "tpe_robust" else "overall_mae"
    assert detail["best_value"] == best["metrics"][objective_key]
    baseline = next(trial for trial in detail["trials"] if trial["number"] == -1)
    assert baseline["objective"] == baseline["metrics"][objective_key]
    assert detail["metrics"]["robust_objective"] > 0
    assert "test_evaluation" not in detail
    assert {"baseline_completed", "trial_completed", "finished"} <= {event["type"] for event in events}
    assert Path(detail["artifacts"]["model"]).is_file()
    directory = Path(detail["directory"])
    assert json.loads((directory / "environment.json").read_text())["packages"]["lightgbm"] == "4.6.0"
    export = Path(service.export_results(detail["id"], tmp_path / "exports"))
    assert (export / "trials.csv").is_file()
    assert (export / "manifest.json").is_file()
    assert (export / "model.txt").is_file()
    assert json.loads((export / "experiment.json").read_text(encoding="utf-8"))["metrics"] == detail["metrics"]
    assert service.list_experiments()[0]["id"] == detail["id"]
    import numpy as np
    from rac_hpo.training.model_export import load_model
    dataset = service._prepare(DATA)
    x_valid, y_valid, _, _ = dataset.split("validation")
    model = load_model(detail["artifacts"]["model"])
    assert float(np.abs(model.predict(x_valid) - y_valid).mean()) == pytest.approx(detail["metrics"]["overall_mae"])


@pytest.mark.skipif(not DATA.exists(), reason="Official dataset not downloaded")
def test_explicit_finalization_refits_once_and_exports_test_report(service, tmp_path):
    import pandas as pd
    source = tmp_path / "measured-first-90-days.csv"
    pd.read_csv(DATA).iloc[:90 * 96].to_csv(source, index=False)
    detail = service.run_experiment("baseline", str(source), {
        "model": {"n_estimators": 10, "n_jobs": 1},
    })
    assert detail["status"] == "completed"
    report = service.finalize_experiment(detail["id"])
    assert report["protocol"] == "refit_train_plus_validation_fixed_best_iteration"
    assert report["metrics"]["overall_mae"] >= 0
    saved = json.loads((Path(detail["directory"]) / "result.json").read_text(encoding="utf-8"))
    assert saved["test_evaluation"] == report
    with pytest.raises(ValueError, match="already evaluated"):
        service.finalize_experiment(detail["id"])
    export = Path(service.export_results(detail["id"], tmp_path / "exports"))
    assert (export / "final_model.txt").is_file()
    assert json.loads((export / "test_evaluation.json").read_text(encoding="utf-8")) == report


@pytest.mark.skipif(not DATA.exists(), reason="Official dataset not downloaded")
def test_cancellation_is_persisted_before_training(service):
    detail = service.run_experiment("baseline", str(DATA), cancel_check=lambda: True)
    assert detail["status"] == "cancelled"
    assert detail["trials"] == []
    assert "model" not in detail["artifacts"]
    assert service.get_experiment(detail["id"])["status"] == "cancelled"


def test_core_modules_do_not_import_presentation_or_storage():
    for layer in ("domain", "data", "training", "optimization", "evaluation"):
        for path in (ROOT / "src/rac_hpo" / layer).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                assert not any(name.startswith(("PySide6", "sqlite3", "rac_hpo.interfaces", "rac_hpo.infrastructure"))
                               for name in names), f"Boundary violation: {path}"
