"""Service persistence contracts use test fixtures, never industrial evidence."""
import json
from pathlib import Path
import zipfile

import joblib
import numpy as np
import pandas as pd
import pytest

from rac_hpo.application.industrial_service import IndustrialService
from rac_hpo.domain.schemas import PreparedDataset


@pytest.fixture
def fixture_dataset():
    values = np.arange(120)
    return PreparedDataset(
        dataset_id="test-fixture-v1", source_path="fixture.csv", source_sha256="fixture-hash",
        features=["signal", "category"], target="failure", target_unit="class",
        X=pd.DataFrame({"signal": values % 2, "category": np.where(values % 3, "a", "b")}),
        y=pd.Series(values % 2), regimes=pd.Series(["all"] * len(values)),
        groups=pd.Series(values.astype(str)),
        splits={"train": list(range(72)), "validation": list(range(72, 96)), "test": list(range(96, 120))},
        manifest={"task_type": "classification", "data_origin": "test_fixture",
                  "split_fingerprint": "fixture-split", "independent_unit_kind": "records"})


def test_success_persists_reloadable_model_and_audited_config(tmp_path, monkeypatch, fixture_dataset):
    from rac_hpo.data import industrial
    monkeypatch.setattr(industrial, "prepare", lambda *args, **kwargs: fixture_dataset)
    service = IndustrialService(tmp_path)
    result = service.run("ai4i_2020", "random_forest", model_config={"n_estimators": 8, "n_jobs": 1})
    assert result["status"] == "completed", result.get("failure_reason")
    model = joblib.load(result["artifacts"]["model"])
    assert np.array_equal(model.predict(fixture_dataset.X.iloc[72:96]), fixture_dataset.y.iloc[72:96])
    assert result["config"]["test_accessed"] is False
    assert result["config"]["model_config"]["early_stopping_rounds"] == 20
    assert service.get_run(result["id"])["log_text"]
    assert len(service.list_runs()) == 1
    assert result["trials"][0]["number"] == -1
    assert "overall_mae" not in result["metrics"]


def test_bad_source_is_retained_with_error_and_traceback(tmp_path):
    service = IndustrialService(tmp_path)
    events = []
    result = service.run("ai4i_2020", data_path=tmp_path / "missing.csv", progress_callback=events.append)
    assert result["status"] == "failed"
    assert result["failure_reason"]
    assert any(item["type"] == "error" for item in events)
    assert result["trials"][0]["status"] == "failed"
    assert (tmp_path / "workspace/industrial_runs" / result["id"] / "logs/traceback.log").is_file()
    assert service.get_run(result["id"])["status"] == "failed"


def test_search_retains_baseline_and_final_trials(tmp_path, monkeypatch, fixture_dataset):
    from rac_hpo.data import industrial
    monkeypatch.setattr(industrial, "prepare", lambda *args, **kwargs: fixture_dataset)
    service = IndustrialService(tmp_path)
    result = service.run("ai4i_2020", "random_forest", "random", max_trials=2,
                         model_config={"n_estimators": 8, "n_jobs": 1})
    assert result["status"] == "completed", result.get("failure_reason")
    assert [trial["number"] for trial in result["trials"]] == [-1, 0, 1]
    assert result["metrics"]["robust_objective"] == result["best_value"]
    records = (tmp_path / "workspace/industrial_runs" / result["id"] / "trials.jsonl").read_text().splitlines()
    assert len(records) == 3


def test_compare_refuses_changed_split_and_invalid_run_path(tmp_path, monkeypatch, fixture_dataset):
    from rac_hpo.data import industrial
    monkeypatch.setattr(industrial, "prepare", lambda *args, **kwargs: fixture_dataset)
    service = IndustrialService(tmp_path)
    first = service.run("ai4i_2020", "random_forest", model_config={"n_estimators": 4, "n_jobs": 1})
    fixture_dataset.manifest["split_fingerprint"] = "changed-split"
    second = service.run("ai4i_2020", "random_forest", model_config={"n_estimators": 4, "n_jobs": 1})
    assert service.compare([first["id"], second["id"]])["comparable"] is False
    with pytest.raises(ValueError, match="invalid industrial run id"):
        service.get_run("../../outside")


def test_dead_worker_run_is_closed_and_logged_without_retry(tmp_path, monkeypatch):
    from rac_hpo.infrastructure import loop_repository
    monkeypatch.setattr(loop_repository, "process_alive", lambda pid: False)
    service = IndustrialService(tmp_path)
    run_id = "IND-1234567890abcdef"
    directory = service.runs / run_id
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps({"id": run_id, "status": "running",
        "created_at": "2026-10-31T00:00:00+00:00", "pid": 99999999, "trials": [],
        "metrics": {}, "artifacts": {}}), encoding="utf-8")
    result = service.get_run(run_id)
    assert result["status"] == "failed"
    assert result["interrupted"] is True
    assert "exited" in result["failure_reason"]
    assert "interrupted" in result["log_text"]


def test_fair_comparison_rejects_different_resources_and_objectives(tmp_path, monkeypatch, fixture_dataset):
    from rac_hpo.data import industrial
    monkeypatch.setattr(industrial, "prepare", lambda *args, **kwargs: fixture_dataset)
    service = IndustrialService(tmp_path)
    first = service.run("ai4i_2020", "random_forest", model_config={"n_estimators": 4, "n_jobs": 1})
    second = service.run("ai4i_2020", "random_forest", model_config={"n_estimators": 8, "n_jobs": 2})
    result = service.compare([first["id"], second["id"]])
    assert result["comparable"] is False
    assert result["descriptive_comparable"] is True
    third = service.run("ai4i_2020", "random_forest", "random", max_trials=1,
                        model_config={"n_estimators": 4, "n_jobs": 1})
    assert service.compare([first["id"], third["id"]])["comparable"] is False


def test_local_bearing_archive_can_be_audited_then_trained_inside_workspace(tmp_path):
    from scipy.io import savemat
    external = tmp_path / "external"
    external.mkdir()
    fixtures = external / "records"
    fixtures.mkdir()
    rng = np.random.default_rng(42)
    for bearing in ("K001", "K002", "K003", "KA04", "KA15", "KA16", "KI04", "KI14", "KI16"):
        for condition in ("N15_M07_F10", "N09_M07_F10"):
            stem = f"{condition}_{bearing}_1"
            savemat(fixtures / f"{stem}.mat", {stem: {"Y": {"Name": "vibration_1", "Data": rng.normal(size=256)}}})
    archive = external / "bearings.zip"
    with zipfile.ZipFile(archive, "w") as compressed:
        for path in fixtures.glob("*.mat"):
            compressed.write(path, path.name)
    workspace = tmp_path / "workspace_root"
    service = IndustrialService(workspace)
    first = service.audit("paderborn", archive)
    second = service.audit("paderborn", archive)
    assert first["manifest"]["split_fingerprint"] == second["manifest"]["split_fingerprint"]
    assert not (external / "bearings_extracted").exists()
    assert all(Path(item["path"]).is_relative_to(workspace) for item in first["manifest"]["source_files"])
    result = service.run("paderborn", "random_forest", data_path=archive,
                         model_config={"n_estimators": 4, "n_jobs": 1})
    assert result["status"] == "completed", result.get("failure_reason")
    assert result["manifest"]["input_archive"]["sha256"]
    cached = Path(first["manifest"]["source_files"][0]["path"])
    cached.write_bytes(b"corrupted cache fixture")
    with pytest.raises(ValueError, match="Cached archive content changed"):
        service.audit("paderborn", archive)
    assert cached.read_bytes() == b"corrupted cache fixture"
