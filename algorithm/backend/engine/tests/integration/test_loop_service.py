import json
from pathlib import Path
import shutil
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from rac_hpo.application.loop_service import LoopService
from rac_hpo.application.experiment_service import ApplicationService

ROOT = Path(__file__).resolve().parents[3].parent / 'rac-data'
SOURCE = ROOT / 'datasets/raw/cnc_milling_2025/FeatureAndMetadata_Milling.csv'


@pytest.fixture
def service(tmp_path):
    (tmp_path / 'configs').mkdir()
    shutil.copy2(ROOT / 'configs/default.yaml', tmp_path / 'configs/default.yaml')
    return LoopService(tmp_path)


def test_authorization_and_illegal_tool_are_audited(service):
    session = service.create_session(SOURCE)
    with pytest.raises(ValueError, match='authorized'):
        service.run_session(session['id'])
    with pytest.raises(ValueError, match='tool'):
        service.check_tool(session['id'], 'external_deploy')
    events = service.get_session(session['id'])['events']
    assert any(event['kind'] == 'tool_rejected' for event in events)


def test_real_loop_once_and_test_isolation(service, monkeypatch):
    session = service.create_session(SOURCE, authorize_training=True,
                                     policy={'max_trials': 2, 'timeout_seconds': 20})
    def forbidden(*args, **kwargs):
        raise AssertionError('Finalization is forbidden in the loop')
    monkeypatch.setattr(ApplicationService, 'finalize_experiment', forbidden)
    result = service.run_session(session['id'], manual_demo=True)
    assert result['state'] in {'WAITING_RELEASE', 'COMPLETED'}
    assert result['candidate']['experiment_id']
    assert result['gate']['binding']['gate_fingerprint'] == result['protocol']['gate_fingerprint']
    assert len(result['released_rows']) >= 51
    assert result['hpo_attempts'] == 1
    initial = service.experiments.get_experiment(result['champion']['experiment_id'])
    assert len(json.loads((Path(initial['directory'])/'manifest.json').read_text(encoding='utf-8'))['source_row_indices']) == 442
    assert 'test_evaluation' not in initial
    if result['state'] == 'WAITING_RELEASE':
        with pytest.raises(ValueError, match='Stale'):
            service.promote(result['id'], result['revision']-1)
        promoted = service.promote(result['id'], result['revision'])
        assert promoted['active_model']['sha256'] == result['candidate']['sha256']
        with pytest.raises(ValueError):
            service.promote(result['id'], result['revision'])
        completed = service.run_session(result['id'])
        assert completed['state'] == 'COMPLETED'
        old = service.predict_active(result['id'], service._dataset(completed).X.iloc[:2])
        rolled = service.rollback(result['id'], completed['revision'])
        assert rolled['active_model']['sha256'] == result['champion']['sha256']
        assert np.isfinite(old).all()
    rerun = service.run_session(result['id'])
    assert rerun['hpo_attempts'] == 1
    other = service.create_session(SOURCE, authorize_training=True)
    with pytest.raises(ValueError, match='Gate'):
        service.run_session(other['id'], manual_demo=True)
    exported = Path(service.export_session(result['id']))
    assert (exported/'session.json').exists()
    assert (exported/'audit.jsonl').exists()


