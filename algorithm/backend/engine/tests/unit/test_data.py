from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.error import URLError
from zipfile import ZipFile

import pandas as pd
import pytest

from rac_hpo.data import download_steel, prepare_steel_dataset
from rac_hpo.data import loader
from rac_hpo.data.validator import STEEL_COLUMNS
from rac_hpo._io import atomic_text


@pytest.fixture
def steel_frame():
    timestamps = pd.date_range("2018-01-01", periods=40 * 4, freq="6h")
    frame = pd.DataFrame({column: 0 for column in STEEL_COLUMNS}, index=range(len(timestamps)))
    frame["date"] = timestamps.strftime("%d/%m/%Y %H:%M")
    frame["Usage_kWh"] = [float(index % 17 + 1) for index in range(len(timestamps))]
    frame["WeekStatus"] = ["Weekend" if stamp.dayofweek >= 5 else "Weekday" for stamp in timestamps]
    frame["Day_of_week"] = timestamps.day_name()
    frame["Load_Type"] = "Light_Load"
    return frame


def write_frame(tmp_path, frame):
    path = tmp_path / "Steel_industry_data.csv"
    frame.to_csv(path, index=False)
    return path


def test_calendar_adapter_has_disjoint_date_splits_and_reproducible_manifest(tmp_path, steel_frame):
    path = write_frame(tmp_path, steel_frame.iloc[::-1])
    manifests = tmp_path / "manifests"
    dataset = prepare_steel_dataset(path, manifests)
    repeated = prepare_steel_dataset(path, manifests)
    assert dataset.features == ["hour", "minute", "day_of_week", "month", "is_weekend"]
    assert dataset.X.columns.tolist() == dataset.features
    assert dataset.groups.is_monotonic_increasing
    assert dataset.splits == repeated.splits
    assert dataset.source_sha256 == sha256(path.read_bytes()).hexdigest()
    seen = set()
    date_sets = {}
    for name, indices in dataset.splits.items():
        assert not seen.intersection(indices)
        seen.update(indices)
        date_sets[name] = set(dataset.groups.iloc[indices])
    assert seen == set(range(len(steel_frame)))
    assert not date_sets["train"] & date_sets["validation"]
    assert not date_sets["validation"] & date_sets["test"]
    for earlier, later in (("train", "validation"), ("validation", "test")):
        assert (pd.Timestamp(min(date_sets[later])) - pd.Timestamp(max(date_sets[earlier]))).days >= 2
    stored = json.loads(next(manifests.glob("*.json")).read_text(encoding="utf-8"))
    assert stored == dataset.manifest
    assert stored["independent_batch_count"] is None
    assert "not independent machining" in stored["group_definition"]
    source_positions = stored["source_row_indices"]
    assert dataset.y.tolist() == steel_frame.iloc[::-1].iloc[source_positions]["Usage_kWh"].tolist()


@pytest.mark.parametrize("problem,expected", [
    ("missing_column", "missing required columns"),
    ("empty", "empty"),
    ("nan", "missing values"),
    ("duplicate", "Duplicate timestamps"),
    ("invalid_date", "Invalid date"),
    ("negative_target", "finite, non-negative"),
    ("infinite_target", "finite, non-negative"),
    ("invalid_regime", "WeekStatus conflicts"),
    ("invalid_weekday", "Day_of_week conflicts"),
])
def test_reject_invalid_data(tmp_path, steel_frame, problem, expected):
    frame = steel_frame.copy()
    if problem == "missing_column":
        frame = frame.drop(columns="Usage_kWh")
    elif problem == "empty":
        frame = frame.iloc[:0]
    elif problem == "nan":
        frame.loc[0, "Usage_kWh"] = float("nan")
    elif problem == "duplicate":
        frame.loc[1, "date"] = frame.loc[0, "date"]
    elif problem == "invalid_date":
        frame.loc[0, "date"] = "31/02/2018 00:00"
    elif problem == "negative_target":
        frame.loc[0, "Usage_kWh"] = -1.0
    elif problem == "infinite_target":
        frame.loc[0, "Usage_kWh"] = float("inf")
    elif problem == "invalid_regime":
        frame.loc[0, "WeekStatus"] = "Unknown"
    elif problem == "invalid_weekday":
        frame.loc[0, "Day_of_week"] = "Tuesday"
    with pytest.raises(ValueError, match=expected):
        prepare_steel_dataset(write_frame(tmp_path, frame))


def test_target_changes_cannot_change_calendar_features_or_split(tmp_path, steel_frame):
    path = write_frame(tmp_path, steel_frame)
    first = prepare_steel_dataset(path)
    steel_frame["Usage_kWh"] *= 100
    second = prepare_steel_dataset(write_frame(tmp_path, steel_frame))
    pd.testing.assert_frame_equal(first.X, second.X)
    pd.testing.assert_series_equal(first.regimes, second.regimes)
    assert first.splits == second.splits


