"""One dispatch point for supported real datasets."""
from pathlib import Path

from .cnc_milling import download_cnc_milling, prepare_cnc_milling
from .loader import download_steel, prepare_steel_dataset

DEFAULT_PATHS = {
    "steel_energy": "datasets/raw/steel_energy/Steel_industry_data.csv",
    "cnc_milling_2025": "datasets/raw/cnc_milling_2025/FeatureAndMetadata_Milling.csv",
}


def prepare(path, manifest_dir=None):
    with Path(path).open(encoding="utf-8-sig") as handle:
        header = handle.readline()
    if header.startswith("Column1;Column2;"):
        return prepare_cnc_milling(path, manifest_dir)
    return prepare_steel_dataset(path, manifest_dir)


def download(dataset_id, root):
    destination = Path(root) / DEFAULT_PATHS[dataset_id]
    return (download_cnc_milling if dataset_id == "cnc_milling_2025" else download_steel)(destination)
