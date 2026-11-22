import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from rac_hpo.infrastructure.artifacts import utc_now


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class ExperimentRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version_exists = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if version_exists:
                versions = connection.execute("SELECT version FROM schema_version").fetchall()
                if len(versions) != 1 or versions[0][0] != 1:
                    raise ValueError("Unsupported database schema; explicit backup and migration required")
            elif connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise ValueError("Unversioned database; explicit backup and migration required")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY, algorithm TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, ended_at TEXT, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trials (
                    experiment_id TEXT NOT NULL, number INTEGER NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, number),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(id)
                );
                CREATE TABLE IF NOT EXISTS regime_metrics (
                    experiment_id TEXT NOT NULL, trial_number INTEGER NOT NULL,
                    regime_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, trial_number, regime_id),
                    FOREIGN KEY(experiment_id, trial_number) REFERENCES trials(experiment_id, number)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    experiment_id TEXT NOT NULL, name TEXT NOT NULL,
                    path TEXT NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, name),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(id)
                );
            """)
            if not version_exists:
                connection.execute("INSERT INTO schema_version VALUES (1)")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_experiment(self, value: dict) -> None:
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO experiments(id,algorithm,status,created_at,ended_at,payload)
                VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, ended_at=excluded.ended_at, payload=excluded.payload
            """, (value["id"], value["algorithm"], value["status"], value["created_at"],
                  value.get("ended_at"), encode(value)))

    def save_trial(self, experiment_id: str, trial: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO trials VALUES (?,?,?,?)",
                (experiment_id, trial["number"], trial["status"], encode(trial)),
            )
            for regime, metric in trial.get("metrics", {}).get("regimes", {}).items():
                connection.execute("INSERT INTO regime_metrics VALUES (?,?,?,?)",
                                   (experiment_id, trial["number"], str(regime), encode(metric)))

    def save_artifact(self, experiment_id: str, name: str, path: str, sha256: str) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO artifacts VALUES (?,?,?,?)",
                               (experiment_id, name, path, sha256))

    def list_experiments(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload FROM experiments ORDER BY created_at DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_experiment(self, experiment_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM experiments WHERE id=?", (experiment_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown experiment: {experiment_id}")
            result = json.loads(row[0])
            result["trials"] = [json.loads(trial[0]) for trial in connection.execute(
                "SELECT payload FROM trials WHERE experiment_id=? ORDER BY number", (experiment_id,)
            ).fetchall()]
        return result
