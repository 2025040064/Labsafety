"""Separate transactional lifecycle storage; never migrates the experiment DB."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .artifacts import utc_now


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class LoopRepository:
    def __init__(self, root):
        self.path = Path(root) / 'workspace/database/lifecycle.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            existing = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if existing:
                try:
                    versions = db.execute('SELECT version FROM schema_version').fetchall()
                except sqlite3.OperationalError as error:
                    raise ValueError('Unversioned lifecycle database; explicit migration required') from error
                if [row[0] for row in versions] != [1]:
                    raise ValueError('Unsupported lifecycle schema; explicit migration required')
            schema = '''
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    payload TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES sessions(id));
                CREATE TABLE IF NOT EXISTS gate_uses(
                    fingerprint TEXT PRIMARY KEY, session_id TEXT NOT NULL, binding TEXT NOT NULL,
                    result TEXT, FOREIGN KEY(session_id) REFERENCES sessions(id));
                CREATE TABLE IF NOT EXISTS leases(
                    session_id TEXT PRIMARY KEY, pid INTEGER NOT NULL, token TEXT NOT NULL);
            '''
            # executescript implicitly commits; keep schema + version in this transaction.
            for statement in schema.split(';'):
                if statement.strip():
                    db.execute(statement)
            if not existing:
                db.execute('INSERT INTO schema_version VALUES (1)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('Unknown loop session')
            value = json.loads(row[0])
            value['events'] = [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM events WHERE session_id=? ORDER BY sequence', (identifier,))]
            return value

    def list(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM sessions ORDER BY rowid DESC')]

    def create(self, value):
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES (?,?)', (value['id'], encode(value)))
            self._event(db, value['id'], 'session_created', {'data_mode': value['data_mode']})

    def _event(self, db, identifier, kind, evidence, actor='system'):
        event = {'event_id': uuid.uuid4().hex, 'session_id': identifier, 'kind': kind,
                 'timestamp': utc_now(), 'actor': actor, 'evidence': evidence}
        db.execute('INSERT INTO events(session_id,payload) VALUES (?,?)', (identifier, encode(event)))

    def save(self, value, kind, evidence=None, actor='system', token=None):
        payload = {key: item for key, item in value.items() if key != 'events'}
        with self.connect() as db:
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (value['id'],)).fetchone()
            old = json.loads(row[0])
            if old['revision'] != value['revision']:
                raise ValueError('Stale loop session revision')
            lease = db.execute('SELECT token FROM leases WHERE session_id=?', (value['id'],)).fetchone()
            if lease and lease[0] != token:
                raise ValueError('Loop session is currently running')
            payload['revision'] += 1
            payload['updated_at'] = utc_now()
            db.execute('UPDATE sessions SET payload=? WHERE id=?', (encode(payload), value['id']))
            self._event(db, value['id'], kind, {'from_state': old['state'],
                        'to_state': value['state'], 'data_mode': value['data_mode'], **(evidence or {})}, actor)
        value.update(revision=payload['revision'], updated_at=payload['updated_at'])

    def acquire(self, identifier):
        token = uuid.uuid4().hex
        with self.connect() as db:
            row = db.execute('SELECT pid FROM leases WHERE session_id=?', (identifier,)).fetchone()
            if row:
                if process_alive(row[0]):
                    raise ValueError('Loop session is already running')
                db.execute('DELETE FROM leases WHERE session_id=?', (identifier,))
            db.execute('INSERT INTO leases VALUES (?,?,?)', (identifier, os.getpid(), token))
        return token

    def release(self, identifier, token):
        with self.connect() as db:
            db.execute('DELETE FROM leases WHERE session_id=? AND token=?', (identifier, token))

    def claim_gate(self, identifier, fingerprint, binding):
        with self.connect() as db:
            row = db.execute('SELECT session_id,binding,result FROM gate_uses WHERE fingerprint=?', (fingerprint,)).fetchone()
            if row:
                if row['session_id'] == identifier and row['binding'] == encode(binding) and row['result']:
                    return json.loads(row['result'])
                raise ValueError('Gate already consumed or interrupted; cannot evaluate another candidate')
            db.execute('INSERT INTO gate_uses VALUES (?,?,?,NULL)', (fingerprint, identifier, encode(binding)))
        return None

    def finish_gate(self, identifier, fingerprint, binding, result):
        with self.connect() as db:
            changed = db.execute('UPDATE gate_uses SET result=? WHERE fingerprint=? AND session_id=? '
                                 'AND binding=? AND result IS NULL',
                                 (encode(result), fingerprint, identifier, encode(binding))).rowcount
            if changed != 1:
                raise ValueError('Gate result cannot be overwritten')

    def gate_owner(self, fingerprint):
        with self.connect() as db:
            row = db.execute('SELECT session_id FROM gate_uses WHERE fingerprint=?', (fingerprint,)).fetchone()
            return row[0] if row else None

    def get_gate(self, fingerprint):
        with self.connect() as db:
            row = db.execute('SELECT session_id,binding,result FROM gate_uses WHERE fingerprint=?',
                             (fingerprint,)).fetchone()
            if row is None:
                return None
            return {'session_id': row['session_id'], 'binding': json.loads(row['binding']),
                    'result': json.loads(row['result']) if row['result'] else None}


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        code = wintypes.DWORD()
        try:
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
