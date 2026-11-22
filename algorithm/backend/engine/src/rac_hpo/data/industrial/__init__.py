"""Industrial dataset entry points. Acquisition is explicit, never part of training."""
from pathlib import Path

from .bearings import fetch_paderborn, prepare_paderborn
from .common import utc_now, write_json
from .local import LOCAL_SOURCES, prepare_lenovo, prepare_pneumatic, prepare_xjtu
from .uci import SOURCES, fetch_uci, prepare_ai4i, prepare_secom

DEFAULT_PATHS = {
    "ai4i_2020": "datasets/raw/ai4i_2020/data.csv",
    "secom": "datasets/raw/secom/data.csv",
    "paderborn": "datasets/raw/paderborn",
    "xjtu_sy": "datasets/raw/xjtu_sy",
    "lenovo": "datasets/raw/lenovo/data.csv",
    "pneumatic_pressure": "datasets/raw/pneumatic_pressure/data.csv",
}
_PREPARERS = {"ai4i_2020": prepare_ai4i, "secom": prepare_secom, "paderborn": prepare_paderborn,
              "xjtu_sy": prepare_xjtu, "lenovo": prepare_lenovo, "pneumatic_pressure": prepare_pneumatic}


def prepare(dataset_key, path, manifest_dir=None, archive_cache_dir=None):
    try:
        adapter = _PREPARERS[dataset_key]
    except KeyError as error:
        raise ValueError(f"Unknown industrial dataset: {dataset_key}") from error
    if dataset_key in {"paderborn", "xjtu_sy"}:
        cache = Path(archive_cache_dir or Path.cwd() / "datasets/processed/industrial") / dataset_key
        return adapter(path, manifest_dir, archive_cache_dir=cache)
    return adapter(path, manifest_dir)


def fetch(dataset_key, destination):
    if dataset_key in SOURCES:
        return fetch_uci(dataset_key, destination)
    if dataset_key == "paderborn":
        return fetch_paderborn(destination)
    if dataset_key not in LOCAL_SOURCES:
        raise ValueError(f"Unknown industrial dataset: {dataset_key}")
    reason = ("Provider approval is required; import authorized local files without bypassing access controls"
              if dataset_key == "lenovo" else "Import a locally obtained official archive with documented license and task metadata")
    directory = Path(destination)
    if directory.suffix:
        directory = directory.parent
    write_json(directory / "download_failure.json", {"dataset_key": dataset_key,
               "source_url": LOCAL_SOURCES[dataset_key], "attempted_at": utc_now(),
               "reason": reason, "error_type": "ExplicitLocalImportRequired", "substitution_used": False})
    raise PermissionError(reason)


__all__ = ["DEFAULT_PATHS", "prepare", "fetch"]
