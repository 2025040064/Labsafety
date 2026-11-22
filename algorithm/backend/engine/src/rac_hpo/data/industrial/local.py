"""Explicit local import contracts for sources needing authorization or labeling."""
from pathlib import Path

import numpy as np
import pandas as pd

from .common import make_dataset, sha256, source_metadata, unpack_cached_archive
from .bearings import signal_features
import re

LOCAL_SOURCES = {
    "lenovo": "https://nbsdc.cn/general/dataDetail?id=67d510b0195d260905af9dcc&type=1",
    "pneumatic_pressure": "https://data.mendeley.com/datasets/ypzswhhzh9/3",
    "xjtu_sy": "https://biaowang.tech/xjtu-sy-bearing-datasets/",
}


def _prepare_documented_table(key, path, manifest_dir=None):
    path = Path(path)
    if path.is_dir():
        path = path / "data.csv"
    source = source_metadata(path)
    required = {"dataset_key", "dataset_version", "license", "source_url", "acquired_at", "sha256",
                "target_column", "group_column", "regime_column", "feature_columns", "classes"}
    if not required.issubset(source) or source["dataset_key"] != key or source["sha256"] != sha256(path):
        raise ValueError(f"{key} local import requires a source.json with checked provenance, schema, label meanings and CSV SHA-256")
    if source.get("source_url") != LOCAL_SOURCES[key] or source.get("license") in (None, "", "unknown"):
        raise ValueError("A verified official source and actual data-use license are required")
    if key == "lenovo" and source.get("authorized_local_use") is not True:
        raise PermissionError("Lenovo data requires provider authorization recorded as authorized_local_use=true")
    if key == "pneumatic_pressure" and source.get("prediction_task") != "operation_action_classification":
        raise ValueError("Pneumatic V3 labels describe process actions; an unverified fault target is not accepted")
    frame = pd.read_csv(path)
    features = source["feature_columns"]
    excluded = [source[name] for name in ("target_column", "group_column", "regime_column")]
    if not isinstance(features, list) or not features or set(features).intersection(excluded):
        raise ValueError("Feature schema must explicitly exclude labels and physical group identity")
    if not set(features + excluded).issubset(frame) or frame[excluded].isna().any().any():
        raise ValueError("Local data table does not satisfy its documented schema")
    X = frame[features].apply(pd.to_numeric, errors="raise")
    y = pd.to_numeric(frame[source["target_column"]], errors="raise")
    if not np.equal(y, y.astype(int)).all() or set(y.astype(int).astype(str)) != set(source["classes"]):
        raise ValueError("Classification labels must be integer IDs with complete documented meanings")
    groups = frame[source["group_column"]].astype(str)
    regimes = frame[source["regime_column"]].astype(str)
    # A device/piece may include several action classes: split complete groups independently of labels.
    units = sorted(groups.unique())
    if len(units) < 5:
        raise ValueError("At least five physical pieces/devices/batches are needed for local grouped validation")
    n_train, n_val = max(1, int(.6 * len(units))), max(1, int(.2 * len(units)))
    selected = {"train": units[:n_train], "validation": units[n_train:n_train + n_val],
                "test": units[n_train + n_val:]}
    splits = {name: np.flatnonzero(groups.isin(ids)).tolist() for name, ids in selected.items()}
    metadata = {"data_origin": "real_measurement", "source_url": source["source_url"],
                "license": source["license"], "acquired_at": source["acquired_at"],
                "dataset_version": source["dataset_version"], "classes": source["classes"],
                "source_acquisition": source, "provenance_verified": True,
                "independent_unit_kind": source.get("independent_unit_kind", "physical_piece"),
                "regime_definition": source.get("regime_definition", "User-provided prior-known metadata; verify domain meaning"),
                "group_definition": source["group_column"], "split_strategy": "Whole physical groups in documented stable ID order, 60/20/20",
                "excluded_fields": excluded, "limitations": source.get("limitations", []) + [
                    "Local normalization must preserve original action labels and physical piece/batch identities; no source labels are merged",
                    "This import contract does not infer unlabeled faults or bypass provider authorization"]}
    return make_dataset(key, path, X, y, regimes, groups, splits, metadata, [path], manifest_dir)


def prepare_lenovo(path, manifest_dir=None):
    return _prepare_documented_table("lenovo", path, manifest_dir)


def prepare_pneumatic(path, manifest_dir=None):
    return _prepare_documented_table("pneumatic_pressure", path, manifest_dir)


