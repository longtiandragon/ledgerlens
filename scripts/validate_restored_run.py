"""Offline process-crash, backup and restart rehearsal on fresh synthetic data only.

The parent creates fresh data only. External network and model construction are
blocked in child processes. Fault injection exists only in this validator.
"""
import argparse
import asyncio
from contextlib import closing
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backup import backup  # noqa: E402
from scripts.backup_manifest import files, read_metadata, verify  # noqa: E402
from scripts.restore_local import restore  # noqa: E402
from scripts.validate_clean_checkout import isolated_environment  # noqa: E402

CRASH_CODE = 79
SYNTHETIC_USAGE = {'calls': 3, 'tokens': 123, 'tools': 2, 'reserved_tokens': 37}


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(root, filename='ledgerlens.sqlite', exclude=()):
    with closing(sqlite3.connect((root / filename).as_uri() + '?mode=ro', uri=True)) as db:
        output = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            if name in exclude:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [r[1] for r in db.execute('PRAGMA table_info(' + quoted + ')')]
            selected = [c for c in columns if not (name == 'documents' and c == 'storage_path')]
            output[name] = sorted(db.execute('SELECT ' + ','.join('"' + c.replace('"', '""') + '"' for c in selected)
                                             + ' FROM ' + quoted).fetchall(), key=repr)
        return output


