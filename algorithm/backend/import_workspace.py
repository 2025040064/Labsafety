"""Copy a stopped RAC-HPO workspace into a new, portable workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def migrate(source, target):
    source, target = source.resolve(), target.resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Source and target must be separate")
    target.mkdir(parents=True, exist_ok=False)

    def rebase(value):
        if isinstance(value, str):
            path = value.replace("\\", "/")
            prefix = source.as_posix() + "/"
            if path.casefold().startswith(prefix.casefold()):
                return path[len(prefix):]
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        return value

    checks = {}
    folders = ("configs", "datasets/raw", "datasets/manifests", "workspace/experiments",
               "workspace/industrial_runs", "workspace/industrial_benchmarks",
               "workspace/industrial_acquisition", "workspace/exports", "workspace/loop")
    files = [source / name for name in ("uv.lock", "datasets/SOURCES.md", "datasets/INDUSTRIAL_SOURCES.md")]
    for folder in folders:
        files.extend(path for path in (source / folder).rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(source)
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        checksum = digest(path)
        if digest(dest) != checksum:
            raise ValueError(f"Copy verification failed: {relative}")
        checks[relative.as_posix()] = checksum

    for path in (source / "workspace/database").glob("*.sqlite3"):
        dest = target / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(dest)) as database, database:
                original.backup(database)
                tables = {
                    "experiments": ("payload",), "trials": ("payload",),
                    "regime_metrics": ("payload",), "artifacts": ("path",),
                    "sessions": ("payload",), "events": ("payload",),
                }
                for table, columns in tables.items():
                    if not database.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                        continue
                    for column in columns:
                        for row_id, old in database.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                            new = rebase(old) if column == "path" else json.dumps(rebase(json.loads(old)), ensure_ascii=False)
                            if old != new:
                                database.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (new, row_id))
                if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError(f"Invalid database copy: {path.name}")
    (target / "import-checksums.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    return len(checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(f"Verified {migrate(args.source, args.target)} copied files and SQLite integrity")
