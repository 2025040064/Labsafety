"""Single-candidate offline orchestration; production deployment is not a tool."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import time
import uuid

import numpy as np

from rac_hpo.data.cnc_milling import prepare_cnc_milling
from rac_hpo.data.loop_views import (build_protocol, build_view, digest, replay_rows,
                                    sample_metadata, save_view, simulate_features)
from rac_hpo.domain.loop_policy import ALLOWED_TOOLS, LoopPolicy
from rac_hpo.domain.schemas import TaskCancelled
from rac_hpo.infrastructure.artifacts import atomic_text, utc_now, write_json
from rac_hpo.infrastructure.loop_repository import LoopRepository
from rac_hpo.monitoring.drift import RULE_VERSION, analyze_batch, gate_metrics
from .experiment_service import ApplicationService
from .loop_models import metrics, model_reference, predict

TERMINAL = {'COMPLETED', 'CANCELLED', 'BUDGET_EXHAUSTED', 'FAILED', 'INTERRUPTED', 'DATA_REVIEW'}
BUSY = {'TRAINING_INITIAL', 'TRAINING_CANDIDATE', 'EVALUATING'}


class LoopService:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.repository = LoopRepository(self.root)
        self.experiments = ApplicationService(self.root)
        self._recover_dead_workers()

    def _recover_dead_workers(self):
        for snapshot in self.repository.list():
            if snapshot['state'] not in BUSY:
                continue
            try:
                token = self.repository.acquire(snapshot['id'])
            except ValueError:
                continue  # A live worker owns the session; do not interrupt it.
            try:
                session = self.get_session(snapshot['id'])
                if session['state'] in BUSY:
                    self._mark_interrupted(session, token)
            finally:
                self.repository.release(snapshot['id'], token)

    def _mark_interrupted(self, session, token):
        if session['state'] == 'EVALUATING':
            saved = self.repository.get_gate(session['protocol']['gate_fingerprint'])
            if (saved and saved['session_id'] == session['id'] and saved['result']
                    and saved['binding'].get('candidate_sha256') == session['candidate']['sha256']
                    and saved['binding'].get('champion_sha256') == session['champion']['sha256']):
                session['gate'] = saved['result']
                session['state'] = 'WAITING_RELEASE' if saved['result']['passed'] else 'MONITORING'
                self._save(session, 'gate_result_recovered', {'gate': saved['result'], 're_evaluated': False}, token)
                return
        key = 'candidate_experiment_id' if session['state'] == 'TRAINING_CANDIDATE' else 'initial_experiment_id'
        identifier = session.get(key)
        if identifier and session['state'].startswith('TRAINING_'):
            experiment = self.experiments.get_experiment(identifier)
            if experiment['status'] == 'running':
                directory = Path(experiment['directory']).resolve()
                if not directory.is_relative_to(self.root/'workspace/experiments'):
                    raise ValueError('Interrupted experiment directory is outside this workspace')
                experiment.update(status='failed', interrupted=True, timing_incomplete=True,
                                  ended_at=utc_now(), failure_reason='Loop worker interrupted; no automatic retry')
                metadata = {key: value for key, value in experiment.items() if key != 'trials'}
                self.experiments.repository.save_experiment(metadata)
                write_json(directory/'result.json', self.experiments.get_experiment(identifier))
        session['state'] = 'INTERRUPTED'
        self._save(session, 'interrupted', {'reason': 'Previous worker exited; linked running experiment closed, no automatic retry'}, token)

    def create_session(self, source_path=None, authorize_training=False, policy=None):
        from rac_hpo.data.registry import DEFAULT_PATHS
        policy = LoopPolicy.model_validate(policy or {}).model_dump()
        source = Path(source_path or DEFAULT_PATHS['cnc_milling_2025'])
        if not source.is_absolute():
            source = self.root / source
        dataset = prepare_cnc_milling(source)
        protocol = build_protocol(dataset)
        view_id = save_view(self.root, build_view(dataset, protocol, [], policy['scenario']))
        identifier = 'LOOP-' + uuid.uuid4().hex[:16]
        directory = self.root / 'workspace/loop/sessions' / identifier
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory/'protocol.json', protocol)
        write_json(directory/'policy.json', policy)
        value = {'id': identifier, 'created_at': utc_now(), 'revision': 0, 'state': 'CREATED',
                 'directory': str(directory), 'protocol': protocol, 'policy': policy,
                 'policy_sha256': digest(policy), 'authorized_policy_sha256': digest(policy) if authorize_training else None,
                 'data_mode': 'REPLAY' if policy['scenario'] == 'natural' else 'SIMULATED',
                 'initial_view_id': view_id, 'released_rows': [], 'replay_tool_index': 0,
                 'batch_offset': 0, 'hpo_attempts': 0, 'training_seconds': 0.,
                 'champion': None, 'candidate': None, 'active_model': None, 'gate': None,
                 'predictions': [], 'proposal': None}
        self.repository.create(value)
        if authorize_training:
            self.repository.save(value, 'training_authorized', {'policy_sha256': digest(policy)}, actor='user')
        return self.get_session(identifier)

    def get_session(self, identifier):
        return self.repository.get(identifier)

    def list_sessions(self):
        return self.repository.list()

    def _dataset(self, session):
        dataset = prepare_cnc_milling(session['protocol']['source_path'])
        if build_protocol(dataset) != session['protocol']:
            raise ValueError('Session source or protocol changed')
        return dataset

    def check_tool(self, identifier, tool):
        session = self.get_session(identifier)
        if tool not in ALLOWED_TOOLS:
            self.repository.save(session, 'tool_rejected', {'tool': tool}, actor='user')
            raise ValueError('Illegal tool rejected')
        return {'tool': tool, 'allowed': True}

    def _save(self, session, kind, evidence, token, progress=None, actor='system'):
        self.repository.save(session, kind, evidence, actor=actor, token=token)
        if progress:
            progress({'type': 'loop_event', 'session_id': session['id'], 'state': session['state'],
                      'revision': session['revision'], 'kind': kind, 'evidence': evidence,
                      'data_mode': session['data_mode']})

    def run_session(self, identifier, manual_demo=False, progress_callback=None, cancel_check=None):
        token = self.repository.acquire(identifier)
        session = self.get_session(identifier)
        try:
            if session['authorized_policy_sha256'] != digest(session['policy']):
                self._save(session, 'training_rejected', {'reason': 'not_authorized'}, token)
                raise ValueError('Training policy is not authorized')
            if session['state'] in TERMINAL or session['state'] == 'WAITING_RELEASE':
                return self.get_session(identifier)
            if session['state'] in BUSY:
                self._mark_interrupted(session, token)
                return self.get_session(identifier)
            owner = self.repository.gate_owner(session['protocol']['gate_fingerprint'])
            if owner and owner != identifier:
                self._save(session, 'gate_reuse_rejected', {'owner_session_id': owner}, token)
                raise ValueError('Gate already consumed by another session; reuse its saved result')
            dataset = self._dataset(session)
            if not session['champion']:
                self._train(session, dataset, token, False, progress_callback, cancel_check)
                session['active_model'] = deepcopy(session['champion'])
                session['state'] = 'MONITORING'
                self._save(session, 'initial_champion_registered', {'model': session['champion']}, token, progress_callback)
            if session['state'] == 'AWAITING_OPTIMIZATION':
                if manual_demo:
                    session['proposal'] = {'trigger': 'manual_demo', 'reason': 'Explicit demonstration; no alert fabricated'}
                    self._candidate(session, dataset, token, progress_callback, cancel_check)
                    if session['state'] == 'WAITING_RELEASE':
                        return self.get_session(identifier)
                else:
                    session['state'] = 'MONITORING'
            while session['replay_tool_index'] < 2:
                self._check_cancel(cancel_check)
                tool = ['5', '8'][session['replay_tool_index']]
                rows = replay_rows(dataset, tool)
                if not self._replay_tool(session, dataset, rows, tool, token, progress_callback, cancel_check):
                    return self.get_session(identifier)
                if tool == '5' and session['hpo_attempts'] == 0:
                    if not session['proposal'] and manual_demo:
                        session['proposal'] = {'trigger': 'manual_demo', 'reason': 'Explicit demonstration; no alert fabricated'}
                    if session['proposal']:
                        self._candidate(session, dataset, token, progress_callback, cancel_check)
                        if session['state'] == 'WAITING_RELEASE':
                            return self.get_session(identifier)
                    else:
                        session['state'] = 'AWAITING_OPTIMIZATION'
                        self._save(session, 'no_optimization_trigger', {'actions': ['continue_observation', 'manual_demo']}, token, progress_callback)
                        return self.get_session(identifier)
            session['state'] = 'COMPLETED'
            self._save(session, 'replay_completed', {'gate_outcome': session['gate']['passed'] if session['gate'] else None}, token, progress_callback)
        except TaskCancelled as error:
            session['state'] = 'CANCELLED'
            self._save(session, 'cancelled', {'reason': str(error)}, token, progress_callback)
        except TrainingBudgetExceeded as error:
            session['state'] = 'BUDGET_EXHAUSTED'
            self._save(session, 'budget_exhausted', {'reason': str(error), 'training_seconds': session['training_seconds'],
                       'overrun_seconds': max(0., session['training_seconds']-session['policy']['training_budget_seconds'])}, token, progress_callback)
        except Exception as error:
            # Validation errors preceding any work remain actionable and have a nonzero CLI exit.
            if session['state'] in BUSY:
                session['state'] = 'FAILED'
                session['failure_reason'] = f'{type(error).__name__}: {error}'
                self._save(session, 'operation_failed', {'reason': session['failure_reason']}, token, progress_callback)
            raise
        finally:
            self.repository.release(identifier, token)
        return self.get_session(identifier)

    @staticmethod
    def _check_cancel(cancel_check):
        if cancel_check and cancel_check():
            raise TaskCancelled('Loop cancellation acknowledged')

    def _train(self, session, dataset, token, candidate, progress, cancel):
        self._check_cancel(cancel)
        remaining = session['policy']['training_budget_seconds'] - session['training_seconds']
        if remaining <= 0:
            raise TrainingBudgetExceeded('Session training budget exhausted')
        view = build_view(dataset, session['protocol'], session['released_rows'] if candidate else [],
                          session['policy']['scenario'])
        view_id = save_view(self.root, view)
        session['state'] = 'TRAINING_CANDIDATE' if candidate else 'TRAINING_INITIAL'
        if candidate:
            if session['hpo_attempts']:
                raise ValueError('Only one HPO attempt is permitted')
            session['hpo_attempts'] = 1
        self._save(session, 'training_started', {'dataset_view_id': view_id, 'tool': 'run_hpo' if candidate else 'initial_baseline'}, token, progress)
        started = time.perf_counter()
        deadline = started + remaining

        def stopped():
            return bool(cancel and cancel()) or time.perf_counter() >= deadline

        def training_event(event):
            if event['type'] == 'started':
                session['candidate_experiment_id' if candidate else 'initial_experiment_id'] = event['experiment_id']
                self._save(session, 'experiment_linked', {'experiment_id': event['experiment_id']}, token, progress)
            if progress:
                progress({**event, 'session_id': session['id'], 'data_mode': session['data_mode']})

        try:
            result = self.experiments.run_experiment(
                session['policy']['algorithm'] if candidate else 'baseline', dataset_view_id=view_id,
                overrides={'experiment': {'seed': session['policy']['seed']},
                           'optimization': {key: session['policy'][key] for key in ('max_trials', 'timeout_seconds')}},
                progress_callback=training_event, cancel_check=stopped)
        finally:
            session['training_seconds'] += time.perf_counter()-started
        if time.perf_counter() >= deadline:
            raise TrainingBudgetExceeded('Budget reached at action/tree-iteration boundary')
        self._check_cancel(cancel)
        if result['status'] != 'completed':
            raise RuntimeError(result.get('failure_reason') or f"Experiment {result['status']}")
        session['candidate' if candidate else 'champion'] = model_reference(result)

    def _replay_tool(self, session, dataset, rows, tool, token, progress, cancel):
        reference = dataset.X.iloc[session['protocol']['roles']['train']]
        reference_regimes = dataset.regimes.iloc[session['protocol']['roles']['train']]
        while session['batch_offset'] < len(rows):
            self._check_cancel(cancel)
            offset = session['batch_offset']
            selected = rows[offset:offset+20]
            X = simulate_features(dataset.X.iloc[selected], reference, session['policy']['scenario'])
            alerts = analyze_batch(reference, X, reference_regimes, dataset.regimes.iloc[selected])
            for alert in alerts:
                self._save(session, 'monitoring_event', {**alert, 'tool_id': tool, 'source_rows': selected}, token, progress)
            if any(alert['kind'] == 'data_quality' for alert in alerts):
                session['state'] = 'DATA_REVIEW'
                self._save(session, 'data_review_required', {'tool_id': tool, 'source_rows': selected}, token, progress)
                return False
            active = predict(session['active_model'], X)
            old = predict(session['champion'], X)
            samples = sample_metadata(dataset, selected)
            for sample, current, previous in zip(samples, active, old):
                session['predictions'].append({**sample, 'tool_id': tool, 'active_prediction': float(current),
                    'champion_prediction': float(previous), 'active_sha256': session['active_model']['sha256'],
                    'champion_sha256': session['champion']['sha256'], 'data_mode': session['data_mode']})
            session['batch_offset'] += len(selected)
            session['state'] = 'MONITORING'
            self._save(session, 'batch_predicted', {'tool_id': tool, 'rows': selected,
                        'labels_available': False, 'batch_rows': len(selected)}, token, progress)
        predictions = [item for item in session['predictions'] if item['tool_id'] == tool]
        selected = [item['source_row'] for item in predictions]
        current_metrics = metrics(dataset, selected, [item['active_prediction'] for item in predictions])
        old_metrics = metrics(dataset, selected, [item['champion_prediction'] for item in predictions])
        session['released_rows'].extend(rows)
        session['replay_tool_index'] += 1
        session['batch_offset'] = 0
        evidence = {'tool_id': tool, 'source_rows': rows, 'metrics': current_metrics,
                    'champion_paired_metrics': old_metrics, 'rule_version': RULE_VERSION}
        if tool == '5':
            bad = [key for key, value in current_metrics['regimes'].items()
                   if value['mae'] > session['champion']['metrics']['regimes'][key]['mae'] * 1.2 + 1e-8]
            if bad:
                session['proposal'] = {'trigger': 'labelled_degradation', 'conditions': bad,
                    'threshold_factor': 1.2, 'rule_version': RULE_VERSION,
                    'reason': 'Observed error exceeds validation reference; possible domain change, not causal attribution'}
                evidence['proposal'] = session['proposal']
        elif session['active_model']['sha256'] != session['champion']['sha256']:
            session['rollback_suggested'] = current_metrics['overall_mae'] > old_metrics['overall_mae'] * 1.05 + 1e-8
            evidence['rollback_suggested'] = session['rollback_suggested']
            evidence['rollback_threshold_factor'] = 1.05
        self._save(session, 'labels_released', evidence, token, progress)
        return True

    def _candidate(self, session, dataset, token, progress, cancel):
        self._save(session, 'optimization_proposed', {'proposal': session['proposal'],
                    'approved_policy_sha256': session['authorized_policy_sha256']}, token, progress)
        self._train(session, dataset, token, True, progress, cancel)
        self._check_cancel(cancel)
        session['state'] = 'EVALUATING'
        self._save(session, 'candidate_frozen', {'candidate': session['candidate']}, token, progress)
        binding = {'candidate_sha256': session['candidate']['sha256'],
                   'champion_sha256': session['champion']['sha256'],
                   'gate_fingerprint': session['protocol']['gate_fingerprint'],
                   'protocol_fingerprint': session['protocol']['fingerprint'],
                   'policy_sha256': session['policy_sha256'], 'rule_version': RULE_VERSION,
                   'thresholds': {'overall_factor': 1.02, 'regime_factor': 1.05, 'epsilon': 1e-8}}
        cached = self.repository.claim_gate(session['id'], binding['gate_fingerprint'], binding)
        if cached is None:
            rows = session['protocol']['roles']['gate']
            X = dataset.X.iloc[rows]
            current = metrics(dataset, rows, predict(session['candidate'], X))
            old = metrics(dataset, rows, predict(session['champion'], X))
            gate = {**gate_metrics(old, current), 'binding': binding, 'data_mode': session['data_mode']}
            write_json(Path(session['directory'])/'gate.json', gate)
            self.repository.finish_gate(session['id'], binding['gate_fingerprint'], binding, gate)
        else:
            gate = cached
        session['gate'] = gate
        session['state'] = 'WAITING_RELEASE' if gate['passed'] else 'MONITORING'
        self._save(session, 'gate_passed' if gate['passed'] else 'gate_rejected', {'gate': gate}, token, progress)

    def _approval(self, identifier, expected_revision, action, actor):
        if not actor or not actor.strip():
            raise ValueError('Approval actor is required')
        session = self.get_session(identifier)
        if session['revision'] != expected_revision:
            raise ValueError('Stale approval; reload the session')
        if action == 'promote':
            if session['state'] != 'WAITING_RELEASE' or not session['gate'] or not session['gate']['passed']:
                raise ValueError('Candidate is not release-ready')
            binding = session['gate']['binding']
            saved_gate = self.repository.get_gate(session['protocol']['gate_fingerprint'])
            if (not saved_gate or saved_gate['session_id'] != identifier
                    or saved_gate['result'] != session['gate']):
                raise ValueError('Gate evidence is not committed')
            if (session['candidate']['sha256'] != binding['candidate_sha256']
                    or session['active_model']['sha256'] != binding['champion_sha256']):
                raise ValueError('Gate model binding changed')
            target = session['candidate']
        else:
            if not session.get('promoted') or session.get('rolled_back') or session['state'] not in {'MONITORING', 'COMPLETED'}:
                raise ValueError('No active simulated release to roll back')
            target = session['champion']
        # Load and validate both sides before atomically committing pointer + approval + audit.
        dataset = self._dataset(session)
        predict(session['active_model'], dataset.X.iloc[session['protocol']['roles']['train'][:1]])
        predict(target, dataset.X.iloc[session['protocol']['roles']['train'][:1]])
        previous = session['active_model']
        session['active_model'] = deepcopy(target)
        if action == 'promote':
            session['promoted'] = True
            session['state'] = 'MONITORING'
        else:
            session['rolled_back'] = True
        self.repository.save(session, 'simulated_release' if action == 'promote' else 'simulated_rollback',
                             {'previous': previous, 'active': target, 'approved_revision': expected_revision}, actor=actor)
        return self.get_session(identifier)

    def promote(self, identifier, expected_revision, actor='user'):
        return self._approval(identifier, expected_revision, 'promote', actor)

    def rollback(self, identifier, expected_revision, actor='user'):
        return self._approval(identifier, expected_revision, 'rollback', actor)

    def reject_release(self, identifier, expected_revision, actor='user'):
        session = self.get_session(identifier)
        if session['revision'] != expected_revision or session['state'] != 'WAITING_RELEASE':
            raise ValueError('Stale release decision')
        session['state'] = 'MONITORING'
        self.repository.save(session, 'release_declined', {'candidate_sha256': session['candidate']['sha256']}, actor=actor)
        return self.get_session(identifier)

    def predict_active(self, identifier, X):
        session = self.get_session(identifier)
        if not session['active_model']:
            raise ValueError('No active model')
        return predict(session['active_model'], X)

    def export_session(self, identifier, destination=None):
        session = self.get_session(identifier)
        destination = Path(destination or self.root/'workspace/exports')
        directory = destination / f'{identifier}-{uuid.uuid4().hex[:8]}'
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory/'session.json', session)
        write_json(directory/'protocol.json', session['protocol'])
        write_json(directory/'policy.json', session['policy'])
        atomic_text(directory/'audit.jsonl', ''.join(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n' for event in session['events']))
        for role in ('champion', 'candidate'):
            if session[role]:
                reference = session[role]
                predict(reference, self._dataset(session).X.iloc[session['protocol']['roles']['train'][:1]])
                shutil.copy2(reference['path'], directory/f'{role}.txt')
                self.experiments.export_results(reference['experiment_id'], directory)
        atomic_text(directory/'report.md', f"# {identifier}\n\n{session['data_mode']} · offline tool sequence demonstration\n\n"
                    f"State: {session['state']}\n\nModel promotion/rollback only affects a local simulated slot.\n"
                    "Gate data was visible in historical research. One independent tool per condition; no confidence guarantee.\n"
                    "Sealed test was excluded. Gate failure and lack of improvement are valid outcomes.\n")
        return str(directory)


class TrainingBudgetExceeded(Exception):
    pass
