"""Run a real AI4I RAC-HPO task in a disposable isolated workspace."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen


PROJECT = Path(__file__).resolve().parents[2]


def request(port, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'X-Lab-Local': '1', 'Content-Type': 'application/json'}
    with urlopen(Request(f'http://127.0.0.1:{port}{path}', data=data, headers=headers), timeout=10) as response:
        return json.load(response)


def start_server(root, port):
    env = {**os.environ, 'LAB_ROOT': str(root), 'LAB_MODE': 'local', 'PORT': str(port),
           'MPLBACKEND': 'Agg', 'MPLCONFIGDIR': str(root / '.cache/matplotlib')}
    process = subprocess.Popen([sys.executable, str(PROJECT / 'backend/server.py')],
                               cwd=PROJECT, env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Backend exited during startup')
        try:
            request(port, '/api/me')
            return process
        except OSError:
            time.sleep(.1)
    process.terminate()
    raise RuntimeError('Backend startup timed out')


def stop_server(process):
    process.terminate()
    process.wait(timeout=10)


def main():
    with tempfile.TemporaryDirectory(dir=PROJECT / '智维云实施') as directory:
        root = Path(directory)
        shutil.copytree(PROJECT / 'rac-data/configs', root / 'configs')
        shutil.copytree(PROJECT / 'rac-data/datasets/raw/ai4i_2020', root / 'datasets/raw/ai4i_2020')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = start_server(root, port)
        try:
            catalog = request(port, '/api/datasets')
            assert next(item for item in catalog if item['id'] == 'ai4i_2020')['available']
            assert request(port, '/api/datasets/ai4i_2020/preview?limit=5')['rows']
            job = request(port, '/api/jobs', {'action': 'industrial_run', 'dataset': 'ai4i_2020',
                          'model': 'random_forest', 'algorithm': 'rac_hpo', 'seed': 42,
                          'estimators': 10, 'trials': 2, 'timeout': 60})
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                detail = request(port, '/api/jobs/' + job['id'])
                if detail['status'] not in ('running', 'cancelling'):
                    break
                time.sleep(.5)
            else:
                raise RuntimeError('Training timed out')
            if detail['status'] != 'completed':
                raise RuntimeError('Training failed: ' + str(detail.get('error')))
            result = detail['result']
            assert result['algorithm'] == 'rac_hpo' and result['status'] == 'completed'
            assert result['metrics'] and result['trials']
            run_id = result['id']
            assert request(port, '/api/runs/' + run_id)['id'] == run_id
            assert request(port, '/api/runs/' + run_id + '/trials')['total'] >= 1
            assert any(event.get('trial') for event in detail['events'])
            print('PASS: real AI4I RAC-HPO task, progress, saved result and trials')
        finally:
            stop_server(process)
        process = start_server(root, port)
        try:
            assert request(port, '/api/jobs/' + job['id'])['status'] == 'completed'
            assert request(port, '/api/runs/' + run_id)['id'] == run_id
            print('PASS: job and experiment remain available after restart')
        finally:
            stop_server(process)


if __name__ == '__main__':
    main()
