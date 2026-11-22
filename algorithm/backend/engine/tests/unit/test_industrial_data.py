"""Small constructed inputs verify boundaries, never industrial performance."""
import json
from pathlib import Path
import zipfile
import struct
import zlib
import shutil

import numpy as np
import pandas as pd
import pytest
from scipy.io import savemat

from rac_hpo.data.industrial import prepare
from rac_hpo.data.industrial.common import sha256, unpack_archive, unpack_cached_archive
from rac_hpo.data.industrial.uci import fetch_uci


def ai4i_csv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 60
    pd.DataFrame({"UDI": range(count), "Product ID": [f"L{i}" for i in range(count)],
                  "Type": ["L", "M", "H"] * 20, "Air temperature [K]": np.arange(count) + 300.,
                  "Process temperature [K]": np.arange(count) + 310., "Rotational speed [rpm]": 1500,
                  "Torque [Nm]": 30., "Tool wear [min]": 20, "Machine failure": [0, 1] * 30,
                  "TWF": [0, 1] * 30, "HDF": 0, "PWF": 0, "OSF": 0, "RNF": 0}).to_csv(path, index=False)
    return path


def test_ai4i_excludes_identity_and_all_failure_causes(tmp_path):
    dataset = prepare("ai4i_2020", ai4i_csv(tmp_path / "data.csv"), tmp_path / "manifests")
    assert dataset.manifest["data_origin"] == "synthetic"
    assert dataset.manifest["task_type"] == "classification"
    assert len(dataset.features) == 6
    assert not {"UDI", "Product ID", "TWF", "HDF", "PWF", "OSF", "RNF", "Machine failure"}.intersection(dataset.X)
    assert dataset.splits["train"] == list(range(36))
    assert dataset.manifest["independent_unit_kind"] == "synthetic_row"


def test_ai4i_ucimlrepo_unitless_feature_names_supported_without_mutating_source(tmp_path):
    path = ai4i_csv(tmp_path / "data.csv")
    frame = pd.read_csv(path)
    frame.columns = [str(col).split(" [")[0].replace("UDI", "UID") for col in frame]
    frame.to_csv(path, index=False)
    before = sha256(path)
    dataset = prepare("ai4i_2020", path)
    assert len(dataset.features) == 6
    assert "Air temperature [K]" in dataset.X
    assert sha256(path) == before
    assert dataset.manifest["feature_name_normalization"]["Air temperature"] == "Air temperature [K]"


def test_dataset_fingerprint_does_not_depend_on_install_directory(tmp_path):
    one = prepare("ai4i_2020", ai4i_csv(tmp_path / "one" / "data.csv"))
    two = prepare("ai4i_2020", ai4i_csv(tmp_path / "two" / "data.csv"))
    assert one.source_sha256 == two.source_sha256
    assert one.manifest["split_fingerprint"] == two.manifest["split_fingerprint"]


def test_secom_time_order_and_missing_values_preserved(tmp_path):
    path = tmp_path / "data.csv"
    timestamps = pd.date_range("2008-07-01", periods=30, freq="h")
    frame = pd.DataFrame({"sensor": np.arange(30, dtype=float), "Label": [-1, 1] * 15,
                          "TimeStamp": timestamps.strftime("%d/%m/%Y %H:%M:%S")})
    frame.loc[2, "sensor"] = np.nan
    frame.iloc[::-1].to_csv(path, index=False)
    dataset = prepare("secom", path)
    assert dataset.X.iloc[0, 0] == 0
    assert pd.isna(dataset.X.iloc[2, 0])
    assert set(dataset.regimes) == {"overall"}
    assert max(dataset.splits["train"]) < min(dataset.splits["validation"])
    assert dataset.y.tolist() == [0, 1] * 15
    assert "timestamp" not in " ".join(dataset.features).lower()


def test_secom_ucimlrepo_class_timestamp_schema_supported(tmp_path):
    path = tmp_path / "data.csv"
    pd.DataFrame({"class": [-1, 1] * 15,
                  "timestamp": pd.date_range("2008-07-19 11:55", periods=30, freq="h").strftime("%d/%m/%Y %H:%M:%S"),
                  "Attribute 1": np.arange(30), "Attribute 2": np.nan}).to_csv(path, index=False)
    original = sha256(path)
    dataset = prepare("secom", path)
    assert dataset.X.shape == (30, 2)
    assert dataset.y.tolist() == [0, 1] * 15
    assert sha256(path) == original


