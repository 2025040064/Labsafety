"""Uncalibrated engineering alerts, never causal or statistical guarantees."""
import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance

RULE_VERSION = 'offline-loop-rules-1'


def analyze_batch(reference, current, reference_regimes, current_regimes):
    def event(kind, evidence, threshold, recommendation):
        return {'kind': kind, 'rule_version': RULE_VERSION, 'evidence': evidence,
                'threshold': threshold, 'recommendation': recommendation}
    if list(current.columns) != list(reference.columns) or current.empty:
        return [event('data_quality', {'reason': 'schema_or_empty'}, None, 'data_review')]
    try:
        invalid = int((~np.isfinite(current.to_numpy(dtype=float))).sum())
    except (TypeError, ValueError):
        invalid = int(current.size)
    if invalid:
        return [event('data_quality', {'invalid_cells': invalid}, 0, 'data_review')]
    result = []
    sensors = [name for name in reference.columns if name.startswith(('Current - ', 'Accelerometer - '))]
    if len(current) >= 20 and sensors:
        distances = {}
        for name in sensors:
            base = reference[name].to_numpy(dtype=float)
            scale = max(float(np.quantile(base, .75) - np.quantile(base, .25)), 1e-8)
            distances[name] = float(wasserstein_distance(base, current[name]) / scale)
        fraction = sum(value > .5 for value in distances.values()) / len(sensors)
        if fraction > .2:
            result.append(event('feature_drift', {'distances': distances, 'fraction': fraction},
                                {'distance': .5, 'fraction': .2, 'minimum_rows': 20}, 'observe'))
    base = pd.Series(reference_regimes).value_counts(normalize=True)
    now = pd.Series(current_regimes).value_counts(normalize=True)
    keys = set(base.index) | set(now.index)
    tv = .5 * sum(abs(float(base.get(key, 0)) - float(now.get(key, 0))) for key in keys)
    if tv > .25:
        result.append(event('condition_drift', {'total_variation': tv,
                                               'proportions': now.to_dict()}, .25, 'observe'))
    return result


def gate_metrics(champion, candidate):
    checks = {}
    checks['overall_mae'] = _within(candidate.get('overall_mae'), champion.get('overall_mae'), 1.02)
    old, new = champion.get('regimes', {}), candidate.get('regimes', {})
    checks['regime_coverage'] = bool(old) and set(old) == set(new)
    for key, value in old.items():
        checks[key] = _within(new.get(key, {}).get('mae'), value.get('mae'), 1.05)
    return {'passed': all(checks.values()), 'checks': checks,
            'thresholds': {'overall_factor': 1.02, 'regime_factor': 1.05, 'epsilon': 1e-8},
            'champion_metrics': champion, 'candidate_metrics': candidate,
            'interpretation': 'Engineering gate only; not a statistical noninferiority claim'}


def _within(value, reference, factor):
    return (isinstance(value, (int, float)) and isinstance(reference, (int, float))
            and np.isfinite(value) and np.isfinite(reference) and value >= 0 and reference >= 0
            and value <= factor * reference + 1e-8)
