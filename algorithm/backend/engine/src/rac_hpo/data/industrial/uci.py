"""UCI adapters preserve the distinction between synthetic and measured data."""
from pathlib import Path
import json
import socket

import numpy as np
import pandas as pd

from .common import chronological_splits, make_dataset, sha256, source_metadata, utc_now, write_json

SOURCES = {
    "ai4i_2020": (601, "https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset"),
    "secom": (179, "https://archive.ics.uci.edu/dataset/179/secom"),
}
AI4I_FEATURES = ["Type", "Air temperature [K]", "Process temperature [K]",
                 "Rotational speed [rpm]", "Torque [Nm]", "Tool wear [min]"]


def fetch_uci(key, destination):
    path = Path(destination)
    if path.suffix.lower() != ".csv":
        path = path / "data.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = source_metadata(path)
        if existing.get("dataset_key") == key and existing.get("sha256") == sha256(path):
            return path
        raise FileExistsError("Existing UCI CSV has no matching provenance; preserving it")
    previous_timeout = socket.getdefaulttimeout()
    try:
        # Acquisition runs in a dedicated process; bound ucimlrepo's urllib calls.
        socket.setdefaulttimeout(60)
        from ucimlrepo import fetch_ucirepo
        dataset = fetch_ucirepo(id=SOURCES[key][0])
        frame = dataset.data.original.copy()
        if key == "secom":
            # ucimlrepo original includes the TimeStamp ID, which is excluded from X.
            columns = {str(col).lower(): col for col in frame}
            if not any("time" in name or "date" in name for name in columns):
                raise ValueError("UCI SECOM response omits the required measurement timestamp")
        frame.to_csv(path, index=False, lineterminator="\n")
        write_json(path.parent / "source.json", {
            "dataset_key": key, "source_url": SOURCES[key][1], "repository_id": SOURCES[key][0],
            "license": "CC BY 4.0", "acquired_at": utc_now(), "sha256": sha256(path),
            "acquisition_method": "ucimlrepo.fetch_ucirepo", "repository_metadata": dict(dataset.metadata),
        })
    except Exception as error:
        write_json(path.parent / "download_failure.json", {
            "dataset_key": key, "source_url": SOURCES[key][1], "attempted_at": utc_now(),
            "error_type": type(error).__name__, "reason": str(error), "substitution_used": False,
        })
        raise RuntimeError(f"UCI acquisition failed for {key}: {error}") from error
    finally:
        socket.setdefaulttimeout(previous_timeout)
    return path


def prepare_ai4i(path, manifest_dir=None):
    path = Path(path)
    if path.is_dir():
        path = path / "data.csv"
    frame = pd.read_csv(path)
    aliases = {name.split(" [")[0]: name for name in AI4I_FEATURES if " [" in name}
    renamed = {name: canonical for name, canonical in aliases.items() if name in frame and canonical not in frame}
    frame = frame.rename(columns=renamed)
    if not set(AI4I_FEATURES + ["Machine failure"]).issubset(frame):
        raise ValueError("AI4I CSV must include Type, five sensors and Machine failure")
    if not frame.Type.isin(["L", "M", "H"]).all():
        raise ValueError("AI4I Type must be L, M or H")
    X = frame[AI4I_FEATURES].copy()
    # A fixed documented ordinal encoding does not fit validation/test statistics.
    X["Type"] = X.Type.map({"L": 0, "M": 1, "H": 2})
    X = X.apply(pd.to_numeric, errors="raise")
    y = pd.to_numeric(frame["Machine failure"], errors="raise").rename("machine_failure")
    if X.isna().any().any() or not y.isin([0, 1]).all():
        raise ValueError("AI4I requires finite sensors and a binary Machine failure label")
    source = source_metadata(path)
    verified = source.get("dataset_key") == "ai4i_2020" and source.get("sha256") == sha256(path)
    metadata = {
        "data_origin": "synthetic", "source_url": SOURCES["ai4i_2020"][1], "license": "CC BY 4.0",
        "acquired_at": source.get("acquired_at"), "provenance_verified": verified,
        "classes": {"0": "no_failure", "1": "machine_failure"},
        "independent_unit_kind": "synthetic_row", "group_definition": "No verified physical equipment ID",
        "regime_definition": "Known product quality Type L/M/H", "split_strategy": "Source-row chronological 60/20/20; UDI and product IDs excluded",
        "excluded_fields": [str(name) for name in frame if name not in AI4I_FEATURES],
        "feature_name_normalization": renamed,
        "feature_units_source": "Official UCI variable metadata; aliases do not change raw source CSV",
        "limitations": ["Synthetic benchmark; never real production data", "Rows are correlated; no physical equipment or batch IDs verified",
                        "Failure mechanisms are defined from sensors; results do not establish field maintenance performance"],
    }
    return make_dataset("ai4i_2020", path, X, y, frame.Type.rename("regime_id"),
                        pd.Series([f"synthetic_row_{i}" for i in range(len(frame))]),
                        chronological_splits(len(frame)), metadata, [path], manifest_dir)


