from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentConfig(StrictModel):
    name: str = "steel_calendar_regression"
    seed: int = Field(default=42, ge=0, le=2147483647)


class DataConfig(StrictModel):
    dataset_id: Literal["steel_energy", "cnc_milling_2025"] = "steel_energy"
    path: str = "datasets/raw/steel_energy/Steel_industry_data.csv"


class ModelConfig(StrictModel):
    name: Literal["lightgbm"] = "lightgbm"
    n_estimators: int = Field(default=300, ge=1, le=10000)
    early_stopping_rounds: int = Field(default=30, ge=1)
    n_jobs: int = Field(default=4, ge=1, le=64)


class OptimizationConfig(StrictModel):
    algorithm: Literal["random", "grid", "tpe", "tpe_robust", "rac_hpo", "successive_halving", "hyperband"] = "tpe"
    max_trials: int = Field(default=12, ge=1, le=100000)
    timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    search_space: dict[str, dict] = Field(default_factory=dict)
    rac_hpo: dict = Field(default_factory=dict)
    min_resource: int = Field(default=10, ge=1)
    reduction_factor: int = Field(default=3, ge=2)


class RobustnessConfig(StrictModel):
    alpha: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)
    epsilon: float = Field(default=1e-8, gt=0, allow_inf_nan=False)
    minimum_independent_groups: int = Field(default=5, ge=2, le=1000)


class OutputConfig(StrictModel):
    save_trials: Literal[True] = True
    save_model: Literal[True] = True


class AppConfig(StrictModel):
    schema_version: Literal[1] = 1
    experiment: ExperimentConfig = Field(default_factory=ExperimentConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    optimization: OptimizationConfig = Field(default_factory=OptimizationConfig)
    robustness: RobustnessConfig = Field(default_factory=RobustnessConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="after")
    def validate_search_space(self):
        if self.optimization.algorithm in {"successive_halving", "hyperband"} and self.optimization.min_resource > self.model.n_estimators:
            raise ValueError("min_resource must not exceed model.n_estimators")
        if self.optimization.search_space:
            from rac_hpo.optimization.base import validate_search_space
            validate_search_space(self.optimization.search_space)
        from rac_hpo.optimization.rac_hpo.search import RACSettings
        try:
            self.optimization.rac_hpo = asdict(RACSettings(**self.optimization.rac_hpo))
        except TypeError as error:
            raise ValueError(f"Invalid RAC settings: {error}") from error
        return self


def merge_config(original: dict, overrides: dict) -> dict:
    result = deepcopy(original)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_config(path: str | Path, overrides: dict | None = None) -> dict:
    with Path(path).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a YAML mapping")
    return AppConfig.model_validate(merge_config(config, overrides or {})).model_dump()
