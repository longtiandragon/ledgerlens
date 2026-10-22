"""Bounded real-HTTP load and relocated SQLite restore on fresh synthetic data.

Creates one new output directory, starts one isolated server at a time, denies
external network/model construction, and never connects to the working ledger.
This is same-host validation, not independent-machine or production capacity.
"""
import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backup import backup  # noqa: E402
from scripts.backup_manifest import files, read_metadata, verify  # noqa: E402
from scripts.restore_local import restore  # noqa: E402
from scripts.validate_clean_checkout import free_port, isolated_environment  # noqa: E402
from scripts.validate_restored_run import rows  # noqa: E402


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serve(port):
    attempts = []

    def deny_external(event, args):
        if event != 'socket.connect':
            return
        address = args[1]
        if isinstance(address, tuple):
            try:
                if ipaddress.ip_address(address[0]).is_loopback:
                    return
            except ValueError:
                pass
        attempts.append('external_connection')
        raise RuntimeError('External network forbidden in synthetic load validation')

    sys.addaudithook(deny_external)
    sys.path.insert(0, str(ROOT / 'backend'))
    from app.config import settings
    require(not settings.deepseek_api_key and settings.task_mode == 'local', 'Offline local mode required')
    require(settings.data_dir.resolve().parent == Path.cwd().resolve(), 'Unexpected isolated data directory')
    from app import model_gateway

    def deny_model(*args, **kwargs):
        attempts.append('model_constructor')
        raise RuntimeError('Model calls forbidden in synthetic load validation')

    model_gateway.DeepSeekGateway = deny_model
    from app.main import app
    from app.db import engine
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', access_log=False))

    def stop_from_parent():
        sys.stdin.readline()
        server.should_exit = True

    threading.Thread(target=stop_from_parent, daemon=True).start()
    try:
        server.run()
    finally:
        engine.dispose()
        save(settings.data_dir.parent / (settings.data_dir.name + '-guard.json'),
             {'forbidden_attempts': attempts, 'model_requests': 0})