async def worker(phase, point, artifact):
    # Import after isolated environment is established by the parent process.
    sys.path.insert(0, str(ROOT / 'backend'))
    attempts = []

    def network_guard(event, args):
        if event == 'socket.connect':
            # Windows asyncio's socketpair fallback connects to loopback.
            address = args[1]
            if isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback:
                return
            attempts.append(event)
            raise RuntimeError('Network is forbidden in restored-run validation')

    sys.addaudithook(network_guard)
    from app.config import settings
    require(not settings.deepseek_api_key and settings.task_mode == 'local', 'Offline local settings required')
    from app import model_gateway

    def no_model(*args, **kwargs):
        attempts.append('model_constructor')
        raise RuntimeError('Model construction is forbidden in restored-run validation')

    model_gateway.DeepSeekGateway = no_model
    from app.db import Run, Session, engine
    from app.main import app, lifespan, start_investigation, serialize
    from app.jobs import execute_run
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    artifact = Path(artifact)
    if phase == 'seed':
        from fastapi import BackgroundTasks, Request
        from fastapi.testclient import TestClient
        from app.schemas import InvestigateInput
        # A hand-written 100.00 - 90.00 voucher; expected difference is 10.00.
        common = {'kind': 'journal', 'business_date': '2026-09-01', 'period': '2026-09',
                  'currency': 'CNY', 'voucher_id': 'RESTORE-1'}
        ledger = [{**common, 'source_id': 'restore-debit', 'account': '1403', 'debit': '100.00', 'credit': '0.00'},
                  {**common, 'source_id': 'restore-credit', 'account': '2202', 'debit': '0.00', 'credit': '90.00'}]
        with TestClient(app) as client:
            def post(url, **kwargs):
                response = client.post(url, **kwargs)
                require(response.is_success, response.text)
                return response.json()
            book = post('/api/v1/books', json={'name': 'Synthetic restore rehearsal', 'framework': 'CAS', 'period': '2026-09'})
            batch = post('/api/v1/imports', data={'book_id': book['id']},
                         files={'file': ('synthetic-restore.json', json.dumps(ledger).encode(), 'application/json')})
            confirmed = post('/api/v1/imports/' + batch['id'] + '/confirm')
            check = post('/api/v1/check-runs', json={'book_id': book['id'], 'snapshot_id': confirmed['snapshot_id']})
            case = next(c for c in check['cases'] if c['rule_id'] == 'VOUCHER_BALANCE')
        with Session() as s:
            # Use the real endpoint transaction; leave its background delivery queued.
            queued = start_investigation(case['id'], InvestigateInput(mode='rules'), BackgroundTasks(),
                                         Request({'type': 'http', 'headers': []}), s)
            run = s.get(Run, queued['id'])
            run.usage = dict(SYNTHETIC_USAGE)
            s.commit()
            save(artifact, {'run_id': run.id, 'case_id': case['id'], 'book_id': book['id'],
                            'snapshot_id': confirmed['snapshot_id'], 'synthetic_persisted_usage': SYNTHETIC_USAGE,
                            'network_attempts': attempts})
    else:
        seeded = json.loads((artifact.parent / 'seed.json').read_text(encoding='utf-8'))
        identity = seeded['run_id']
        config = {'configurable': {'thread_id': identity}}
        if phase == 'crash':
            original = AsyncSqliteSaver.aput

            async def crash_after_commit(self, config, checkpoint, metadata, new_versions):
                result = await original(self, config, checkpoint, metadata, new_versions)
                values = checkpoint.get('channel_values', {})
                matched = ('base_status' in values and not values.get('result')) if point == 'detect' else bool(values.get('result'))
                if matched:
                    save(artifact, {'point': point, 'run_id': identity, 'checkpoint_id': checkpoint['id'],
                                    'has_result': bool(values.get('result')), 'network_attempts': attempts})
                    # Abrupt exit deliberately skips Python/SQLite/lease cleanup.
                    os._exit(CRASH_CODE)
                return result

            AsyncSqliteSaver.aput = crash_after_commit
            await execute_run(identity)
            raise RuntimeError('Expected committed-checkpoint crash point was not reached')
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / 'checkpoints.sqlite')) as saver:
            saved = await saver.aget_tuple(config)
            require(saved is not None, 'Restored checkpoint missing')
            checkpoint_id = saved.checkpoint['id']
            prior_result = saved.checkpoint['channel_values'].get('result')
        with Session() as s:
            before = serialize(s.get(Run, identity))
        async with lifespan(app):
            await asyncio.wait_for(asyncio.gather(*app.state.recovery_tasks), timeout=30)
            with Session() as s:
                finished = serialize(s.get(Run, identity))
            require(finished['status'] == 'complete', 'Recovery failed: ' + str(finished['error']))
            require(finished['active_slot'] is None and finished['worker_token'] is None, 'Lease not released')
            require(finished['result']['status'] == 'supported', 'Incorrect final status')
            require(finished['result']['claims'][0]['calculation']['difference'] == '10.00', 'Independent amount mismatch')
            require(finished['usage'] == SYNTHETIC_USAGE, 'Persisted budget changed or reset')
            require(sum(s['event'] == 'deterministic_check' for s in finished['steps']) == 1, 'Detection repeated')
            if prior_result:
                require(finished['result'] == prior_result, 'Completed checkpoint result was recomputed')
            # Two terminal deliveries must leave the entire saved Run unchanged.
            await asyncio.gather(execute_run(identity), execute_run(identity))
            with Session() as s:
                require(serialize(s.get(Run, identity)) == finished, 'Terminal redelivery changed run')
        async with lifespan(app):
            require(not app.state.recovery_tasks, 'Second restart requeued completed run')
            with Session() as s:
                require(serialize(s.get(Run, identity)) == finished, 'Second restart changed run')
        require(not attempts, 'Forbidden network or model activity attempted')
        save(artifact, {'status': 'passed', 'before_status': before['status'], 'restored_checkpoint_id': checkpoint_id,
                        'reused_completed_checkpoint': prior_result is not None, 'run': finished,
                        'duplicate_deliveries_unchanged': True, 'second_restart_unchanged': True,
                        'network_attempts': attempts, 'model_requests': 0})
    engine.dispose()


