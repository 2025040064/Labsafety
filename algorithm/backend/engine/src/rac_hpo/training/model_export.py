"""Read model text in Python to support Unicode paths on Windows."""

from pathlib import Path

import lightgbm as lgb


def load_model(path: str | Path) -> lgb.Booster:
    """Load an exported UTF-8 model without passing its path to LightGBM's C API."""
    return lgb.Booster(model_str=Path(path).read_text(encoding="utf-8"))