def test_view_experiment_cannot_finalize_before_prepare(service, monkeypatch):
    session = service.create_session(SOURCE, authorize_training=True)
    view_id = session['initial_view_id']
    detail = service.experiments.run_experiment('baseline', dataset_view_id=view_id,
                                               overrides={'model': {'n_estimators': 10}})
    from rac_hpo.infrastructure.artifacts import source_fingerprint
    environment = json.loads((Path(detail['directory'])/'environment.json').read_text(encoding='utf-8'))
    assert environment['implementation_source_sha256'] == source_fingerprint(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(service.experiments, '_prepare', lambda *a, **k: pytest.fail('test accessed'))
    with pytest.raises(ValueError, match='cannot be finalized'):
        service.experiments.finalize_experiment(detail['id'])


def test_cancel_budget_and_quality(service):
    session = service.create_session(SOURCE, authorize_training=True)
    cancelled = service.run_session(session['id'], cancel_check=lambda: True)
    assert cancelled['state'] == 'CANCELLED'
    tiny = service.create_session(SOURCE, authorize_training=True,
                                  policy={'training_budget_seconds': 1e-8})
    assert service.run_session(tiny['id'])['state'] == 'BUDGET_EXHAUSTED'
    missing = service.create_session(SOURCE, authorize_training=True,
                                     policy={'scenario': 'missing_sensor'})
    result = service.run_session(missing['id'])
    assert result['state'] == 'DATA_REVIEW'
    assert result['data_mode'] == 'SIMULATED'
    assert result['hpo_attempts'] == 0
    assert not result['released_rows']


def test_interruption_and_session_lock(service):
    session = service.create_session(SOURCE, authorize_training=True)
    token = service.repository.acquire(session['id'])
    with pytest.raises(ValueError, match='already running'):
        service.run_session(session['id'])
    session['state'] = 'TRAINING_INITIAL'
    service.repository.save(session, 'training_started', token=token)
    service.repository.release(session['id'], token)
    restored = LoopService(service.root).run_session(session['id'])
    assert restored['state'] == 'INTERRUPTED'
    assert restored['hpo_attempts'] == 0


def test_gate_transaction_binding_and_recovery(service):
    session = service.create_session(SOURCE)
    repo = service.repository
    key = session['protocol']['gate_fingerprint']
    binding = {'candidate': 'one'}
    assert repo.claim_gate(session['id'], key, binding) is None
    with pytest.raises(ValueError):
        repo.claim_gate(session['id'], key, {'candidate': 'two'})
    repo.finish_gate(session['id'], key, binding, {'passed': False})
    assert repo.claim_gate(session['id'], key, binding) == {'passed': False}
    with pytest.raises(ValueError):
        repo.finish_gate(session['id'], key, binding, {'passed': True})
    original = repo.get(session['id'])
    with pytest.raises(ValueError):
        repo.save({**original, 'revision': original['revision']+1}, 'bad_update')
    assert repo.get(session['id'])['revision'] == original['revision']


def test_parallel_repository_initialization(tmp_path, monkeypatch):
    import sqlite3
    import time
    from rac_hpo.infrastructure.loop_repository import LoopRepository
    # Widen the executescript implicit-commit gap, if present, deterministically.
    original = sqlite3.connect
    class SlowConnection(sqlite3.Connection):
        def executescript(self, sql):
            result = super().executescript(sql)
            time.sleep(.1)
            return result
    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: original(*args, factory=SlowConnection, **kwargs))
    with ThreadPoolExecutor(max_workers=4) as executor:
        repos = list(executor.map(lambda _: LoopRepository(tmp_path), range(4)))
    assert all(repo.list() == [] for repo in repos)


def release_ready(service):
    from rac_hpo.application.loop_models import model_reference
    from rac_hpo.monitoring.drift import gate_metrics
    session = service.create_session(SOURCE, authorize_training=True)
    result = service.experiments.run_experiment('baseline', dataset_view_id=session['initial_view_id'],
                                               overrides={'model': {'n_estimators': 10}})
    champion = model_reference(result)
    session.update(champion=champion, candidate=deepcopy(champion), active_model=deepcopy(champion),
                   state='WAITING_RELEASE')
    binding = {'candidate_sha256': champion['sha256'], 'champion_sha256': champion['sha256'],
               'gate_fingerprint': session['protocol']['gate_fingerprint']}
    gate = {**gate_metrics(champion['metrics'], champion['metrics']), 'binding': binding}
    repo = service.repository
    repo.claim_gate(session['id'], binding['gate_fingerprint'], binding)
    repo.finish_gate(session['id'], binding['gate_fingerprint'], binding, gate)
    session['gate'] = gate
    repo.save(session, 'fixture_release_ready')
    return repo.get(session['id'])


