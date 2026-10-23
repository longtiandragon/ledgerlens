"""Offline checks for the container acceptance runner; no Docker operations."""
import json
import sys

import httpx
import pytest

from scripts import validate_containers as runner


def test_existing_report_refused_before_any_validation(tmp_path, monkeypatch):
    path = tmp_path / 'report.json'
    path.write_bytes(b'original failed evidence\n')
    calls = []
    monkeypatch.setattr(sys, 'argv', ['validator', '--output', str(path)])
    monkeypatch.setattr(runner, 'verify', lambda *args: calls.append(args) or {'passed': True, 'checks': {}})
    with pytest.raises(SystemExit):
        runner.main()
    assert calls == []
    assert path.read_bytes() == b'original failed evidence\n'


@pytest.mark.parametrize('failure', [RuntimeError('readiness failed'), KeyboardInterrupt()])
def test_unexpected_failure_keeps_nonpassing_report(tmp_path, monkeypatch, failure):
    path = tmp_path / 'new' / 'report.json'
    monkeypatch.setattr(sys, 'argv', ['validator', '--output', str(path)])

    def fail(*args):
        # The output name must already be reserved before any remote action.
        reserved = json.loads(path.read_text(encoding='utf-8'))
        assert reserved['passed'] is False and reserved['status'] == 'running'
        raise failure

    monkeypatch.setattr(runner, 'verify', fail)
    with pytest.raises(SystemExit):
        runner.main()
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['passed'] is False and report['error']['type'] == type(failure).__name__
    assert report['status'] == 'failed'


def test_success_written_to_reserved_report(tmp_path, monkeypatch):
    path = tmp_path / 'report.json'
    monkeypatch.setattr(sys, 'argv', ['validator', '--output', str(path)])

    def done(*args):
        with pytest.raises(FileExistsError):
            path.open('x')
        return {'passed': True, 'checks': {'example': True}}

    monkeypatch.setattr(runner, 'verify', done)
    runner.main()
    assert json.loads(path.read_text(encoding='utf-8')) == {
        'passed': True, 'status': 'completed', 'checks': {'example': True}}


@pytest.mark.parametrize('current,expected', [
    ('0006 (head)\n', True), ('0003 (head)\n', False), ('0006\n', False),
    ('x0006 (head)\n', False), ('0006 (head)\n0003 (head)\n', False),
    ('0006 (head)\n0006 (head)\n', False), ('', False),
])
def test_migration_head_uses_current_source_exactly(current, expected):
    assert runner.migrated_to_head(current, ('0006',)) is expected


def test_repo_migration_head_is_not_historical_0003():
    assert runner.source_heads() == ('0008',)


@pytest.mark.parametrize('fault', ['key', 'mode', 'services', 'duplicate_service', 'unhealthy', 'migration', 'schema', 'pgvector'])
def test_failed_preflight_never_creates_book_or_stops_worker(monkeypatch, fault):
    docker_calls = []
    requests = []
    services = ('postgres', 'redis', 'api', 'worker', 'scheduler', 'web')

    def compose(*args, **kwargs):
        docker_calls.append(args)
        if args == ('ps', '--format', 'json'):
            names = services[:-1] if fault == 'services' else services
            if fault == 'duplicate_service':
                names = (*services[:-1], 'api')
            return '\n'.join(json.dumps({'Service': name, 'State': 'running',
                                         'Health': 'unhealthy' if fault == 'unhealthy' and name == 'api' else ''})
                             for name in names)
        if args == ('exec', '-T', 'api', 'alembic', 'current'):
            return '0003 (head)' if fault == 'migration' else '0008 (head)'
        if args[:4] == ('exec', '-T', 'postgres', 'psql'):
            return '0' if fault == 'pgvector' else '1'
        raise AssertionError('Unexpected Docker mutation: ' + repr(args))

    def respond(request):
        requests.append((request.method, request.url.path))
        assert request.method == 'GET' and request.url.path == '/api/v1/health'
        return httpx.Response(200, json={'mode': 'local' if fault == 'mode' else 'celery',
                                        'model_configured': fault == 'key'})

    original_client = httpx.Client
    monkeypatch.setattr(runner, 'compose', compose)
    monkeypatch.setattr(runner.httpx, 'Client', lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k: type('Result', (), {
        'returncode': 1 if fault == 'schema' else 0, 'stdout': 'No new upgrade operations', 'stderr': ''})())
    report = runner.verify('http://offline.invalid', 'http://offline.invalid')
    assert report['passed'] is False and 'error' in report
    assert all(method == 'GET' for method, _ in requests)
    assert all(call[0] in ('ps', 'exec') for call in docker_calls)
    assert 'book_id' not in report


