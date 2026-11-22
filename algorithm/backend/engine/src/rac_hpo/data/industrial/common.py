"""Shared provenance, immutable splits and bounded archive extraction."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import os
import shutil
import tarfile
import uuid
import zipfile

import numpy as np
import pandas as pd

from rac_hpo._io import atomic_text
from rac_hpo.domain.schemas import PreparedDataset


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    atomic_text(Path(path), json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def source_metadata(path):
    sidecar = Path(path).parent / "source.json"
    return json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}


def make_dataset(key, path, X, y, regimes, groups, splits, metadata, source_files,
                 manifest_dir=None):
    """No statistics are fitted here. Test rows remain explicitly held out."""
    task_type = metadata.get("task_type", "classification")
    X, y = X.reset_index(drop=True), y.reset_index(drop=True)
    y = y.astype(int if task_type == "classification" else float)
    regimes, groups = regimes.reset_index(drop=True).astype(str), groups.reset_index(drop=True).astype(str)
    if not len(X) or len(X) != len(y) or set(splits) != {"train", "validation", "test"}:
        raise ValueError("Dataset rows and train/validation/test splits must be present")
    indices = [int(index) for name in splits for index in splits[name]]
    if len(indices) != len(X) or sorted(indices) != list(range(len(X))):
        raise ValueError("Dataset splits must be disjoint and cover each source row exactly once")
    if any(not splits[name] for name in splits):
        raise ValueError("Each dataset split must contain observations")
    if task_type == "classification" and (y.nunique() < 2 or y.iloc[splits["train"]].nunique() != y.nunique()):
        raise ValueError("Training split must include every declared target class")
    if not np.isfinite(X.to_numpy(dtype=float)[~X.isna().to_numpy()]).all():
        raise ValueError("Features may contain missing values but never infinite values")
    files = [{"path": str(Path(item).resolve()), "sha256": sha256(item)} for item in source_files]
    common_root = Path(os.path.commonpath([str(Path(item).resolve().parent) for item in source_files]))
    content_identity = [{"relative_path": str(Path(item["path"]).relative_to(common_root)).replace("\\", "/"),
                         "sha256": item["sha256"]} for item in files]
    aggregate = hashlib.sha256(json.dumps(content_identity, sort_keys=True).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps({"source_sha256": aggregate, "splits": splits,
                                           "features": list(X), "target": str(y.name),
                                           "task_type": task_type, "classes": metadata.get("classes")}, sort_keys=True).encode()).hexdigest()
    manifest = {
        "dataset_id": key, "dataset_key": key, "dataset_version": f"{key}-v1-{aggregate[:12]}",
        "task_type": task_type, "target": str(y.name), "target_unit": metadata.get("target_unit", "class"),
        "rows": len(X), "feature_order": list(X), "source_sha256": aggregate,
        "source_files": files, "splits": splits, "split_fingerprint": fingerprint,
        "source_row_indices": list(range(len(X))), "acquired_at": None,
        "preprocessing": {"statistics_fitted": False, "missing_values": "train-fold imputation required"},
        **metadata,
    }
    manifest["split_summary"] = {
        name: {"rows": len(rows), "groups": int(groups.iloc[rows].nunique()),
               "regime_rows": {str(k): int(v) for k, v in regimes.iloc[rows].value_counts().items()},
               **({"class_rows": {str(k): int(v) for k, v in y.iloc[rows].value_counts().items()}}
                  if task_type == "classification" else {"target_min": float(y.iloc[rows].min()),
                                                         "target_max": float(y.iloc[rows].max())})}
        for name, rows in splits.items()
    }
    if manifest_dir is not None:
        destination = Path(manifest_dir) / f"{key}_{fingerprint[:16]}.json"
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            atomic_text(destination, encoded, overwrite=False)
        except FileExistsError:
            if destination.read_text(encoding="utf-8") != encoded:
                raise ValueError("Existing immutable dataset manifest differs")
    return PreparedDataset(key, str(Path(path).resolve()), aggregate, list(X), str(y.name),
                           manifest["target_unit"], X, y, regimes, groups, splits, manifest)


def chronological_splits(count):
    boundaries = (int(count * .6), int(count * .8))
    return {"train": list(range(boundaries[0])),
            "validation": list(range(boundaries[0], boundaries[1])),
            "test": list(range(boundaries[1], count))}


def grouped_splits(groups, labels):
    """Target class stratification uses physical units, never signal windows."""
    table = pd.DataFrame({"group": groups, "label": labels}).drop_duplicates()
    if table.groupby("group")["label"].nunique().max() != 1:
        raise ValueError("Each physical group must have one class for grouped classification")
    split_groups = {name: [] for name in ("train", "validation", "test")}
    for label in sorted(table.label.unique()):
        units = sorted(table.loc[table.label == label, "group"].tolist())
        if len(units) < 3:
            raise ValueError("Each class requires at least three physical groups for train/validation/test")
        # Deterministic assignment; no random window leakage and no feature-based selection.
        n_train = max(1, len(units) - 2 * max(1, int(len(units) * .2)))
        n_val = max(1, (len(units) - n_train) // 2)
        split_groups["train"].extend(units[:n_train])
        split_groups["validation"].extend(units[n_train:n_train + n_val])
        split_groups["test"].extend(units[n_train + n_val:])
    return {name: np.flatnonzero(groups.isin(units)).tolist() for name, units in split_groups.items()}


def unpack_archive(path, destination, max_bytes=8 * 1024 ** 3):
    """Extract ZIP/TAR/RAR4 regular files; reject links, traversal and bombs."""
    path, destination = Path(path), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".rar":
        from .archives import unpack_rar4
        return unpack_rar4(path, destination, max_bytes)
    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        entries = [(item.filename, item.file_size, item.is_dir(), bool(item.external_attr >> 16 & 0o170000 == 0o120000), item)
                   for item in archive.infolist()]
        opener = archive.open
    elif tarfile.is_tarfile(path):
        archive = tarfile.open(path)
        entries = [(item.name, item.size, item.isdir(), not (item.isfile() or item.isdir()), item)
                   for item in archive.getmembers()]
        opener = archive.extractfile
    else:
        raise ValueError("Supported local archives are ZIP and TAR; RAR must first be extracted")
    try:
        if sum(size for _, size, _, _, _ in entries) > max_bytes or len(entries) > 100000:
            raise ValueError("Archive exceeds the explicit import size limit")
        for name, _, is_directory, unsafe, _ in entries:
            normalized = PurePosixPath(name.replace("\\", "/"))
            target = (destination / str(normalized)).resolve()
            if unsafe or normalized.is_absolute() or ".." in normalized.parts or ":" in name or not target.is_relative_to(destination):
                raise ValueError(f"Unsafe archive member: {name}")
        for name, _, is_directory, _, item in entries:
            target = destination / name.replace("\\", "/")
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(f"Archive import would overwrite an existing file: {target}")
            with opener(item) as source, target.open("xb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
    finally:
        archive.close()
    return destination


def unpack_cached_archive(path, cache_root):
    """Extract once inside the workspace and verify every cached file on reuse."""
    path = Path(path).resolve()
    cache_root = Path(cache_root).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    digest = sha256(path)
    destination = cache_root / digest

    def verify():
        marker = destination / "archive_manifest.json"
        data_dir = destination / "data"
        if (destination.is_symlink() or marker.is_symlink() or data_dir.is_symlink()
                or not destination.resolve().is_relative_to(cache_root)):
            raise ValueError("Archive cache must not contain a symlink or escape its workspace")
        if not marker.is_file() or not data_dir.is_dir():
            raise ValueError(f"Incomplete archive cache; preserve and inspect: {destination}")
        recorded = json.loads(marker.read_text(encoding="utf-8"))
        if recorded.get("archive_sha256") != digest:
            raise ValueError("Cached archive identity does not match the source")
        entries = list(data_dir.rglob("*"))
        if any(entry.is_symlink() or not entry.resolve().is_relative_to(data_dir.resolve()) for entry in entries):
            raise ValueError("Archive cache must not contain a symlink or escape its workspace")
        actual = {file.relative_to(data_dir).as_posix(): sha256(file)
                  for file in entries if file.is_file()}
        if actual != recorded.get("files"):
            raise ValueError(f"Cached archive content changed; preserve and inspect: {destination}")
        return data_dir

    if destination.exists():
        return verify()
    temporary = (cache_root / f".{digest}.{uuid.uuid4().hex}.tmp").resolve()
    if not temporary.is_relative_to(cache_root):
        raise ValueError("Archive cache must stay within its configured workspace directory")
    try:
        temporary.mkdir()
        data_dir = unpack_archive(path, temporary / "data")
        files = {file.relative_to(data_dir).as_posix(): sha256(file)
                 for file in data_dir.rglob("*") if file.is_file()}
        if sha256(path) != digest:
            raise ValueError("Source archive changed while being extracted")
        write_json(temporary / "archive_manifest.json", {"archive_sha256": digest, "files": files})
        try:
            os.rename(temporary, destination)
        except OSError:
            if not destination.is_dir():
                raise
        return verify()
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