def test_local_import_cannot_inherit_official_provenance_or_license(tmp_path, steel_frame):
    dataset = prepare_steel_dataset(write_frame(tmp_path, steel_frame))
    assert dataset.manifest["license"] == "unknown"
    assert dataset.manifest["source_type"] == "user_supplied"
    assert dataset.manifest["source_url"] is None
    assert dataset.manifest["citation"] is None
    assert dataset.manifest["doi"] is None
    assert not dataset.manifest["provenance_verified"]
    assert dataset.dataset_id.startswith("user_supplied_steel_")
    assert dataset.manifest["adapter_reference"]["doi"] == "10.24432/C52G8C"


def test_too_few_dates_and_absent_regimes_are_explicit_errors(tmp_path, steel_frame):
    with pytest.raises(ValueError, match="at least 7 distinct dates"):
        prepare_steel_dataset(write_frame(tmp_path, steel_frame.iloc[:4]))
    weekdays_only = steel_frame[steel_frame["WeekStatus"] == "Weekday"]
    with pytest.raises(ValueError, match="both pre-defined"):
        prepare_steel_dataset(write_frame(tmp_path, weekdays_only))


def test_manifest_cannot_overwrite_different_existing_record(tmp_path, steel_frame):
    path = write_frame(tmp_path, steel_frame)
    manifests = tmp_path / "manifests"
    prepare_steel_dataset(path, manifests)
    manifest = next(manifests.glob("*.json"))
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Existing dataset manifest differs"):
        prepare_steel_dataset(path, manifests)
    assert manifest.read_text(encoding="utf-8") == "{}"


def test_identical_bytes_at_another_path_can_reuse_immutable_manifest(tmp_path, steel_frame):
    path = write_frame(tmp_path, steel_frame)
    copied = tmp_path / "another-name.csv"
    copied.write_bytes(path.read_bytes())
    directory = tmp_path / "manifests"
    original = prepare_steel_dataset(path, directory)
    duplicate = prepare_steel_dataset(copied, directory)
    assert original.manifest == duplicate.manifest
    assert original.source_path != duplicate.source_path
    assert len(list(directory.glob("*.json"))) == 1


def test_manifest_concurrent_creation_cannot_overwrite_history(tmp_path, steel_frame, monkeypatch):
    def competing_write(path, content, *, overwrite=True):
        assert not overwrite
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}", encoding="utf-8")
        atomic_text(path, content, overwrite=overwrite)
    monkeypatch.setattr(loader, "atomic_text", competing_write, raising=False)
    path = write_frame(tmp_path, steel_frame)
    manifests = tmp_path / "manifests"
    with pytest.raises(ValueError, match="Existing dataset manifest differs"):
        prepare_steel_dataset(path, manifests)
    assert next(manifests.glob("*.json")).read_text(encoding="utf-8") == "{}"
    assert not list(manifests.glob("*.tmp"))


def test_unreadable_csv_and_invalid_manifest_directory_report_value_errors(tmp_path, steel_frame):
    with pytest.raises(ValueError, match="Could not read Steel CSV"):
        prepare_steel_dataset(tmp_path / "missing.csv")
    path = write_frame(tmp_path, steel_frame)
    with pytest.raises(ValueError, match="Could not save the dataset manifest"):
        prepare_steel_dataset(path, path)


def test_download_validates_checksum_and_preserves_existing_files(tmp_path, monkeypatch):
    contents = b"date,Usage_kWh\n01/01/2018 00:00,1.0\n"
    archive = BytesIO()
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("Steel_industry_data.csv", contents)
    monkeypatch.setattr(loader, "OFFICIAL_SHA256", sha256(contents).hexdigest())
    monkeypatch.setattr(loader, "urlopen", lambda *args, **kwargs: BytesIO(archive.getvalue()))
    path = download_steel(tmp_path / "download")
    assert path.read_bytes() == contents
    assert download_steel(path) == path
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Existing file differs"):
        download_steel(path)
    assert path.read_bytes() == b"changed"


def test_download_failure_cannot_create_success_file(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("test network failure")
    monkeypatch.setattr(loader, "urlopen", fail)
    path = tmp_path / "download" / "Steel_industry_data.csv"
    with pytest.raises(ValueError, match="Could not download"):
        download_steel(path)
    assert not path.exists()


def test_download_rejects_changed_official_csv(tmp_path, monkeypatch):
    archive = BytesIO()
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("Steel_industry_data.csv", b"unexpected official update")
    monkeypatch.setattr(loader, "urlopen", lambda *args, **kwargs: BytesIO(archive.getvalue()))
    with pytest.raises(ValueError, match="checksum changed"):
        download_steel(tmp_path / "download")
    assert not (tmp_path / "download" / "Steel_industry_data.csv").exists()