def test_valid_source_head_allows_preflight_to_reach_synthetic_book(monkeypatch):
    """A fully mocked stack demonstrates the current head 0008 is accepted, not just old heads rejected."""
    seen = []

    def compose(*args, **kwargs):
        if args == ('ps', '--format', 'json'):
            return '\n'.join(json.dumps({'Service': name, 'State': 'running'})
                             for name in ('postgres', 'redis', 'api', 'worker', 'scheduler', 'web'))
        if args == ('exec', '-T', 'api', 'alembic', 'current'):
            return '0008 (head)\n'
        if args[:4] == ('exec', '-T', 'postgres', 'psql'):
            return '1'
        raise AssertionError('Unexpected Docker operation')

    def respond(request):
        seen.append((request.method, request.url.path))
        if request.method == 'GET' and request.url.path == '/api/v1/health':
            return httpx.Response(200, json={'mode': 'celery', 'model_configured': False})
        assert request.method == 'POST' and request.url.path == '/api/v1/books'
        # Deliberately stop before workflow/fault injection; no actual server is used.
        return httpx.Response(503, json={'detail': 'test ends after successful preflight'})

    original_client = httpx.Client
    monkeypatch.setattr(runner, 'compose', compose)
    monkeypatch.setattr(runner.httpx, 'Client', lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k: type('Result', (), {
        'returncode': 0, 'stdout': 'No new upgrade operations', 'stderr': ''})())
    report = runner.verify('http://offline.invalid', 'http://offline.invalid')
    assert seen == [('GET', '/api/v1/health'), ('POST', '/api/v1/books')]
    assert report['expected_migration_heads'] == ['0008']
    assert len(report['checks']) == 5 and all(report['checks'].values())
    assert report['passed'] is False and report['error']['type'] == 'HTTPStatusError'


@pytest.mark.parametrize('fault', ['queue_http', 'queue_interrupt', 'partial_stop', 'purge',
                                 'worker_restart', 'worker_exited', 'worker_probe', 'cleanup_interrupt',
                                 'normal_start_exited'])
def test_interrupted_fault_injection_restores_only_owned_services(monkeypatch, fault):
    calls = []
    stopped = set()
    investigating = 0
    cleanup = False

    def compose(*args, **kwargs):
        nonlocal cleanup
        calls.append(args)
        if args == ('ps', '--format', 'json'):
            return '\n'.join(json.dumps({'Service': name, 'State': 'running'})
                             for name in ('postgres', 'redis', 'api', 'worker', 'scheduler', 'web'))
        if args == ('exec', '-T', 'api', 'alembic', 'current'):
            return '0008 (head)'
        if args[:4] == ('exec', '-T', 'postgres', 'psql'):
            return '1'
        if args[0] == 'stop':
            stopped.update(args[1:])
            if fault == 'partial_stop':
                cleanup = True
                raise RuntimeError('stop result uncertain')
            return ''
        if args[0] == 'start':
            if cleanup and args[1] == 'worker' and fault in ('worker_restart', 'cleanup_interrupt'):
                raise RuntimeError('restart failed') if fault == 'worker_restart' else KeyboardInterrupt()
            stopped.difference_update(args[1:])
            return ''
        if args[:4] == ('exec', '-T', 'worker', 'python'):
            return ''  # Duplicate terminal-run delivery.
        if args[0] == 'run':
            cleanup = True
            raise RuntimeError('purge failed')
        if args[:4] == ('ps', '--all', '--format', 'json'):
            service = args[4]
            if cleanup and fault == 'worker_probe' and service == 'worker':
                raise RuntimeError('probe failed')
            if fault == 'normal_start_exited' and not cleanup:
                cleanup = True
                return json.dumps({'Service': service, 'State': 'exited'})
            return json.dumps({'Service': service, 'State': 'exited' if cleanup and fault == 'worker_exited' and service == 'worker' else 'running'})
        raise AssertionError('Unexpected Docker operation: ' + repr(args))

    def respond(request):
        nonlocal investigating, cleanup
        path = request.url.path.removeprefix('/api/v1')
        if path == '/health':
            data = {'mode': 'celery', 'model_configured': False}
        elif path == '/books':
            data = {'id': 'book'}
        elif path == '/books/book/demo':
            data = {'snapshot_id': 'snap'}
        elif path == '/books/book/records':
            data = []
        elif path == '/snapshots/snap':
            data = {'id': 'snap'}
        elif path == '/check-runs':
            data = {'cases': [{'id': 'case1'}, {'id': 'case2'}]}
        elif path.endswith('/investigate'):
            investigating += 1
            if investigating == 2 and fault in ('queue_http', 'queue_interrupt'):
                cleanup = True
                if fault == 'queue_interrupt':
                    raise KeyboardInterrupt()
                return httpx.Response(503, json={'detail': 'queued request failed'})
            data = {'id': 'run' + str(investigating)}
        elif path.startswith('/runs/'):
            data = {'status': 'queued' if 'worker' in stopped else 'complete', 'usage': {'calls': 0}}
        else:
            raise AssertionError('Unexpected HTTP request: ' + path)
        return httpx.Response(200, json=data)

    original_client = httpx.Client
    monkeypatch.setattr(runner, 'compose', compose)
    monkeypatch.setattr(runner.httpx, 'Client', lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k: type('Result', (), {
        'returncode': 0, 'stdout': 'No new upgrade operations', 'stderr': ''})())
    report = runner.verify('http://offline.invalid', 'http://offline.invalid')
    assert report['passed'] is False and 'error' in report
    assert report['book_id'] == 'book'  # Keep partial evidence on keyboard interruption too.
    entries = report['service_restoration']
    expected = ['worker'] if fault in ('queue_http', 'queue_interrupt', 'partial_stop', 'normal_start_exited') else ['worker', 'scheduler']
    assert [entry['service'] for entry in entries] == expected
    failing_cleanup = fault in ('worker_restart', 'worker_exited', 'worker_probe', 'cleanup_interrupt')
    assert report['checks']['interrupted_services_restored'] is (not failing_cleanup)
    assert entries[0]['restored'] is (not failing_cleanup)
    if len(entries) == 2:
        assert entries[1]['restored'] is True  # Failure restoring worker must not skip scheduler.
    assert all(call[1] in ('worker', 'scheduler') for call in calls if call[0] == 'start')
    assert not any(call[0] in ('down', 'restart') for call in calls)