def prepare_secom(path, manifest_dir=None):
    path = Path(path)
    if path.is_dir() and (path / "secom.data").exists():
        features_path, labels_path = path / "secom.data", path / "secom_labels.data"
        X = pd.read_csv(features_path, sep=r"\s+", header=None)
        labels = pd.read_csv(labels_path, sep=r"\s+", header=None)
        y = labels.iloc[:, 0]
        timestamps = labels.iloc[:, 1].astype(str) + " " + labels.iloc[:, 2].astype(str)
        files = [features_path, labels_path]
    else:
        if path.is_dir():
            path = path / "data.csv"
        frame = pd.read_csv(path)
        time_columns = [col for col in frame if any(token in str(col).lower() for token in ("time", "date"))]
        label_columns = [col for col in frame if str(col).lower() in ("label", "class", "failure", "pass/fail", "pass_fail")]
        if len(time_columns) != 1 or len(label_columns) != 1:
            raise ValueError("SECOM CSV requires one timestamp and one Label column")
        timestamps, y = frame[time_columns[0]], frame[label_columns[0]]
        X = frame.drop(columns=time_columns + label_columns)
        files = [path]
    # Published rows mix zero-padded dates and optional seconds; preserve day-first meaning.
    parsed = pd.to_datetime(timestamps, dayfirst=True, format="mixed", errors="raise")
    order = np.argsort(parsed.to_numpy(), kind="stable")
    X = X.iloc[order].reset_index(drop=True).apply(pd.to_numeric, errors="raise")
    X.columns = [f"sensor_{i:03}" for i in range(X.shape[1])]
    y = pd.to_numeric(y.iloc[order].reset_index(drop=True), errors="raise")
    if X.shape[1] < 1 or not y.isin([-1, 1]).all() or len(y) != len(X):
        raise ValueError("SECOM labels must use the official -1 pass / +1 fail coding")
    y = (y == 1).astype(int).rename("failure")
    source = source_metadata(files[0])
    metadata = {
        "data_origin": "real_measurement", "source_url": SOURCES["secom"][1], "license": "CC BY 4.0",
        "acquired_at": source.get("acquired_at"), "classes": {"0": "pass", "1": "fail"},
        "provenance_verified": source.get("dataset_key") == "secom" and source.get("sha256") == sha256(files[0]),
        "independent_unit_kind": "production_entity", "group_definition": "One production entity per row; device/batch IDs unavailable",
        "regime_definition": "overall: no verified operating-condition metadata", "split_strategy": "Stable measurement-time order 60/20/20",
        "timestamps": parsed.iloc[order].astype(str).tolist(), "source_row_indices": [int(i) for i in order],
        "excluded_fields": ["TimeStamp", "Label"],
        "limitations": ["Anonymized sensors have no verified engineering meanings", "Missing/constant sensors require training-fold-only preprocessing",
                        "No independent machine or batch IDs; overall is not a multi-condition benchmark"],
    }
    return make_dataset("secom", path, X, y, pd.Series("overall", index=X.index),
                        pd.Series([f"production_entity_{i}" for i in order]), chronological_splits(len(X)),
                        metadata, files, manifest_dir)
