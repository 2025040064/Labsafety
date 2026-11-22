from pathlib import Path

import pytest

from rac_hpo.data.cnc_milling import OFFICIAL_SHA256, prepare_cnc_milling
from rac_hpo.data.registry import prepare

ROOT = Path(__file__).resolve().parents[3].parent / 'rac-data'
DATA = ROOT / "datasets/raw/cnc_milling_2025/FeatureAndMetadata_Milling.csv"


@pytest.mark.skipif(not DATA.exists(), reason="Download author CNC data first")
def test_real_cnc_has_physical_tool_holdouts_and_no_target_features(tmp_path):
    dataset = prepare_cnc_milling(DATA, tmp_path)
    assert dataset.source_sha256 == OFFICIAL_SHA256
    assert len(dataset.y) == 968
    assert dataset.groups.nunique() == 14
    assert len(dataset.features) == 125
    assert not {"FileName", "TollIndex", "SampleIndex", "NumberOfCycle", "CycleToFailure", "CycleToFailureNormalized"} & set(dataset.features)
    grouped = {key: set(dataset.groups.iloc[indices]) for key, indices in dataset.splits.items()}
    assert not grouped["train"] & grouped["validation"]
    assert not grouped["train"] & grouped["test"]
    assert not grouped["validation"] & grouped["test"]
    assert set().union(*grouped.values()) == set(dataset.groups)
    assert set(dataset.y.index) == set(sum(dataset.splits.values(), []))
    assert (dataset.groups.value_counts() == 1).any()  # Retain the real premature failure.
    assert dataset.manifest == prepare_cnc_milling(DATA, tmp_path).manifest
    assert prepare(DATA).dataset_id == "cnc_milling_2025"


def test_cnc_rejects_invalid_schema(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text("Column1;Column2\nx;y\n1;2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="120 sensor features"):
        prepare_cnc_milling(path)