def test_promotion_rollback_predictions_hash_and_atomicity(service, monkeypatch):
    session = release_ready(service)
    repo = service.repository
    original_event = repo._event
    def failed_event(*args, **kwargs):
        raise RuntimeError('simulated transaction failure')
    monkeypatch.setattr(repo, '_event', failed_event)
    with pytest.raises(RuntimeError, match='transaction failure'):
        service.promote(session['id'], session['revision'])
    assert repo.get(session['id'])['revision'] == session['revision']
    assert not repo.get(session['id']).get('promoted')
    monkeypatch.setattr(repo, '_event', original_event)
    resumed = LoopService(service.root)
    promoted = resumed.promote(session['id'], session['revision'])
    X = service._dataset(session).X.iloc[:3]
    expected = resumed.predict_active(session['id'], X)
    rolled = resumed.rollback(session['id'], promoted['revision'])
    assert rolled['active_model'] == session['champion']
    assert np.array_equal(resumed.predict_active(session['id'], X), expected)
    with pytest.raises(ValueError):
        resumed.rollback(session['id'], rolled['revision'])
    path = Path(session['champion']['path'])
    content = path.read_bytes()
    path.write_bytes(content+b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        resumed.predict_active(session['id'], X)


def test_labels_are_hidden_until_whole_tool_ends(service):
    session = service.create_session(SOURCE, authorize_training=True, policy={'max_trials': 1})
    result = service.run_session(session['id'], manual_demo=True)
    events = result['events']
    first_release = next(i for i, event in enumerate(events) if event['kind'] == 'labels_released')
    batches = [event for event in events[:first_release] if event['kind'] == 'batch_predicted']
    assert [event['evidence']['batch_rows'] for event in batches] == [20, 20, 11]
    assert all(event['evidence']['labels_available'] is False for event in batches)
    assert not any(event['kind'] == 'optimization_proposed' for event in events[:first_release])


def test_restart_recovers_dead_process_and_linked_experiment(service):
    import subprocess
    import sys
    session = service.create_session(SOURCE, authorize_training=True)
    code = '''
import os, sys
from pathlib import Path
from rac_hpo.application.loop_service import LoopService
service = LoopService(sys.argv[1])
session = service.get_session(sys.argv[2])
token = service.repository.acquire(session['id'])
directory = service.root/'workspace/experiments/EXP-interrupted'
directory.mkdir(parents=True)
service.experiments.repository.save_experiment({'id':'EXP-interrupted','algorithm':'baseline',
    'status':'running','created_at':session['created_at'],'directory':str(directory)})
session.update(state='TRAINING_INITIAL',initial_experiment_id='EXP-interrupted')
service.repository.save(session,'training_started',token=token)
os._exit(7)
'''
    child = subprocess.run([sys.executable, '-c', code, str(service.root), session['id']],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert child.returncode == 7, child.stderr.decode('utf-8', errors='replace')
    restored = LoopService(service.root)
    assert restored.get_session(session['id'])['state'] == 'INTERRUPTED'
    experiment = restored.experiments.get_experiment('EXP-interrupted')
    assert experiment['status'] == 'failed'
    assert experiment['interrupted']
    assert (Path(experiment['directory'])/'result.json').exists()
    assert restored.run_session(session['id'])['hpo_attempts'] == 0


def test_restart_recovers_committed_gate_without_reading_data(service, monkeypatch):
    session = release_ready(service)
    session['state'] = 'EVALUATING'
    service.repository.save(session, 'fixture_crash_after_gate_commit')
    monkeypatch.setattr(LoopService, '_dataset', lambda *args: pytest.fail('Gate data read twice'))
    restored = LoopService(service.root).get_session(session['id'])
    assert restored['state'] == 'WAITING_RELEASE'
    assert restored['gate'] == session['gate']
    assert restored['events'][-1]['kind'] == 'gate_result_recovered'
