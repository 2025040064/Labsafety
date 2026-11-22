from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rac_hpo.data.loop_views import build_protocol, build_view, load_view, replay_rows, save_view
from rac_hpo.monitoring.drift import analyze_batch, gate_metrics


ROOT = Path(__file__).resolve().parents[3].parent / 'rac-data'
SOURCE = ROOT / 'datasets/raw/cnc_milling_2025/FeatureAndMetadata_Milling.csv'


@pytest.fixture
def dataset():
    from rac_hpo.data.cnc_milling import prepare_cnc_milling
    return prepare_cnc_milling(SOURCE)


def test_protocol_and_restricted_view(dataset, tmp_path):
    protocol = build_protocol(dataset)
    assert {key: len(value) for key, value in protocol['roles'].items()} == {
        'train': 341, 'validation': 101, 'replay': 167, 'gate': 233, 'test': 126}
    initial = build_view(dataset, protocol, [])
    assert len(initial.y) == 442
    assert set(initial.splits) == {'train', 'validation'}
    assert set(initial.groups) == {'2', '6', '105', '10', '7', '11', '4', '103'}
    identifier = save_view(tmp_path, initial)
    restored = load_view(tmp_path, identifier)
    assert restored.manifest == initial.manifest
    assert restored.X.equals(initial.X)
    assert save_view(tmp_path, initial) == identifier
    with pytest.raises(ValueError):
        load_view(tmp_path, '../invalid')


def test_replay_and_release_only_whole_tools(dataset):
    protocol = build_protocol(dataset)
    rows = replay_rows(dataset, '5')
    assert len(rows) == 51
    assert len(replay_rows(dataset, '8')) == 116
    with pytest.raises(ValueError, match='released'):
        build_view(dataset, protocol, rows[:20])
    candidate = build_view(dataset, protocol, rows)
    assert len(candidate.splits['train']) == 392
    assert len(candidate.splits['validation']) == 101
    assert set(candidate.manifest['source_row_indices']).isdisjoint(protocol['roles']['gate'])
    with pytest.raises(ValueError):
        build_view(dataset, protocol, protocol['roles']['test'])


def test_monitoring_unlabelled_quality_and_small_batch():
    reference = pd.DataFrame({'Current - a': np.arange(40.), 'ADOC': [5]*20+[10]*20})
    current = reference.iloc[:20].copy()
    current['Current - a'] += 100
    events = analyze_batch(reference, current, ['A']*20+['B']*20, ['A']*20)
    assert {event['kind'] for event in events} == {'feature_drift', 'condition_drift'}
    assert all('performance' not in event['kind'] for event in events)
    assert not any(event['kind'] == 'feature_drift' for event in analyze_batch(
        reference, current.iloc[:3], ['A']*20+['B']*20, ['A']*3))
    current.iloc[0, 0] = np.nan
    assert analyze_batch(reference, current, ['A']*40, ['A']*20)[0]['kind'] == 'data_quality'


def test_gate_zero_error_and_missing_regime():
    assert gate_metrics({'overall_mae': 0., 'regimes': {'A': {'mae': 0.}}},
                        {'overall_mae': 0., 'regimes': {'A': {'mae': 0.}}})['passed']
    assert not gate_metrics({'overall_mae': 0., 'regimes': {'A': {'mae': 0.}}},
                            {'overall_mae': 1., 'regimes': {'A': {'mae': 1.}}})['passed']
    assert not gate_metrics({'overall_mae': 1., 'regimes': {'A': {'mae': 1.}}},
                            {'overall_mae': 1., 'regimes': {}})['passed']


def test_gate_fingerprint_does_not_reset_when_source_moves(dataset, tmp_path):
    from rac_hpo.data.cnc_milling import prepare_cnc_milling
    source = tmp_path/'same-source.csv'
    source.write_bytes(Path(dataset.source_path).read_bytes())
    assert build_protocol(prepare_cnc_milling(source))['gate_fingerprint'] == build_protocol(dataset)['gate_fingerprint']


def test_simulated_view_and_tamper_detection(dataset, tmp_path):
    import json
    rows = replay_rows(dataset, '5')
    view = build_view(dataset, build_protocol(dataset), rows, 'feature_shift')
    assert view.manifest['data_mode'] == 'SIMULATED'
    identifier = save_view(tmp_path, view)
    assert load_view(tmp_path, identifier).X.equals(view.X)
    target = tmp_path/'workspace/loop/views'/f'{identifier}.json'
    content = json.loads(target.read_text(encoding='utf-8'))
    content['source_row_indices'][0] = 999
    target.write_text(json.dumps(content), encoding='utf-8')
    with pytest.raises(ValueError, match='checksum'):
        load_view(tmp_path, identifier)


@pytest.mark.parametrize('policy', [{'max_trials': 49}, {'training_budget_seconds': -1},
                                   {'timeout_seconds': float('nan')}, {'external_deploy': True}])
def test_policy_bounds_and_unknown_actions(policy):
    from rac_hpo.domain.loop_policy import LoopPolicy
    with pytest.raises(ValueError):
        LoopPolicy.model_validate(policy)
