"""Verified UCI Steel download and a conservative calendar-only regression adapter."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

import pandas as pd

from rac_hpo._io import atomic_text
from rac_hpo.domain.schemas import PreparedDataset
from .splitter import split_calendar_days
from .validator import STEEL_COLUMNS, validate_steel_frame


SOURCE_URL = "https://archive.ics.uci.edu/dataset/851/steel+industry+energy+consumption"
DOWNLOAD_URL = "https://archive.ics.uci.edu/static/public/851/steel+industry+energy+consumption.zip"
OFFICIAL_SHA256 = "9b1cee6f9cb9cd9df2b95814ca90a9a2ff15b7f5f1fba0fae3c643e82072eacc"
FEATURES = ["hour", "minute", "day_of_week", "month", "is_weekend"]
SOURCE_METADATA = {
    "dataset_id": "uci_steel_energy_851",
    "dataset_version": "uci-851-sha256-9b1cee6f9cb9",
    "source_url": SOURCE_URL,
    "download_url": DOWNLOAD_URL,
    "doi": "10.24432/C52G8C",
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/legalcode",
    "citation": "V E, S., Shin, C., & Cho, Y. (2021). Steel Industry Energy Consumption [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C52G8C.",
    "verified_on": "2026-10-30",
    "official_rows": 35040,
    "official_sha256": OFFICIAL_SHA256,
}


def download_steel(destination: str | Path) -> Path:
    """Download the pinned official CSV; preserve existing files on errors or drift."""
    destination = Path(destination)
    output = destination if destination.suffix.lower() == ".csv" else destination / "Steel_industry_data.csv"
    if output.exists():
        if sha256(output.read_bytes()).hexdigest() == OFFICIAL_SHA256:
            return output
        raise ValueError(f"Existing file differs from the verified UCI dataset: {output}")
    try:
        with urlopen(DOWNLOAD_URL, timeout=60) as response:
            archive = response.read(20_000_001)
        if len(archive) > 20_000_000:
            raise ValueError("Steel download exceeds the expected archive size limit.")
        with ZipFile(BytesIO(archive)) as zipped:
            csv_bytes = zipped.read("Steel_industry_data.csv")
    except (OSError, URLError, BadZipFile, KeyError) as error:
        raise ValueError(f"Could not download the official Steel dataset: {error}") from error
    if sha256(csv_bytes).hexdigest() != OFFICIAL_SHA256:
        raise ValueError("Official Steel CSV checksum changed; verify the source before accepting a new version.")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(csv_bytes)
    except OSError as error:
        raise ValueError(f"Could not save the official Steel dataset: {error}") from error
    return output


def prepare_steel_dataset(path: str | Path, manifest_dir: str | Path | None = None) -> PreparedDataset:
    """Load a local CSV, derive known calendar inputs, and freeze chronological splits."""
    source = Path(path)
    try:
        raw_bytes = source.read_bytes()
        frame = pd.read_csv(BytesIO(raw_bytes))
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError) as error:
        raise ValueError(f"Could not read Steel CSV: {error}") from error
    timestamps, target = validate_steel_frame(frame)
    chronological = timestamps.sort_values(kind="stable").index
    source_row_indices = chronological.tolist()
    timestamps = timestamps.iloc[chronological].reset_index(drop=True)
    y = target.iloc[chronological].reset_index(drop=True).rename("Usage_kWh")
    X = pd.DataFrame({
        "hour": timestamps.dt.hour,
        "minute": timestamps.dt.minute,
        "day_of_week": timestamps.dt.dayofweek,
        "month": timestamps.dt.month,
        "is_weekend": (timestamps.dt.dayofweek >= 5).astype(int),
    }, columns=FEATURES)
    regimes = X["is_weekend"].map({0: "weekday", 1: "weekend"}).rename("regime_id")
    groups = timestamps.dt.strftime("%Y-%m-%d").rename("date_group")
    splits, date_splits = split_calendar_days(groups)
    for split_name in ("train", "validation", "test"):
        if set(regimes.iloc[splits[split_name]]) != {"weekday", "weekend"}:
            raise ValueError(f"Split {split_name} needs both pre-defined weekday and weekend regimes.")
    source_hash = sha256(raw_bytes).hexdigest()
    official_source = source_hash == OFFICIAL_SHA256
    source_metadata = SOURCE_METADATA.copy() if official_source else {
        "dataset_id": f"user_supplied_steel_{source_hash[:12]}",
        "source_url": None, "download_url": None, "doi": None,
        "license": "unknown", "license_url": None, "citation": None,
        "verified_on": None,
    }
    manifest = {
        **source_metadata,
        "adapter_reference": SOURCE_METADATA.copy(),
        "source_type": "official_uci" if official_source else "user_supplied",
        "provenance_verified": official_source,
        "prepared_version": "steel-calendar-v1",
        "dataset_version": f"steel-calendar-v1-sha256-{source_hash[:12]}",
        "source_sha256": source_hash,
        "matches_official_sha256": official_source,
        "source_verification": "official-byte-identical" if official_source else "local-import-not-official-byte-identical",
        "source_columns": frame.columns.tolist(),
        "task": "calendar-based industry energy consumption regression",
        "target": "Usage_kWh",
        "target_unit": "kWh",
        "feature_order": FEATURES.copy(),
        "feature_units": {"hour": "hour", "minute": "minute", "day_of_week": "Monday=0..Sunday=6", "month": "1..12", "is_weekend": "0/1"},
        "excluded_fields": [column for column in STEEL_COLUMNS if column not in ("date", "Usage_kWh")],
        "exclusion_reason": "Contemporaneous power, emissions, and Load_Type may be target-derived or unavailable before prediction; NSM and text calendar labels are replaced by parsed date features.",
        "regime_definition": {"weekday": "calendar day_of_week < 5", "weekend": "calendar day_of_week >= 5"},
        "group_definition": "Calendar dates are correlated time blocks, not independent machining runs, tools, batches, or machines.",
        "independent_batch_count": None,
        "reliability": "insufficient evidence for independent manufacturing-batch inference",
        "preprocessing": {"version": "steel-calendar-v1", "statistics_fitted": False, "missing_values": "reject", "sort": "stable by parsed date timestamp", "timestamp_semantics": "Use date labels as supplied, including same-date 00:00 rows; do not relabel to the next day."},
        "rows": len(frame),
        "date_range": [str(timestamps.min()), str(timestamps.max())],
        "split_strategy": "chronological whole-calendar-day blocks; approximate 60/20/20 retained days; one entire date excluded at both boundaries",
        "gap_days": 1,
        "split_index_kind": "zero-based iloc positions in chronological PreparedDataset",
        "source_row_indices": source_row_indices,
        "splits": splits,
        "split_dates": date_splits,
        "split_summary": {
            name: {
                "rows": len(indices), "calendar_dates": len(date_splits[name]),
                "regime_rows": regimes.iloc[indices].value_counts().to_dict(),
                "regime_calendar_dates": {
                    regime: int(groups.iloc[indices][regimes.iloc[indices] == regime].nunique())
                    for regime in ("weekday", "weekend")
                },
            } for name, indices in splits.items()
        },
        "test_policy": "Search and early stopping may only use train/validation; test labels remain sealed until explicit final evaluation.",
    }
    if manifest_dir is not None:
        directory = Path(manifest_dir)
        manifest_path = directory / f"steel_calendar_v1_{source_hash[:12]}.json"
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            try:
                atomic_text(manifest_path, encoded, overwrite=False)
            except FileExistsError:
                if manifest_path.read_text(encoding="utf-8") != encoded:
                    raise ValueError(f"Existing dataset manifest differs; preserve it and use a new version directory: {manifest_path}")
        except OSError as error:
            raise ValueError(f"Could not save the dataset manifest: {error}") from error
    return PreparedDataset(
        dataset_id=source_metadata["dataset_id"], source_path=str(source.resolve()),
        source_sha256=source_hash, features=FEATURES.copy(), target="Usage_kWh",
        target_unit="kWh", X=X, y=y, regimes=regimes, groups=groups,
        splits=splits, manifest=manifest,
    )
