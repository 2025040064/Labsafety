import subprocess
import importlib.metadata

from rac_hpo.infrastructure.artifacts import environment_snapshot


def test_snapshot_survives_missing_git_executable(monkeypatch, tmp_path):
    """A machine without git on PATH must still produce a usable snapshot."""

    def missing_git(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified", "git")

    monkeypatch.setattr(subprocess, "run", missing_git)
    snapshot = environment_snapshot(tmp_path)
    assert snapshot["code_commit"] == "unversioned"
    assert snapshot["python"]
    assert snapshot["source_sha256"]
    assert snapshot["packages"]["lightgbm"] == "4.6.0"


def test_snapshot_records_commit_when_git_succeeds(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = "deadbeef\n"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Completed())
    snapshot = environment_snapshot(tmp_path)
    assert snapshot["code_commit"] == "deadbeef"


def test_snapshot_accepts_pyside6_essentials(monkeypatch, tmp_path):
    version = importlib.metadata.version

    def essentials_only(name):
        if name == "PySide6":
            raise importlib.metadata.PackageNotFoundError(name)
        return version(name)

    monkeypatch.setattr(importlib.metadata, "version", essentials_only)
    snapshot = environment_snapshot(tmp_path)
    assert snapshot["packages"]["PySide6"] == version("PySide6-Essentials")
