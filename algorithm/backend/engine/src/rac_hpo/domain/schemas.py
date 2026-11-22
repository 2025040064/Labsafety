from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class PreparedDataset:
    dataset_id: str
    source_path: str
    source_sha256: str
    features: list[str]
    target: str
    target_unit: str
    X: pd.DataFrame
    y: pd.Series
    regimes: pd.Series
    groups: pd.Series
    splits: dict[str, list[int]]
    manifest: dict[str, Any]

    def split(self, name: str) -> tuple:
        indices = self.splits[name]
        return (
            self.X.iloc[indices], self.y.iloc[indices],
            self.regimes.iloc[indices], self.groups.iloc[indices],
        )


@dataclass
class TrainingResult:
    params: dict[str, Any]
    metrics: dict[str, Any]
    duration_seconds: float
    model: Any = None
    best_iteration: int = 0
    status: str = "completed"
    failure_reason: str | None = None


@dataclass
class OptimizationResult:
    algorithm: str
    best_params: dict[str, Any]
    best_value: float | None
    best_trial: int | None
    trials: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0
    status: str = "completed"


class TaskCancelled(Exception):
    """Raised once a running task has acknowledged cancellation."""