def validate(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 'restored-run-validation-v1', 'status': 'running', 'scenarios': [],
              'scope': 'Fresh synthetic SQLite, rules mode; real process exit after committed checkpoint; no remote service',
              'model_requests': 0, 'source_sha256': {p: digest(ROOT / p) for p in (
                  'scripts/validate_restored_run.py', 'scripts/backup.py', 'scripts/restore_local.py',
                  'backend/app/jobs.py', 'backend/app/investigation.py', 'backend/app/main.py')}}
    started = time.monotonic()

    def child(folder, data, phase, point, expected=0):
        artifact = folder / (phase + '.json')
        env = isolated_environment() | {'DATA_DIR': str(data), 'DATABASE_URL': 'sqlite:///' + (data / 'ledgerlens.sqlite').as_posix(),
            'TASK_MODE': 'local', 'DEEPSEEK_API_KEY': '', 'TELEMETRY_ENABLED': 'false', 'PYTHONPATH': str(ROOT / 'backend')}
        with (folder / (phase + '.log')).open('wb') as stream:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', phase, '--point', point,
                                     '--artifact', str(artifact)], cwd=folder, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=60,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        require(result.returncode == expected, f'{point}/{phase} exit {result.returncode}, expected {expected}; inspect retained log')
        return json.loads(artifact.read_text(encoding='utf-8'))

    try:
        for point in ('detect', 'finalize'):
            folder = output / point
            folder.mkdir()
            source = folder / 'source'
            seeded = child(folder, source, 'seed', point)
            crashed = child(folder, source, 'crash', point, CRASH_CODE)
            require(not crashed['network_attempts'], 'Crash worker attempted network')
            before = rows(source)
            with closing(sqlite3.connect((source / 'ledgerlens.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
                require(db.execute('SELECT status FROM agent_runs WHERE id=?', (seeded['run_id'],)).fetchone() == ('running',),
                        'Crash did not leave an interrupted running lease')
            saved = backup(source, folder / 'backup')
            metadata = read_metadata(saved)
            restored = restore(saved, folder / 'restored')
            require(rows(restored) == before, 'Restore altered business rows beyond source paths')
            require(rows(restored, 'checkpoints.sqlite') == rows(source, 'checkpoints.sqlite'), 'Checkpoint rows changed in restore')
            resumed = child(folder, restored, 'recover', point)
            require(resumed['before_status'] == 'running', 'Recovery did not receive interrupted run')
            require(resumed['restored_checkpoint_id'] == crashed['checkpoint_id'], 'Different checkpoint restored')
            require(resumed['reused_completed_checkpoint'] == (point == 'finalize'), 'Unexpected crash boundary')
            require(rows(source) == before, 'Original runtime mutated')
            require(rows(restored, exclude=('agent_runs',)) == rows(source, exclude=('agent_runs',)), 'Recovery mutated ledger/history')
            verify(saved, metadata)
            require(files(saved) == metadata['files'], 'Backup mutated after recovery')
            report['scenarios'].append({'point': point, 'status': 'passed', 'crash_exit': CRASH_CODE,
                'checkpoint_id': crashed['checkpoint_id'], 'reused_completed_checkpoint': resumed['reused_completed_checkpoint'],
                'synthetic_usage_preserved': resumed['run']['usage'], 'difference': '10.00',
                'original_business_rows_unchanged': True, 'restored_non_run_rows_unchanged': True,
                'duplicate_deliveries_unchanged': True, 'second_restart_unchanged': True, 'backup_unchanged': True,
                'evidence_sha256': {p.relative_to(output).as_posix(): digest(p) for p in folder.glob('*.json')}})
            print(point + ': passed', flush=True)
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        report['seconds'] = round(time.monotonic() - started, 2)
        save(output / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=('seed', 'crash', 'recover'), help=argparse.SUPPRESS)
    parser.add_argument('--point', choices=('detect', 'finalize'), help=argparse.SUPPRESS)
    parser.add_argument('--artifact', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        asyncio.run(worker(args.worker, args.point, args.artifact))
    else:
        if args.output is None:
            parser.error('--output is required')
        result = validate(args.output)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0 if result['status'] == 'passed' else 1)
