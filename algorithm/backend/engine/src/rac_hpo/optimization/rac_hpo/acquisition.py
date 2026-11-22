"""Expected-improvement approximation from ensemble members and measured costs."""
import numpy as np

from rac_hpo.domain.metrics import normalized_objective


def acquisition_scores(members, costs, incumbent_j, incumbent_regime_errors, weights, alpha,
                       priorities, beta=0.5, gamma=0.5, t0=0.01):
    candidate_j = normalized_objective(members, weights, alpha)
    global_ei = np.maximum(incumbent_j - candidate_j, 0).mean(axis=1)
    regime_ei = np.maximum(np.asarray(incumbent_regime_errors) - members, 0).mean(axis=1)
    attention_ei = (regime_ei * np.asarray(priorities)).sum(axis=1)
    return (global_ei + beta * attention_ei) / (np.asarray(costs) + t0) ** gamma
