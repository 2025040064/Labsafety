"""The fixed, explicit execution policy approved at session start."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LoopPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    seed: int = Field(default=42, ge=0, le=2147483647)
    algorithm: Literal['rac_hpo', 'tpe', 'tpe_robust', 'random'] = 'rac_hpo'
    max_trials: int = Field(default=48, ge=1, le=48)
    timeout_seconds: float = Field(default=120, gt=0, le=120, allow_inf_nan=False)
    training_budget_seconds: float = Field(default=300, gt=0, le=300, allow_inf_nan=False)
    scenario: Literal['natural', 'missing_sensor', 'feature_shift'] = 'natural'


ALLOWED_TOOLS = frozenset({'inspect_dataset', 'query_experiments', 'analyze_drift',
                           'propose_experiment', 'run_hpo', 'evaluate_candidate',
                           'promote_candidate', 'rollback_model'})
