"""Author-published 2025 CNC measurements; hold out entire physical tools."""
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

from rac_hpo._io import atomic_text
from rac_hpo.domain.schemas import PreparedDataset

DOWNLOAD_URL = "https://ndownloader.figshare.com/files/52968974"
SOURCE_URL = "https://doi.org/10.6084/m9.figshare.28589216.v1"
OFFICIAL_SHA256 = "e125471f983e98ae326e2c47e15f633bb30738e285b9ad6e54b5e364d4d66dbc"
OFFICIAL_MD5 = "cbcad3f9631e0be6c4f71ff27749108c"
METADATA_COLUMNS = ["FileName", "NumberOfCycle", "SampleIndex", "TollIndex", "MillingToolType",
                    "ADOC", "RDOC", "HardnessMean", "ToolHolderLength", "CycleToFailure",
                    "CycleToFailureNormalized"]
PROCESS_FEATURES = ["MillingToolType", "ADOC", "RDOC", "HardnessMean", "ToolHolderLength"]


def download_cnc_milling(destination: str | Path) -> Path:
    path = Path(destination)
    if path.suffix.lower() != ".csv":
        path = path / "FeatureAndMetadata_Milling.csv"
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != OFFICIAL_SHA256:
            raise ValueError("Existing CNC file differs from the pinned author file; preserving it")
        return path
    with urlopen(DOWNLOAD_URL, timeout=45) as response:
        content = response.read(3_000_001)
    if hashlib.sha256(content).hexdigest() != OFFICIAL_SHA256:
        raise ValueError("CNC author file checksum changed; verify the published version")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    return path


def prepare_cnc_milling(path: str | Path, manifest_dir=None) -> PreparedDataset:
    source = Path(path)
    try:
        content = source.read_bytes()
        # The author file has a generic first header, then the real header.
        frame = pd.read_csv(io.BytesIO(content), sep=";", header=1)
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not read author CNC feature CSV: {error}") from error
    if frame.empty or list(frame.columns[-11:]) != METADATA_COLUMNS or frame.shape[1] != 131:
        raise ValueError("CNC file must contain the published 120 sensor features and 11 metadata columns")
    sensors = list(frame.columns[:120])
    if not all(name.startswith(("Accelerometer - ", "Current - ")) for name in sensors):
        raise ValueError("Unexpected CNC sensor feature schema")
    numeric = frame.drop(columns="FileName").astype(str).apply(
        lambda column: pd.to_numeric(column.str.replace(",", ".", regex=False), errors="raise")
    )
    if not np.isfinite(numeric.to_numpy()).all() or frame["FileName"].isna().any():
        raise ValueError("CNC measurements and metadata must be finite and complete")
    if frame["FileName"].duplicated().any():
        raise ValueError("CNC cycles must have unique file identifiers")
    if (numeric["CycleToFailure"] < 0).any() or not numeric["ADOC"].isin([5, 10]).all():
        raise ValueError("CNC remaining cycles must be nonnegative and ADOC must be 5 or 10 mm")
    for name in ("TollIndex", "SampleIndex", "NumberOfCycle", "CycleToFailure"):
        if (numeric[name] != numeric[name].astype(int)).any():
            raise ValueError(f"CNC {name} must be an integer")
    groups = numeric["TollIndex"].astype(int).astype(str).rename("tool_id")
    regimes = numeric["ADOC"].astype(int).map({5: "ADOC_5mm", 10: "ADOC_10mm"}).rename("regime_id")
    group_regimes = pd.DataFrame({"group": groups, "regime": regimes}).groupby("group")["regime"]
    if (group_regimes.nunique() != 1).any():
        raise ValueError("Each physical tool must have one predeclared depth regime")
    # Fixed, target-independent tool allocation: 4/2/1 tools per depth regime.
    rng = np.random.default_rng(42)
    split_groups = {name: [] for name in ("train", "validation", "test")}
    for regime in sorted(regimes.unique()):
        tools = sorted(groups[regimes == regime].unique(), key=int)
        if len(tools) < 5:
            raise ValueError("Each CNC depth regime needs at least five physical tools for grouped splitting")
        tools = list(rng.permutation(tools))
        validation_count = max(1, round(len(tools) * 2 / 7))
        train_count = len(tools) - validation_count - 1
        for name, selected in (("train", tools[:train_count]),
                               ("validation", tools[train_count:-1]), ("test", tools[-1:])):
            split_groups[name].extend(str(value) for value in selected)
    splits = {name: np.flatnonzero(groups.isin(tools)).tolist() for name, tools in split_groups.items()}
    features = sensors + PROCESS_FEATURES
    checksum = hashlib.sha256(content).hexdigest()
    official = checksum == OFFICIAL_SHA256
    manifest = {
        "dataset_id": "cnc_milling_2025" if official else f"user_cnc_{checksum[:12]}",
        "dataset_version": f"cnc-tool-group-v1-{checksum[:12]}", "source_sha256": checksum,
        "source_url": SOURCE_URL if official else None, "download_url": DOWNLOAD_URL if official else None,
        "license": "CC BY 4.0" if official else "unknown", "provenance_verified": official,
        "citation": "Piecuch, G. & Żabiński, T. (2025), CNC Milling Process Dataset, doi:10.6084/m9.figshare.28589216.v1" if official else None,
        "paper_url": "https://www.nature.com/articles/s41597-025-04923-y",
        "target": "CycleToFailure", "target_unit": "cycles", "rows": len(frame),
        "feature_order": features, "regime_definition": "Predeclared axial cutting depth ADOC: 5 or 10 mm",
        "independent_unit_kind": "physical_tool", "group_definition": "Author TollIndex denotes 14 physical cutters; one machine, not 968 independent tools",
        "independent_batch_count": int(groups.nunique()), "split_groups": split_groups, "splits": splits,
        "split_strategy": "Fixed seed 42, entire physical tools held out, stratified by ADOC without target access",
        "source_row_indices": list(range(len(frame))),
        "preprocessing": {"version": "cnc-tool-group-v1", "header_row": 1, "separator": ";",
                          "decimal_comma_metadata": True, "statistics_fitted": False, "missing_values": "reject"},
        "excluded_fields": [name for name in METADATA_COLUMNS if name not in PROCESS_FEATURES],
        "prediction_time": "After observing the current milling cycle, estimate remaining cycles; current-cycle signal summaries are known",
        "target_leakage_controls": "Exclude both failure labels, file/sample/tool IDs and cycle number; no future signals or lifetime-normalized feature",
        "limitations": ["Only 14 cutters on one machine; grouped validation has two tools per depth regime",
                        "Small-sample regimes have insufficient independent-tool evidence for confidence claims",
                        "Author aggregation is reused; raw 25 GB sensor archive was not downloaded or independently recomputed",
                        "Single-cycle premature failure is retained; no target-based outlier removal"],
        "split_summary": {name: {"rows": len(indices), "tools": len(split_groups[name]),
                                  "regime_rows": regimes.iloc[indices].value_counts().to_dict()}
                          for name, indices in splits.items()},
    }
    if manifest_dir is not None:
        target = Path(manifest_dir) / f"cnc_tool_group_v1_{checksum[:12]}.json"
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            atomic_text(target, encoded, overwrite=False)
        except FileExistsError:
            if target.read_text(encoding="utf-8") != encoded:
                raise ValueError("Existing CNC split manifest differs; use a new explicit dataset version")
    return PreparedDataset(manifest["dataset_id"], str(source.resolve()), checksum, features,
                           "CycleToFailure", "cycles", numeric[features], numeric["CycleToFailure"],
                           regimes, groups, splits, manifest)