class IsolatedServer:
    def __init__(self, folder, data):
        self.folder, self.data = folder, data
        self.port = free_port()
        self.base_url = f'http://127.0.0.1:{self.port}'
        self.process = None
        self.log = None

    async def start(self):
        env = isolated_environment() | {'DATA_DIR': str(self.data),
            'DATABASE_URL': 'sqlite:///' + (self.data / 'ledgerlens.sqlite').as_posix(),
            'TASK_MODE': 'local', 'AUTH_MODE': 'single_user', 'DEEPSEEK_API_KEY': '',
            'TELEMETRY_ENABLED': 'false', 'PYTHONPATH': str(ROOT / 'backend')}
        self.log = (self.folder / (self.data.name + '-server.log')).open('wb')
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve', str(self.port)],
            cwd=self.folder, env=env, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 45
        async with httpx.AsyncClient(base_url=self.base_url, trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                require(self.process.poll() is None, 'Isolated API exited; inspect retained log')
                try:
                    reply = await client.get('/api/v1/health')
                    if reply.status_code == 200:
                        require(reply.json() == {'status': 'ok', 'mode': 'local', 'model_configured': False},
                                'Unexpected API mode or configured model')
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(.1)
        raise TimeoutError('Isolated API did not become ready')

    async def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.stdin.write(b'stop\n')
                self.process.stdin.flush()
                try:
                    await asyncio.to_thread(self.process.wait, 20)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    await asyncio.to_thread(self.process.wait, 10)
                    raise RuntimeError('API required forced termination; not a graceful-stop pass')
            require(self.process.returncode == 0, 'Isolated API failed')
        if self.log:
            self.log.close()
        if self.process:
            self.process.stdin.close()
            guard = json.loads((self.folder / (self.data.name + '-guard.json')).read_text(encoding='utf-8'))
            require(not guard['forbidden_attempts'], 'Forbidden network or model call attempted')


async def api(client, method, path, **kwargs):
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.TransportError as exc:
        raise RuntimeError(f'{method} {path}: transport {type(exc).__name__}: {exc!r}') from exc
    require(response.is_success, f'{method} {path}: {response.status_code}: {response.text[:200]}')
    return response.json()


async def seed(client):
    book = await api(client, 'POST', '/api/v1/books', json={
        'name': 'Synthetic bounded HTTP load and restore', 'framework': 'CAS', 'period': '2026-09'})
    common = {'kind': 'journal', 'business_date': '2026-09-01', 'period': '2026-09',
              'currency': 'CNY', 'voucher_id': 'LOAD-1'}
    ledger = [{**common, 'source_id': 'load-debit', 'account': '1405', 'debit': '100.00', 'credit': '0.00'},
              {**common, 'source_id': 'load-credit', 'account': '2202', 'debit': '0.00', 'credit': '90.00'}]
    batch = await api(client, 'POST', '/api/v1/imports', data={'book_id': book['id']},
                      files={'file': ('synthetic-load.json', json.dumps(ledger).encode(), 'application/json')})
    confirmed = await api(client, 'POST', '/api/v1/imports/' + batch['id'] + '/confirm')
    checked = await api(client, 'POST', '/api/v1/check-runs',
                        json={'book_id': book['id'], 'snapshot_id': confirmed['snapshot_id']})
    case = next(c for c in checked['cases'] if c['rule_id'] == 'VOUCHER_BALANCE')
    return {'book_id': book['id'], 'snapshot_id': confirmed['snapshot_id'], 'case_id': case['id'],
            'check_id': checked['id']}


async def views(client, seeded):
    paths = {'records': f"/api/v1/books/{seeded['book_id']}/records",
             'documents': f"/api/v1/books/{seeded['book_id']}/documents",
             'snapshot': f"/api/v1/snapshots/{seeded['snapshot_id']}",
             'overview': f"/api/v1/books/{seeded['book_id']}/overview"}
    return {key: await api(client, 'GET', value) for key, value in paths.items()}


def statistics(samples):
    values = sorted(samples)
    def percentile(p):
        return round(values[min(len(values) - 1, int((len(values) - 1) * p))] * 1000, 2)
    return {'requests': len(values), 'p50_ms': percentile(.5), 'p95_ms': percentile(.95),
            'p99_ms': percentile(.99), 'max_ms': round(values[-1] * 1000, 2)}


async def run_load(client, seeded, original, duration, concurrency):
    deadline = time.monotonic() + duration
    samples, run_ids = defaultdict(list), []
    checks = {'book_id': seeded['book_id'], 'snapshot_id': seeded['snapshot_id']}
    paths = [('GET', f"/api/v1/books/{seeded['book_id']}/records", 'records'),
             ('GET', f"/api/v1/snapshots/{seeded['snapshot_id']}", 'snapshot'),
             ('POST', '/api/v1/check-runs', 'check'),
             ('GET', f"/api/v1/books/{seeded['book_id']}/overview", 'overview')]

    async def reader(worker_id):
        index = worker_id
        while time.monotonic() < deadline:
            method, path, label = paths[index % len(paths)]
            started = time.perf_counter()
            result = await api(client, method, path, **({'json': checks} if method == 'POST' else {}))
            samples[label].append(time.perf_counter() - started)
            require(result['id'] == seeded['check_id'] if label == 'check' else result == original[label],
                    'Concurrent read/check changed original ledger, snapshot, or check identity')
            index += 1
            await asyncio.sleep(.25)

    async def investigator():
        while time.monotonic() < deadline:
            started = time.perf_counter()
            run = await api(client, 'POST', f"/api/v1/cases/{seeded['case_id']}/investigate", json={'mode': 'rules'})
            run_ids.append(run['id'])
            finish_deadline = time.monotonic() + 20
            while time.monotonic() < finish_deadline:
                run = await api(client, 'GET', f"/api/v1/runs/{run['id']}")
                if run['status'] not in ('queued', 'running'):
                    break
                await asyncio.sleep(.1)
            require(run['status'] == 'complete' and run['result']['status'] == 'supported', 'Investigation did not complete')
            require(run['result']['claims'][0]['calculation']['difference'] == '10.00', 'Independent 10.00 oracle failed')
            samples['investigation_end_to_end'].append(time.perf_counter() - started)
            await asyncio.sleep(min(5, max(0, deadline - time.monotonic())))

    tasks = [asyncio.create_task(reader(i)) for i in range(concurrency)] + [asyncio.create_task(investigator())]
    try:
        await asyncio.gather(*tasks)
    except Exception as exc:
        exc.load_progress = {'elapsed_seconds': round(duration - max(0, deadline - time.monotonic()), 2),
                             'results': {key: statistics(value) for key, value in samples.items()},
                             'investigation_ids': run_ids}
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    require(len(run_ids) > 0, 'No real investigations were executed')
    return {'duration_requested_seconds': duration, 'read_check_clients': concurrency, 'periodic_investigators': 1,
            'pacing_seconds_per_read_client': .25, 'investigation_ids': run_ids,
            'results': {key: statistics(value) for key, value in samples.items()}, 'failed_requests': 0}


async def validate(output, duration, concurrency):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 'recovery-load-v1', 'status': 'running',
              'created_at': datetime.now(timezone.utc).isoformat(), 'model_requests': 0,
              'independent_machine': False, 'database': 'SQLite', 'task_mode': 'local',
              'http_policy': {'client_keepalive_expiry_seconds': 2, 'server_keepalive_seconds': 5,
                              'transport_retries': 0, 'request_timeout_seconds': 25},
              'latency_clock': 'time.perf_counter',
              'source_sha256': {p: digest(ROOT / p) for p in
                ('scripts/validate_recovery_load.py', 'scripts/backup.py', 'scripts/restore_local.py',
                 'backend/app/main.py', 'backend/app/jobs.py')},
              'limits': ['Same machine, one API process, two source rows; not a capacity or enterprise soak test.',
                         'No PostgreSQL/Celery/OCR/model requests or independent-machine validation.',
                         'Backup occurs only after the writer process stops; no online backup or power-loss claim.']}
    save(output / 'report.json', report)
    started = time.monotonic()
    server = IsolatedServer(output, output / 'source')
    try:
        await server.start()
        # Expire client idle sockets before uvicorn's 5 s keepalive deadline.
        # Investigations are paced at 5 s; equal deadlines can race on Windows.
        # A transport failure still fails the run: no retry or dropped request.
        async with httpx.AsyncClient(base_url=server.base_url, timeout=25, trust_env=False,
                                     limits=httpx.Limits(keepalive_expiry=2)) as client:
            seeded = await seed(client)
            original = await views(client, seeded)
            require(len(original['records']) == 2, 'Unexpected seed records')
            report['load'] = await run_load(client, seeded, original, duration, concurrency)
            require(await views(client, seeded) == original, 'Original views changed under load')
            finished_runs = {identity: await api(client, 'GET', '/api/v1/runs/' + identity)
                             for identity in report['load']['investigation_ids']}
        await server.stop()
        server = None
        before = rows(output / 'source')
        backup_path = backup(output / 'source', output / 'backup')
        metadata = read_metadata(backup_path)
        restored = restore(backup_path, output / 'restored')
        require(rows(restored) == before, 'Restored business rows differ beyond document paths')
        require(rows(restored, 'checkpoints.sqlite') == rows(output / 'source', 'checkpoints.sqlite'),
                'Restored checkpoint rows differ')
        server = IsolatedServer(output, restored)
        await server.start()
        async with httpx.AsyncClient(base_url=server.base_url, timeout=25, trust_env=False,
                                     limits=httpx.Limits(keepalive_expiry=2)) as client:
            require(await views(client, seeded) == original, 'Restored HTTP views changed')
            for identity, expected in finished_runs.items():
                require(await api(client, 'GET', '/api/v1/runs/' + identity) == expected, 'Restored run changed')
            documents = await api(client, 'GET', f"/api/v1/books/{seeded['book_id']}/documents")
            for document in documents:
                response = await client.get('/api/v1/documents/' + document['id'] + '/file')
                require(response.is_success and hashlib.sha256(response.content).hexdigest() == document['sha256'],
                        'Restored document download failed hash verification')
        await server.stop()
        server = None
        require(rows(output / 'source') == before, 'Original synthetic source mutated during restore')
        require(rows(restored) == before, 'Restored startup changed completed business rows')
        verify(backup_path, metadata)
        require(files(backup_path) == metadata['files'], 'Backup changed during restore')
        report['checks'] = {'original_ledger_unchanged': True, 'restored_business_rows_equal': True,
                            'restored_checkpoint_rows_equal': True, 'restored_http_views_equal': True,
                            'restored_complete_runs_equal': True, 'restored_download_hashes_equal': True,
                            'backup_unchanged': True, 'external_network_and_models_blocked': True}
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        report['traceback'] = traceback.format_exc()
        if hasattr(exc, 'load_progress'):
            report['partial_load'] = exc.load_progress
    finally:
        if server is not None:
            try:
                await server.stop()
            except Exception as exc:
                report['cleanup_error'] = {'type': type(exc).__name__, 'message': str(exc)}
                report['status'] = 'failed'
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        save(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--clients', type=int, default=2)
    parser.add_argument('--serve', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return 0
    if args.output is None or not 10 <= args.seconds <= 600 or not 1 <= args.clients <= 4:
        parser.error('--output required; seconds 10..600; clients 1..4')
    report = asyncio.run(validate(args.output, args.seconds, args.clients))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
