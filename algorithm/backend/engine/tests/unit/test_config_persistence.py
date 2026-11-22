import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from rac_hpo.infrastructure.artifacts import atomic_text, write_json
from rac_hpo.infrastructure.config_loader import load_config
from rac_hpo.infrastructure.database import ExperimentRepository


ROOT = Path(__file__).resolve().parents[3].parent / 'rac-data'


@pytest.mark.parametrize("overrides", [
    {"schema_version": 2}, {"optimization": {"max_trials": 0}},
    {"optimization": {"algorithm": "unsupported"}}, {"model": {"n_jobs": 0}},
    {"robustness": {"alpha": 1.1}}, {"unknown": True},
])
def test_config_rejects_unsupported_values(overrides):
    with pytest.raises((ValueError, ValidationError)):
        load_config(ROOT / "configs/default.yaml", overrides)


def test_repository_retains_failed_trials_and_regimes(tmp_path):
    repository = ExperimentRepository(tmp_path / "results.sqlite3")
    experiment = {"id": "EXP-test", "algorithm": "tpe", "status": "running", "created_at": "2026-10-30"}
    repository.save_experiment(experiment)
    repository.save_trial("EXP-test", {"number": 0, "status": "completed", "metrics": {
        "regimes": {"weekday": {"mae": 2.0, "samples": 10, "independent_groups": 2}}
    }})
    repository.save_trial("EXP-test", {"number": 1, "status": "failed", "failure_reason": "training error"})
    assert len(repository.get_experiment("EXP-test")["trials"]) == 2
    with repository.connect() as connection:
        assert connection.execute("SELECT count(*) FROM regime_metrics").fetchone()[0] == 1


def test_unknown_schema_is_blocked_without_mutation(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER)")
        connection.execute("INSERT INTO schema_version VALUES (99)")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported database schema"):
        ExperimentRepository(path)
    assert path.read_bytes() == original


def test_repository_connection_is_closed_after_context(tmp_path):
    repository = ExperimentRepository(tmp_path / "results.sqlite3")
    with repository.connect() as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_atomic_write_never_leaves_temporary_files(tmp_path):
    path = tmp_path / "result.json"
    write_json(path, {"mae": 2.5})
    assert json.loads(path.read_text()) == {"mae": 2.5}
    assert list(tmp_path.glob("*.tmp")) == []
    with pytest.raises(ValueError):
        write_json(path, {"mae": float("nan")})
    assert json.loads(path.read_text()) == {"mae": 2.5}