def prepare_xjtu(path, manifest_dir=None, archive_cache_dir=None):
    original = Path(path)
    root = original if original.is_dir() else unpack_cached_archive(
        original, archive_cache_dir or Path.cwd() / "datasets/processed/industrial/xjtu_sy")
    source = source_metadata(root / "unused")
    if source.get("dataset_key") != "xjtu_sy" or not source.get("complete_runs"):
        raise ValueError("XJTU-SY import requires source.json complete_runs with verified final record and observed-failure evidence for each physical bearing")
    if source.get("source_url") != LOCAL_SOURCES["xjtu_sy"] or not source.get("acquired_at"):
        raise ValueError("XJTU-SY official provenance and acquisition date must be documented")
    rows, targets, units, regimes, files, mapping = [], [], [], [], [], []
    conditions = {1: (2100, 12), 2: (2250, 11), 3: (2400, 10)}
    for folder in sorted(root.rglob("Bearing*")):
        match = re.fullmatch(r"Bearing([123])_([1-5])", folder.name, re.I)
        if not folder.is_dir() or not match:
            continue
        unit = folder.name
        evidence = source["complete_runs"].get(unit, {})
        if evidence.get("failure_observed") is not True:
            raise ValueError(f"Observed final failure is not documented for {unit}")
        records = sorted(folder.glob("*.csv"), key=lambda item: int(item.stem) if item.stem.isdigit() else -1)
        final_record = evidence.get("last_record")
        if not isinstance(final_record, int) or [item.stem for item in records] != [str(i) for i in range(1, final_record + 1)]:
            raise ValueError(f"XJTU-SY run has missing, duplicate, unexpected or truncated records: {unit}")
        condition = int(match[1])
        speed, force = conditions[condition]
        for index, file in enumerate(records, start=1):
            frame = pd.read_csv(file)
            if frame.shape != (32768, 2):
                raise ValueError("XJTU-SY raw CSV must contain exactly 32768 samples and two vibration channels")
            numeric = frame.apply(pd.to_numeric, errors="raise")
            features = signal_features(numeric.iloc[:, 0].to_numpy(), "horizontal")
            features.update(signal_features(numeric.iloc[:, 1].to_numpy(), "vertical"))
            features.update({"rotational_speed_rpm": speed, "radial_force_kn": force})
            rows.append(features); targets.append(final_record - index); units.append(unit)
            regimes.append(f"speed_{speed}rpm_load_{force}kn"); files.append(file)
            mapping.append({"bearing_id": unit, "record_number": index, "file": str(file.relative_to(root))})
    if not files:
        raise ValueError("XJTU-SY import contains no complete official-named bearing directories")
    groups, regime_series = pd.Series(units), pd.Series(regimes)
    chosen = {name: [] for name in ("train", "validation", "test")}
    for regime in sorted(set(regimes)):
        bearings = sorted(groups[regime_series == regime].unique())
        if len(bearings) < 3:
            raise ValueError("Each XJTU-SY operating condition requires at least three complete physical bearings")
        chosen["train"].extend(bearings[:-2]); chosen["validation"].append(bearings[-2]); chosen["test"].append(bearings[-1])
    splits = {name: np.flatnonzero(groups.isin(ids)).tolist() for name, ids in chosen.items()}
    metadata = {"task_type": "regression", "target_unit": "minutes", "data_origin": "real_measurement",
                "source_url": LOCAL_SOURCES["xjtu_sy"], "license": source.get("license", "Author permits prognostics algorithm validation with citation; no standard license verified"),
                "citation": "Wang, B.; Lei, Y.; Li, N.; Li, N. (2020), A Hybrid Prognostics Approach for Estimating Remaining Useful Life of Rolling Element Bearings, IEEE Transactions on Reliability 69(1), 401-412",
                "acquired_at": source["acquired_at"], "source_acquisition": source, "provenance_verified": False,
                "independent_unit_kind": "physical_bearings", "group_definition": "One complete physical bearing life run",
                "regime_definition": "Official prior-known speed / radial-force operating condition",
                "split_strategy": "Whole bearings within condition, all observations retain chronological source order; last two bearing IDs held out",
                "source_record_mapping": mapping, "excluded_fields": ["bearing_id", "record_number", "total_lifetime", "remaining_life"],
                "limitations": ["Remaining life targets require a verified complete run ending at observed failure",
                                "Current-vibration diagnostics predict remaining minutes after each observation; no future signal or total lifetime enters X",
                                "Accelerated laboratory degradation; does not establish production-domain performance",
                                "Adapter preserves a regression task; classification/HPO comparison must not mix its scores with fault classification"]}
    if original.is_file():
        metadata["input_archive"] = {"path": str(original.resolve()), "sha256": sha256(original)}
    return make_dataset("xjtu_sy", original, pd.DataFrame(rows), pd.Series(targets, name="remaining_life_minutes"),
                        regime_series, groups, splits, metadata, files, manifest_dir)
