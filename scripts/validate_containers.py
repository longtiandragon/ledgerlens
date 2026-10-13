"""Container acceptance for PostgreSQL, Redis and Celery; no model calls.

Runs against an already started ``docker compose`` stack whose DEEPSEEK_API_KEY
is blank, so only deterministic rule investigations are possible. Fault
injection is explicit: a stopped worker, a duplicate Celery delivery and a
database-simulated dead lease (a worker that claimed a run and vanished).
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, 'DEEPSEEK_API_KEY': ''}


def source_heads() -> tuple[str, ...]:
    """Read migration scripts, without loading env.py or connecting to a database."""
    heads = tuple(sorted(ScriptDirectory(str(ROOT / 'backend/migrations')).get_heads()))
    if not heads:
        raise ValueError('No source migration head')
    return heads


def migrated_to_head(current: str, expected: tuple[str, ...]) -> bool:
    # Non-verbose Alembic current: each applied head is a complete output line.
    # Compare against this checkout, so a stale image cannot certify its own head.
    lines = [line.strip() for line in current.splitlines() if line.strip()]
    return bool(expected) and sorted(lines) == sorted(f'{head} (head)' for head in expected)


def require_preflight(checks: dict) -> None:
    failed = [name for name, passed in checks.items() if passed is not True]
    if failed:
        raise RuntimeError('Container preflight failed: ' + ', '.join(failed))


def service_state(name: str) -> tuple[bool, list[dict]]:
    observed = [json.loads(line) for line in compose(
        'ps', '--all', '--format', 'json', name).splitlines() if line.strip()]
    running = (len(observed) == 1 and observed[0].get('Service') == name
               and observed[0].get('State') == 'running'
               and observed[0].get('Health') in (None, '', 'healthy'))
    return running, [{'service': item.get('Service'), 'state': item.get('State'),
                      'health': item.get('Health')} for item in observed]


def compose(*args: str, check: bool = True) -> str:
    result = subprocess.run(['docker', 'compose', *args], cwd=ROOT, env=ENV, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=300)
    if check and result.returncode:
        raise RuntimeError(f'docker compose {args[0]} failed: {result.stderr.strip()[-400:]}')
    return result.stdout


def psql(sql: str) -> str:
    return compose('exec', '-T', 'postgres', 'psql', '-U', 'ledgerlens', '-d', 'ledgerlens', '-tAc', sql).strip()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def wait_healthy(client: httpx.Client, seconds: float = 120) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if client.get('/api/v1/health').status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def verify(base_url: str, web_url: str) -> dict:
    report = {'schema_version': 'container-acceptance-v1', 'started_at': datetime.now(UTC).isoformat(),
              'checks': {}, 'model_requests': 0, 'synthetic_book': True,
              'scope': 'container_runtime_and_task_semantics_not_accounting_accuracy'}
    checks = report['checks']
    pending_services: set[str] = set()

    def stop_services(*names: str) -> None:
        # Preflight proves these services initially ran. Record intent first:
        # a failed/interrupting stop may already have stopped part of the group.
        pending_services.update(names)
        compose('stop', *names)

    def start_services(*names: str) -> None:
        compose('start', *names)
        for name in names:
            if not service_state(name)[0]:
                raise RuntimeError('Service not running after start: ' + name)
            pending_services.discard(name)

    with httpx.Client(base_url=base_url, timeout=60, trust_env=False) as client:
        def get(path, **kwargs):
            response = client.get('/api/v1' + path, **kwargs)
            response.raise_for_status()
            return response.json()

        def post(path, value=None):
            response = client.post('/api/v1' + path, json=value)
            response.raise_for_status()
            return response.json()

        def wait_run(run_id, seconds=60, until=('complete', 'failed', 'budget_exhausted')):
            deadline = time.monotonic() + seconds
            current = get('/runs/' + run_id)
            while current['status'] not in until and time.monotonic() < deadline:
                time.sleep(.5)
                current = get('/runs/' + run_id)
            return current

        try:
            services = [json.loads(line) for line in compose('ps', '--format', 'json').splitlines() if line.strip()]
            report['services'] = {item['Service']: item.get('Health') or item['State'] for item in services}
            expected_services = {'postgres', 'redis', 'api', 'worker', 'scheduler', 'web'}
            checks['all_services_running'] = (len(services) == len(expected_services)
                and {item['Service'] for item in services} == expected_services
                and all(item['State'] == 'running' and item.get('Health') in (None, '', 'healthy')
                        for item in services))
            require_preflight(checks)
            health = get('/health')
            checks['api_uses_celery_and_no_model_key'] = health['mode'] == 'celery' and health['model_configured'] is False
            require_preflight(checks)
            current = compose('exec', '-T', 'api', 'alembic', 'current')
            report['alembic_current'] = current.strip()
            expected_heads = source_heads()
            report['expected_migration_heads'] = list(expected_heads)
            checks['postgres_migrated_to_head'] = migrated_to_head(current, expected_heads)
            require_preflight(checks)
            drift = subprocess.run(['docker', 'compose', 'exec', '-T', 'api', 'alembic', 'check'], cwd=ROOT, env=ENV,
                                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
            checks['postgres_schema_matches_models'] = drift.returncode == 0 and 'No new upgrade operations' in (
                drift.stdout + drift.stderr)
            require_preflight(checks)
            checks['pgvector_extension_available'] = psql(
                "select count(*) from pg_available_extensions where name='vector'") == '1'
            require_preflight(checks)

            book = post('/books', {'name': '容器验收 · 合成演示', 'framework': 'CAS', 'period': '2026-09'})
            report['book_id'] = book['id']
            base = '/books/' + book['id']
            demo = post(base + '/demo')
            records_before = digest(get(base + '/records'))
            snapshot = get('/snapshots/' + demo['snapshot_id'])
            cases = post('/check-runs', {'book_id': book['id'], 'snapshot_id': snapshot['id']})['cases']
            report['case_count'] = len(cases)
            checks['check_run_created_cases'] = len(cases) >= 2

            # 1. Normal Celery delivery.
            run = post('/cases/' + cases[0]['id'] + '/investigate', {'mode': 'rules'})
            done = wait_run(run['id'])
            checks['celery_worker_completes_rule_run'] = done['status'] == 'complete' and done['usage'].get('calls', 0) == 0
            report['run_ids'] = [run['id']]

            # 2. Duplicate delivery of a terminal run is a no-op.
            before = get('/runs/' + run['id'])
            compose('exec', '-T', 'worker', 'python', '-c',
                    f"from app.jobs import celery_investigate as t; [t.delay('{run['id']}') for _ in range(2)]")
            time.sleep(6)
            checks['duplicate_delivery_creates_no_second_result'] = get('/runs/' + run['id']) == before

            # 3. Work queued while the worker is down survives in Redis.
            stop_services('worker')
            queued = post('/cases/' + cases[1]['id'] + '/investigate', {'mode': 'rules'})
            time.sleep(3)
            still = get('/runs/' + queued['id'])['status']
            start_services('worker')
            finished = wait_run(queued['id'], 90)
            checks['queued_run_survives_worker_outage'] = still == 'queued' and finished['status'] == 'complete'
            report['run_ids'].append(queued['id'])

            # 4. A dead lease: claimed by a vanished worker. Its original Redis
            # message must not run it again; only stale-lease recovery may.
            # Scheduled recovery would race the explicit check: stop beat and
            # drain queued beat messages before enqueueing the orphan's message.
            stop_services('scheduler', 'worker')
            compose('run', '--rm', '--no-deps', 'worker', '.venv/bin/celery', '-A', 'app.jobs:celery_app',
                    'purge', '-f')
            orphan = post('/cases/' + cases[0]['id'] + '/investigate', {'mode': 'rules'})
            psql("update agent_runs set status='running', worker_token='vanished-worker', "
                 f"heartbeat='2000-01-01T00:00:00+00:00' where id='{orphan['id']}' and status='queued'")
            start_services('worker')
            time.sleep(6)
            dead = get('/runs/' + orphan['id'])
            redelivery_noop = dead['status'] == 'running' and not dead['steps']
            recovered = compose('exec', '-T', 'worker', 'python', '-c',
                                'from app.jobs import recover_stale_runs as r; print(r())')
            final = wait_run(orphan['id'], 90)
            token = psql(f"select coalesce(worker_token, '<none>') from agent_runs where id='{orphan['id']}'")
            checks['dead_lease_not_rerun_by_old_message'] = redelivery_noop
            checks['stale_lease_recovered_once'] = (final['status'] == 'complete' and token == '<none>'
                                                    and "'recovered': 1" in recovered)
            report['recovery_output'] = recovered.strip()
            start_services('scheduler')
            report['run_ids'].append(orphan['id'])

            # 5. Simulation, review and export on PostgreSQL.
            adjustment = [{'voucher_id': 'SIM1', 'account': '2202', 'debit': '100000.00', 'credit': '0.00',
                           'business_date': '2026-09-30', 'period': '2026-09'},
                          {'voucher_id': 'SIM1', 'account': '1405', 'debit': '0.00', 'credit': '100000.00',
                           'business_date': '2026-09-30', 'period': '2026-09'}]
            simulation = post('/simulations', {'case_id': cases[0]['id'], 'adjustments': adjustment})
            checks['simulation_exact_and_balanced'] = simulation['balanced'] and all(
                isinstance(value, str) for value in simulation['delta'].values())
            post('/reviews', {'case_id': cases[0]['id'], 'expected_snapshot_id': snapshot['id'],
                              'decision': 'reserved', 'comment': '容器验收'})
            paper = client.get(f"/api/v1/exports/cases/{cases[0]['id']}", params={'format': 'json'})
            checks['export_contains_runs_and_evidence'] = paper.status_code == 200 and bool(paper.json()['evidence'])

            # 6. Persistence across a full service restart.
            compose('restart', 'postgres', 'redis', 'api', 'worker', 'scheduler')
            checks['api_healthy_after_restart'] = wait_healthy(client)
            checks['records_unchanged_after_restart'] = digest(get(base + '/records')) == records_before
            checks['runs_persist_after_restart'] = all(get('/runs/' + identity)['status'] == 'complete'
                                                       for identity in report['run_ids'])
            checks['snapshot_unchanged'] = get('/snapshots/' + snapshot['id']) == snapshot
            case_ids = "','".join(case['id'] for case in cases)
            checks['no_extra_runs_from_redelivery_or_recovery'] = psql(
                f"select count(*) from agent_runs where case_id in ('{case_ids}')") == str(len(report['run_ids']))
            checks['active_slots_released'] = psql(
                "select count(*) from agent_runs where active_slot is not null and status in ('complete','failed')") == '0'

            web = httpx.get(web_url, timeout=20, trust_env=False)
            proxied = httpx.get(web_url.rstrip('/') + '/api/v1/health', timeout=20, trust_env=False)
            checks['web_served_and_api_proxied'] = web.status_code == 200 and proxied.json().get('mode') == 'celery'
            report['records_sha256'] = records_before
        except (Exception, KeyboardInterrupt) as exc:
            report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:400]}
        finally:
            # Only restore services stopped by this run. Keep the original error
            # even when restoration succeeds; command success alone is not proof
            # of a running container, nor does running prove Celery readiness.
            restoration = report['service_restoration'] = []
            for name in ('worker', 'scheduler'):
                if name not in pending_services:
                    continue
                entry = {'service': name, 'restored': False}
                restoration.append(entry)
                try:
                    compose('start', name)
                    entry['restored'], entry['observed_states'] = service_state(name)
                except (Exception, KeyboardInterrupt) as exc:
                    entry['error'] = {'type': type(exc).__name__, 'message': str(exc)[:400]}
            if restoration:
                checks['interrupted_services_restored'] = all(item['restored'] for item in restoration)
    report['completed_at'] = datetime.now(UTC).isoformat()
    report['passed'] = bool(checks) and all(checks.values()) and 'error' not in report
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--web-url', default='http://127.0.0.1:8080/')
    parser.add_argument('--output', type=Path, default=ROOT / '.local/reports/container-acceptance-v1.json')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output = args.output.open('x', encoding='utf-8', newline='\n')
    except FileExistsError:
        parser.error('Refusing to overwrite an existing acceptance report')
    with output:
        report = {'schema_version': 'container-acceptance-v1', 'status': 'running', 'passed': False,
                  'started_at': datetime.now(UTC).isoformat(), 'checks': {}}
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        output.flush()
        os.fsync(output.fileno())
        try:
            report = verify(args.base_url, args.web_url)
            report['status'] = 'completed' if report['passed'] else 'failed'
        except (Exception, KeyboardInterrupt) as exc:
            report.update(status='failed', completed_at=datetime.now(UTC).isoformat(),
                          error={'type': type(exc).__name__, 'message': str(exc)[:400]})
        output.seek(0)
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        output.truncate()
        output.flush()
        os.fsync(output.fileno())
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'error': report.get('error')},
                     ensure_ascii=False, indent=1))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
