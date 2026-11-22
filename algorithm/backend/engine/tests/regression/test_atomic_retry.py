import os

import pytest

from rac_hpo._io import atomic_text


def test_atomic_write_retries_temporary_windows_sharing_failure(tmp_path, monkeypatch):
    target = tmp_path / "trial.json"
    target.write_text("old", encoding="utf-8")
    original = os.replace
    attempts = []
    def temporary_lock(source, destination):
        attempts.append(1)
        if len(attempts) < 3:
            error = PermissionError("temporary Windows sharing lock")
            error.winerror = 32
            raise error
        return original(source, destination)
    monkeypatch.setattr(os, "replace", temporary_lock)
    atomic_text(target, "new complete content")
    assert target.read_text(encoding="utf-8") == "new complete content"
    assert len(attempts) == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_permanent_permission_failure_preserves_old_file(tmp_path, monkeypatch):
    target = tmp_path / "trial.json"
    target.write_text("old", encoding="utf-8")
    def denied(*arguments):
        raise PermissionError("permanent denial")
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        atomic_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.tmp"))