def test_secom_mixed_seconds_and_single_digit_dates_keep_dayfirst_order(tmp_path):
    path = tmp_path / "data.csv"
    stamps = ["19/07/2008 11:55:00", "1/8/2008 2:02", "01/08/2008 03:02:00"]
    pd.DataFrame({"class": [-1, 1, -1] * 10, "timestamp": stamps * 10,
                  "Attribute 1": range(30)}).to_csv(path, index=False)
    dataset = prepare("secom", path)
    assert dataset.manifest["timestamps"][0] == "2008-07-19 11:55:00"
    assert dataset.manifest["timestamps"][-1] == "2008-08-01 03:02:00"


def test_paderborn_all_windows_and_conditions_hold_out_entire_bearings(tmp_path):
    rng = np.random.default_rng(71)
    for bearing in ("K001", "K002", "K003", "KA04", "KA15", "KA16", "KI04", "KI14", "KI16"):
        for condition in ("N15_M07_F10", "N09_M07_F10"):
            stem = f"{condition}_{bearing}_1"
            savemat(tmp_path / f"{stem}.mat", {stem: {"Y": {"Name": "vibration_1", "Data": rng.normal(size=256)}}})
    dataset = prepare("paderborn", tmp_path)
    units = {name: set(dataset.groups.iloc[indices]) for name, indices in dataset.splits.items()}
    assert len(dataset.X) == 144
    assert not units["train"] & units["validation"]
    assert not units["train"] & units["test"]
    assert not units["validation"] & units["test"]
    for indices in dataset.splits.values():
        assert set(dataset.y.iloc[indices]) == {0, 1, 2}
        assert set(dataset.regimes.iloc[indices]) == {"N15_M07_F10", "N09_M07_F10"}
    assert "bearing_id" not in dataset.X
    assert dataset.manifest["independent_unit_kind"] == "physical_bearings"


@pytest.mark.parametrize("member", ["../outside.txt", "/absolute.txt", "C:/absolute.txt", "safe/../../outside.txt"])
def test_archive_traversal_rejected_before_extraction(tmp_path, member):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("safe.txt", "safe")
        handle.writestr(member, "not safe")
    with pytest.raises(ValueError, match="Unsafe archive"):
        unpack_archive(archive, tmp_path / "output")
    assert not (tmp_path / "output" / "safe.txt").exists()


def test_archive_existing_file_preserved(tmp_path):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("data.txt", "new")
    output = tmp_path / "output"
    output.mkdir()
    (output / "data.txt").write_text("original")
    with pytest.raises(FileExistsError):
        unpack_archive(archive, output)
    assert (output / "data.txt").read_text() == "original"


def test_cached_archive_rejects_new_symlink(tmp_path, monkeypatch):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("data.txt", "original")
    output = unpack_cached_archive(archive, tmp_path / "cache")
    injected = output / "unexpected.mat"
    injected.write_text("external target fixture")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == injected or original(path))
    with pytest.raises(ValueError, match="symlink"):
        unpack_cached_archive(archive, tmp_path / "cache")


def stored_rar(path, name, payload=b"small verified archive fixture"):
    def header(content):
        return struct.pack("<H", zlib.crc32(content) & 0xffff) + content
    encoded = name.encode()
    main = header(struct.pack("<BHHHI", 0x73, 0, 13, 0, 0))
    file_header = header(struct.pack("<BHHIIBIIBBHI", 0x74, 0x8000, 32 + len(encoded),
        len(payload), len(payload), 2, zlib.crc32(payload), 0, 20, 0x30, len(encoded), 0x20) + encoded)
    end = header(struct.pack("<BHH", 0x7b, 0x4000, 7))
    path.write_bytes(b"Rar!\x1a\x07\x00" + main + file_header + payload + end)
    return path


@pytest.mark.skipif(shutil.which("tar") is None, reason="Trusted system tar is required for local RAR4")
def test_safe_local_rar4_extraction(tmp_path):
    archive = stored_rar(tmp_path / "input.rar", "bearing/sensor.mat")
    root = unpack_archive(archive, tmp_path / "output")
    assert (root / "bearing" / "sensor.mat").read_bytes() == b"small verified archive fixture"


def test_local_rar4_traversal_rejected_before_extraction(tmp_path):
    archive = stored_rar(tmp_path / "input.rar", "../outside.mat")
    with pytest.raises(ValueError, match="Unsafe archive"):
        unpack_archive(archive, tmp_path / "output")
    assert not (tmp_path / "outside.mat").exists()


