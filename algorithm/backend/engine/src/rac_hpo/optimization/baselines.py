"""Finite grid and resource policies; independent of trainers and storage."""
import itertools
import math

import optuna


RESOURCE_METHODS = {"successive_halving", "hyperband"}
SEARCH_METHODS = {"random", "grid", "tpe", "tpe_robust", "rac_hpo"} | RESOURCE_METHODS


def grid_candidates(space):
    """Three levels per dimension, respecting integer/step/log contracts.

    This is a deliberately coarse finite grid, not exhaustive continuous search.
    Iteration is lazy so even a large space does not allocate a Cartesian table.
    """
    levels = []
    for spec in space.values():
        low, high = spec["low"], spec["high"]
        middle = math.sqrt(low * high) if spec.get("log") else (low + high) / 2
        values = [low, middle, high]
        step = spec.get("step", 1 if spec["type"] == "int" else None)
        if step is not None:
            maximum = math.floor((high - low) / step + 1e-10)
            values = [low + min(maximum, max(0, round((value - low) / step))) * step for value in values]
        if spec["type"] == "int":
            values = [int(value) for value in values]
        levels.append(sorted(set(values)))
    for values in itertools.product(*levels):
        yield dict(zip(space, values))


def resource_pruner(algorithm, config):
    minimum = config.get("min_resource", 10)
    maximum = config.get("max_resource", 300)
    factor = config.get("reduction_factor", 3)
    for key, value in (("min_resource", minimum), ("max_resource", maximum), ("reduction_factor", factor)):
        if isinstance(value, bool) or not isinstance(value, int) or value < (2 if key == "reduction_factor" else 1):
            raise ValueError(f"{key} must be a positive integer (reduction_factor >= 2)")
    if minimum > maximum:
        raise ValueError("min_resource must not exceed max_resource")
    if algorithm == "hyperband":
        return optuna.pruners.HyperbandPruner(min_resource=minimum, max_resource=maximum, reduction_factor=factor)
    return optuna.pruners.SuccessiveHalvingPruner(min_resource=minimum, reduction_factor=factor)
