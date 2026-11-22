"""Physical-bearing adapters; windows never become independent split units."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import time
from urllib.request import Request, urlopen
import zlib

import numpy as np
import pandas as pd
from scipy.io import loadmat
from scipy.stats import kurtosis, skew

from .common import grouped_splits, make_dataset, sha256, source_metadata, unpack_cached_archive, utc_now, write_json

PADERBORN_SOURCE = "https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter"
PADERBORN_FILES = "http://groups.uni-paderborn.de/kat/BearingDataCenter/"
MAT_NAME = re.compile(r"(?P<regime>N\d{2}_M\d{2}_F\d{2})_(?P<bearing>K(?:A|I)?\d{2,3})_(?P<record>\d+)\.mat$", re.I)
CONDITIONS = {"N15_M07_F10": (1500, .7, 1000), "N09_M07_F10": (900, .7, 1000),
              "N15_M01_F10": (1500, .1, 1000), "N15_M07_F04": (1500, .7, 400)}
# Three healthy and six accelerated-lifetime damaged bearings. No synthetic signal is used.
SMALL_BEARINGS = ["K001", "K002", "K003", "KA04", "KA15", "KA16", "KI04", "KI14", "KI16"]
SMALL_CONDITIONS = {"N15_M07_F10", "N09_M07_F10"}


def _range(url, start, end):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"Range": f"bytes={start}-{end}",
                                              "User-Agent": "RAC-HPO-data-audit/1"}), timeout=40) as response:
                if response.status != 206:
                    raise ValueError("Official server does not support bounded Range requests; full download not authorized by this helper")
                content = response.read(end - start + 2)
                if len(content) != end - start + 1:
                    raise ValueError("Official server returned a truncated or oversized byte range")
                return content
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def _fetch_one_bearing(bearing, destination):
    """Read RAR4 headers, skip unwanted payloads, extract exact official file bytes."""
    url = f"{PADERBORN_FILES}{bearing}.rar"
    prefix = _range(url, 0, 19)
    if prefix[:7] != b"Rar!\x1a\x07\x00" or prefix[9] != 0x73:
        raise ValueError("Selective download requires the published non-solid RAR4 format")
    main_flags = struct.unpack_from("<H", prefix, 10)[0]
    if main_flags & 0x89:
        raise ValueError("Solid/encrypted RAR archives cannot be partially imported")
    progress_path = destination / ".acquisition" / f"{bearing}.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    selected = progress.get("selected_records", [])
    if any(not (destination / item["archive_entry"]).is_file() or
           sha256(destination / item["archive_entry"]) != item["sha256"] for item in selected):
        raise ValueError("Partial Paderborn download files differ from saved acquisition hashes")
    seen = {MAT_NAME.search(item["archive_entry"])["regime"] for item in selected}
    if seen == SMALL_CONDITIONS:
        return selected
    offset = progress.get("next_offset", 7 + struct.unpack_from("<H", prefix, 12)[0])
    for _ in range(100):
        header = _range(url, offset, offset + 255)
        kind, flags, header_size = struct.unpack_from("<BHH", header, 2)
        if kind == 0x7b:
            break
        if header_size < 7 or header_size > 256:
            raise ValueError("Unexpected RAR header size")
        packed = struct.unpack_from("<I", header, 7)[0] if flags & 0x8000 else 0
        if kind == 0x74:
            if flags & 0x17 or flags & 0x100:
                raise ValueError("Split, solid, encrypted or large RAR entries require full local extraction")
            name_size = struct.unpack_from("<H", header, 26)[0]
            name = header[32:32 + name_size].decode("utf-8").replace("\\", "/")
            match = MAT_NAME.search(name)
            if match and match["bearing"] == bearing and match["regime"] in SMALL_CONDITIONS and match["regime"] not in seen:
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or ":" in name or len(relative.parts) != 2 or relative.parts[0] != bearing:
                    raise ValueError("Unsafe file path in the official RAR archive")
                if packed > 12_000_000:
                    raise ValueError("Official MAT record exceeds the small-validation limit")
                destination.mkdir(parents=True, exist_ok=True)
                entry = _range(url, offset, offset + header_size + packed - 1)
                # Repackage byte-for-byte compressed entries; no sensor values are altered.
                end = struct.pack("<BHH", 0x7b, 0x4000, 7)
                end = struct.pack("<H", zlib.crc32(end) & 0xffff) + end
                temporary = destination / f".{bearing}_{match['regime']}.rar"
                temporary.write_bytes(prefix + entry + end)
                try:
                    process = subprocess.run(["tar", "-xf", str(temporary), "-C", str(destination), name],
                                             capture_output=True, text=True, timeout=60)
                    if process.returncode:
                        raise ValueError(f"RAR extraction failed: {process.stderr.strip()}")
                    result = destination / name
                    if not result.is_file():
                        raise ValueError("Expected official MAT record missing after extraction")
                    selected.append({"archive_url": url, "byte_start": offset,
                                     "byte_end": offset + header_size + packed - 1,
                                     "archive_entry": name, "sha256": sha256(result)})
                    seen.add(match["regime"])
                finally:
                    temporary.unlink(missing_ok=True)
        offset += header_size + packed
        write_json(progress_path, {"bearing": bearing, "archive_url": url, "updated_at": utc_now(),
                                  "next_offset": offset, "selected_records": selected})
        if seen == SMALL_CONDITIONS:
            return selected
    raise ValueError(f"Could not find both required operating conditions for {bearing}")


def fetch_paderborn(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if shutil.which("tar") is None:
        raise RuntimeError("Paderborn selective download needs a trusted tar build with RAR4 support")
    try:
        sidecar = destination / "source.json"
        if sidecar.exists():
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            if all((destination / row["archive_entry"]).is_file() and
                   sha256(destination / row["archive_entry"]) == row["sha256"] for row in metadata.get("selected_records", [])) and len(metadata.get("selected_records", [])) == 18:
                return destination
            raise ValueError("Existing Paderborn provenance differs; preserve it and choose a new destination")
        with ThreadPoolExecutor(max_workers=9) as pool:
            records = sum(list(pool.map(lambda bearing: _fetch_one_bearing(bearing, destination), SMALL_BEARINGS)), [])
        write_json(sidecar, {"dataset_key": "paderborn", "source_url": PADERBORN_SOURCE,
                             "license": "CC BY-NC 4.0", "acquired_at": utc_now(),
                             "acquisition_method": "Official HTTP RAR4 byte ranges; exact compressed entries extracted using tar",
                             "selected_records": records, "bearings": SMALL_BEARINGS,
                             "selection_policy": "One first-encountered recording per bearing and each of two predetermined operating conditions; no labels/features used for record selection"})
    except Exception as error:
        write_json(destination / "download_failure.json", {"dataset_key": "paderborn", "attempted_at": utc_now(),
                   "source_url": PADERBORN_FILES, "error_type": type(error).__name__, "reason": str(error),
                   "substitution_used": False})
        raise
    return destination


def signal_features(signal, prefix):
    signal = np.asarray(signal, dtype=float).reshape(-1)
    if len(signal) < 16 or not np.isfinite(signal).all():
        raise ValueError("Sensor record must contain at least 16 finite observations")
    rms = float(np.sqrt(np.mean(signal ** 2)))
    standard = float(np.std(signal))
    spectrum = np.abs(np.fft.rfft(signal - signal.mean())) ** 2
    energy = max(float(spectrum.sum()), 1e-20)
    bands = np.array_split(spectrum, 4)
    values = {"mean": float(signal.mean()), "std": standard, "rms": rms,
              "peak_to_peak": float(np.ptp(signal)), "crest_factor": float(np.max(np.abs(signal)) / max(rms, 1e-12)),
              "skew": float(skew(signal)) if standard > 1e-12 else 0.,
              "kurtosis": float(kurtosis(signal)) if standard > 1e-12 else 0.}
    values.update({f"spectral_band_{index}": float(band.sum() / energy) for index, band in enumerate(bands)})
    return {f"{prefix}_{name}": value for name, value in values.items()}


def prepare_paderborn(path, manifest_dir=None, archive_cache_dir=None):
    original = Path(path)
    root = original if original.is_dir() else unpack_cached_archive(
        original, archive_cache_dir or Path.cwd() / "datasets/processed/industrial/paderborn")
    files = sorted(root.rglob("*.mat"))
    if not files:
        raise ValueError("Paderborn import contains no official-named MAT sensor records")
    rows, units, regimes, labels, mapping = [], [], [], [], []
    for file in files:
        match = MAT_NAME.fullmatch(file.name)
        if not match:
            raise ValueError(f"Unrecognized Paderborn MAT filename: {file.name}")
        bearing, regime = match["bearing"], match["regime"]
        if regime not in CONDITIONS or bearing.startswith("KB"):
            raise ValueError("Unsupported operating condition or combined damage; keep labels separate")
        label = 2 if bearing.startswith("KI") else 1 if bearing.startswith("KA") else 0
        content = loadmat(file, simplify_cells=True)
        value = content.get(file.stem)
        if not isinstance(value, dict) or "Y" not in value:
            raise ValueError(f"Paderborn MAT must contain the published Y signal structure: {file.name}")
        sensors = value["Y"] if isinstance(value["Y"], (list, np.ndarray)) else [value["Y"]]
        signals = {str(item.get("Name", "")).lower(): np.asarray(item.get("Data", []), dtype=float).reshape(-1)
                   for item in sensors if isinstance(item, dict)}
        selected = {name: data for name, data in signals.items() if "vibration" in name or "current" in name}
        if not selected:
            raise ValueError(f"No named vibration/current channels in {file.name}")
        # Eight non-overlapping windows per measured record; split assignment remains the physical bearing.
        chunks = {name: np.array_split(data, 8) for name, data in selected.items()}
        for window in range(8):
            features = {}
            for name, signals_by_window in chunks.items():
                clean_name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
                features.update(signal_features(signals_by_window[window], clean_name))
            speed, torque, force = CONDITIONS[regime]
            features.update({"rotational_speed_rpm": speed, "load_torque_nm": torque, "radial_force_n": force})
            rows.append(features); units.append(bearing); regimes.append(regime); labels.append(label)
            mapping.append({"file": str(file.relative_to(root)), "window": window, "bearing_id": bearing})
    X, y = pd.DataFrame(rows), pd.Series(labels, name="bearing_damage")
    if X.isna().any().any():
        raise ValueError("Paderborn channels must have identical names across records")
    groups = pd.Series(units)
    splits = grouped_splits(groups, y)
    source = source_metadata(root / "unused")
    records = source.get("selected_records", [])
    verified = len(records) == len(files) and all((root / record["archive_entry"]).is_file() and
                sha256(root / record["archive_entry"]) == record["sha256"] for record in records)
    metadata = {"data_origin": "real_measurement", "source_url": PADERBORN_SOURCE,
                "license": "CC BY-NC 4.0", "acquired_at": source.get("acquired_at"),
                "citation": "Lessmeier, C.; Kimotho, J. K.; Zimmer, D.; Sextro, W. (2016), Condition Monitoring of Bearing Damage in Electromechanical Drive Systems by Using Motor Current Signals of Electric Motors: A Benchmark Data Set for Data-Driven Classification, European Conference of the Prognostics and Health Management Society; KAt DataCenter, Paderborn University",
                "provenance_verified": verified, "source_acquisition": source,
                "classes": {"0": "healthy", "1": "outer_race_damage", "2": "inner_race_damage"},
                "independent_unit_kind": "physical_bearings", "group_definition": "Official physical bearing code; all conditions/records/windows remain in one split",
                "regime_definition": "Known rotational speed / load torque / radial force from official N/M/F setting",
                "split_strategy": "Lexicographic physical-bearing split within damage class, approximately 60/20/20 with at least one bearing per class per split",
                "source_window_mapping": mapping, "excluded_fields": ["bearing_id", "record_number", "damage_class"],
                "limitations": ["Laboratory test rig measurements, not a live factory deployment", "Includes accelerated-life and/or artificially damaged bearings; damage method must not be conflated with naturally occurring field failure",
                                "Eight windows from each record are correlated, never eight independent bearings", "Small subset has one validation bearing per class; no calibrated reliability claim",
                                "Noncommercial license; commercial reuse requires provider permission"]}
    if original.is_file():
        metadata["input_archive"] = {"path": str(original.resolve()), "sha256": sha256(original)}
    return make_dataset("paderborn", original, X, y, pd.Series(regimes), groups, splits, metadata,
                        files, manifest_dir)
