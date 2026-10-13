"""Validate a committed checkout without local state, keys, models or network installs.

Uses existing uv/npm caches, installed Python/Node/Chrome. This is an offline
reproducibility check, not a proof that an uncached network install succeeds.
All outputs and failed checkouts are retained; existing directories are refused.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def isolated_environment():
    # Do not inherit API keys, DB addresses, optional dataset flags or PYTHONPATH.
    keep = {'path', 'systemroot', 'windir', 'comspec', 'pathext', 'temp', 'tmp',
            'userprofile', 'appdata', 'localappdata', 'programfiles', 'programfiles(x86)',
            'programdata', 'homedrive', 'homepath', 'home'}
    return {k: v for k, v in os.environ.items() if k.lower() in keep} | {
        'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1', 'UV_PYTHON_DOWNLOADS': 'never',
        'npm_config_audit': 'false', 'npm_config_fund': 'false', 'NO_PROXY': '127.0.0.1,localhost'}


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def validate(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 'clean-checkout-validation-v1', 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(), 'steps': [], 'model_requests': 0,
              'validator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': 'Offline cached dependency install and committed-source local rule/browser workflow'}
    checkout, archive = output / 'checkout', output / 'source.zip'
    env, processes, handles = isolated_environment(), [], []
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def save():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def command(name, args, cwd, timeout=600, extra=None):
        started = time.monotonic()
        log = output / (name + '.log')
        item = {'name': name, 'status': 'running', 'log': log.name}
        report['steps'].append(item)
        save()
        print(name, flush=True)
        with log.open('wb') as stream:
            result = subprocess.run(args, cwd=cwd, env=env | (extra or {}), stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=timeout, creationflags=flags)
        item.update(exit_code=result.returncode, seconds=round(time.monotonic() - started, 2),
                    status='passed' if result.returncode == 0 else 'failed',
                    sha256=hashlib.sha256(log.read_bytes()).hexdigest())
        save()
        if result.returncode:
            raise RuntimeError(f'{name} failed; inspect {log}')

    def start(name, args, cwd, extra):
        stream = (output / (name + '.log')).open('wb')
        handles.append(stream)
        child = subprocess.Popen(args, cwd=cwd, env=env | extra, stdout=stream, stderr=subprocess.STDOUT, creationflags=flags)
        processes.append(child)
        return child

    opener = build_opener(ProxyHandler({}))

    def ready(url, child):
        for _ in range(80):
            if child.poll() is not None:
                raise RuntimeError('Owned service exited before readiness')
            try:
                with opener.open(url, timeout=1) as response:
                    if response.status == 200:
                        return response.read()
            except OSError:
                time.sleep(.25)
        raise TimeoutError('Owned service was not ready')

    try:
        report['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        command('archive', ['git', 'archive', '--format=zip', '-o', str(archive), report['source_commit']], ROOT)
        report['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        checkout.mkdir()
        with zipfile.ZipFile(archive) as packed:
            for entry in packed.infolist():
                if not (checkout / entry.filename).resolve().is_relative_to(checkout):
                    raise ValueError('Archive path escapes checkout')
            packed.extractall(checkout)
        report['excluded_local_state'] = {name: not (checkout / name).exists() for name in
            ('.env', '.local', '.cache', '.venv', 'runtime', 'frontend/node_modules')}
        if not all(report['excluded_local_state'].values()):
            raise ValueError('Committed archive unexpectedly contains local state')
        uv, npm, node = (shutil.which(name) for name in ('uv', 'npm.cmd' if os.name == 'nt' else 'npm', 'node'))
        if not all((uv, npm, node)):
            raise RuntimeError('uv, npm and Node must be installed')
        command('python-install', [uv, 'sync', '--frozen', '--offline', '--python', sys._base_executable,
            '--cache-dir', str(ROOT / '.cache/uv')], checkout)
        python = checkout / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
        command('frontend-install', [npm, 'ci', '--offline', '--cache', str(ROOT / '.cache/npm')], checkout / 'frontend')
        command('backend-tests', [str(python), '-m', 'pytest', '-q', '--junitxml=' + str(output / 'backend.xml')], checkout)
        suites = ET.parse(output / 'backend.xml').getroot().findall('testsuite')
        report['backend'] = {key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
        report['backend']['skip_reasons'] = sorted({s.get('message', '') for s in ET.parse(output / 'backend.xml').iter('skipped')})
        command('contracts', [str(python), 'scripts/generate_contracts.py', '--check'], checkout)
        command('frontend-build', [npm, 'run', 'build'], checkout / 'frontend')
        command('generate-synthetic-data', [str(python), '-m', 'datasets.generate', '--output', '.local/datasets'], checkout)
        command('rule-benchmark', [str(python), '-m', 'benchmarks.evaluate'], checkout,
                extra={'PYTHONPATH': str(checkout / 'backend') + os.pathsep + str(checkout)})
        report['rule_benchmark'] = json.loads((output / 'rule-benchmark.log').read_text(encoding='utf-8'))
        rules = report['rule_benchmark']
        if (rules['case_count'] != 480 or rules['failures'] or rules['exact_label_match_rate'] != 1
                or rules['reference_cases']['case_count'] != 30 or rules['reference_cases']['passed'] != 30
                or rules['model_calls'] != 0):
            raise ValueError('Rule benchmark did not meet its existing 480/30 acceptance contract')
        # Tests may create state; the live demo uses a separate initially absent data directory.
        demo_data = output / 'demo-data'
        assert not demo_data.exists()
        api_port, ui_port = free_port(), free_port()
        while api_port == ui_port:
            ui_port = free_port()
        api_url, ui_url = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{ui_port}'
        api_env = {'PYTHONPATH': str(checkout / 'backend') + os.pathsep + str(checkout),
                   'DATA_DIR': str(demo_data), 'DATABASE_URL': 'sqlite:///' + (demo_data / 'demo.sqlite').as_posix()}
        api = start('api', [str(python), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(api_port)], checkout, api_env)
        health = json.loads(ready(api_url + '/api/v1/health', api))
        if health.get('model_configured') is not False or health.get('mode') != 'local':
            raise ValueError('Clean API must have no model key and use local jobs')
        with opener.open(api_url + '/api/v1/books', timeout=3) as response:
            if json.loads(response.read()) != []:
                raise ValueError('Clean demo database is not empty')
        report['fresh_api'] = health | {'initial_books': 0}
        web = start('web', [node, str(checkout / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1',
                    '--port', str(ui_port), '--strictPort'], checkout / 'frontend', {'API_PROXY_TARGET': api_url})
        ready(ui_url, web)
        command('browser-tests', [npm, 'run', 'test:e2e', '--', '--reporter=json', '--output=' + str(output / 'browser-artifacts')],
                checkout / 'frontend', extra={'UI_URL': ui_url, 'API_PROXY_TARGET': api_url}, timeout=600)
        raw = (output / 'browser-tests.log').read_text(encoding='utf-8')
        browser = json.loads(raw[raw.index('{'):])
        report['browser'] = browser['stats']
        if report['browser']['unexpected'] or report['browser']['flaky']:
            raise ValueError('Browser failures or flakes are not acceptance')
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True, timeout=30, creationflags=flags)
                else:
                    child.terminate()
                child.wait(timeout=30)
        for handle in handles:
            handle.close()
        report['owned_processes_stopped'] = all(child.poll() is not None for child in processes)
        save()
    print(json.dumps({'status': report['status'], 'report': str(output / 'report.json')}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if validate(args.output)['status'] == 'passed' else 1)