def test_selective_rar_rejects_encrypted_entry_before_extraction(tmp_path, monkeypatch):
    from rac_hpo.data.industrial import bearings
    prefix = bytearray(20)
    prefix[:7] = b"Rar!\x1a\x07\x00"
    prefix[9] = 0x73
    struct.pack_into("<H", prefix, 12, 13)
    name = b"K001/N15_M07_F10_K001_1.mat"
    header = bytearray(256)
    struct.pack_into("<BHH", header, 2, 0x74, 0x8004, 32 + len(name))
    struct.pack_into("<H", header, 26, len(name))
    header[32:32 + len(name)] = name
    monkeypatch.setattr(bearings, "_range", lambda url, start, end: bytes(prefix) if start == 0 else bytes(header))
    def forbidden(*args, **kwargs):
        raise AssertionError("Encrypted RAR must never reach extraction")
    monkeypatch.setattr(bearings.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="encrypted"):
        bearings._fetch_one_bearing("K001", tmp_path)


def test_uci_failure_is_saved_without_substitution(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    def fail(**kwargs):
        raise ConnectionError("fixture network outage")
    monkeypatch.setitem(sys.modules, "ucimlrepo", SimpleNamespace(fetch_ucirepo=fail))
    with pytest.raises(RuntimeError, match="UCI acquisition failed"):
        fetch_uci("secom", tmp_path)
    record = json.loads((tmp_path / "download_failure.json").read_text())
    assert record["substitution_used"] is False
    assert not (tmp_path / "data.csv").exists()


def test_lenovo_authorization_required(tmp_path):
    path = tmp_path / "data.csv"
    pd.DataFrame({"sensor": range(10), "label": [0, 1] * 5, "piece": range(10), "regime": "known"}).to_csv(path, index=False)
    metadata = {"dataset_key": "lenovo", "dataset_version": "local-1", "license": "Provider permission",
                "source_url": "https://nbsdc.cn/general/dataDetail?id=67d510b0195d260905af9dcc&type=1",
                "acquired_at": "2026-10-31", "sha256": sha256(path), "target_column": "label",
                "group_column": "piece", "regime_column": "regime", "feature_columns": ["sensor"],
                "classes": {"0": "pass", "1": "fail"}}
    (tmp_path / "source.json").write_text(json.dumps(metadata))
    with pytest.raises(PermissionError, match="authorization"):
        prepare("lenovo", path)


def test_xjtu_complete_runs_rul_and_physical_groups(tmp_path):
    complete = {}
    for bearing in ("Bearing1_1", "Bearing1_2", "Bearing1_3"):
        folder = tmp_path / bearing
        folder.mkdir()
        complete[bearing] = {"last_record": 3, "failure_observed": True}
        for index in range(1, 4):
            pd.DataFrame({"Horizontal_vibration_signals": np.sin(np.arange(32768)),
                          "Vertical_vibration_signals": np.cos(np.arange(32768))}).to_csv(folder / f"{index}.csv", index=False)
    (tmp_path / "source.json").write_text(json.dumps({"dataset_key": "xjtu_sy",
        "source_url": "https://biaowang.tech/xjtu-sy-bearing-datasets/", "acquired_at": "2026-10-31",
        "complete_runs": complete}))
    dataset = prepare("xjtu_sy", tmp_path)
    assert dataset.manifest["task_type"] == "regression"
    assert dataset.target_unit == "minutes"
    assert dataset.y.tolist() == [2., 1., 0.] * 3
    assert len(set(dataset.groups.iloc[dataset.splits["train"]])) == 1
    assert not {"record_number", "total_lifetime", "remaining_life"}.intersection(dataset.X)
    archive = tmp_path.parent / "xjtu-source.zip"
    with zipfile.ZipFile(archive, "w") as compressed:
        for item in tmp_path.rglob("*"):
            if item.is_file():
                compressed.write(item, item.relative_to(tmp_path).as_posix())
    cache = tmp_path.parent / "workspace-cache"
    first = prepare("xjtu_sy", archive, archive_cache_dir=cache)
    second = prepare("xjtu_sy", archive, archive_cache_dir=cache)
    assert first.manifest["split_fingerprint"] == second.manifest["split_fingerprint"]
    assert first.manifest["input_archive"]["sha256"] == sha256(archive)
    assert not (tmp_path.parent / "xjtu-source_extracted").exists()
    record = tmp_path / "Bearing1_1" / "1.csv"
    complete_record = record.read_bytes()
    pd.read_csv(record).iloc[:32].to_csv(record, index=False)
    with pytest.raises(ValueError, match="32768"):
        prepare("xjtu_sy", tmp_path)
    record.write_bytes(complete_record)
    (tmp_path / "Bearing1_1" / "2.csv").unlink()
    with pytest.raises(ValueError, match="truncated"):
        prepare("xjtu_sy", tmp_path)
