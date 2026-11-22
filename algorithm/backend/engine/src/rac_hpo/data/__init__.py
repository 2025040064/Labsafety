"""Local, verified dataset adapters; independent of GUI and training."""

from .loader import download_steel, prepare_steel_dataset

__all__ = ["download_steel", "prepare_steel_dataset"]
