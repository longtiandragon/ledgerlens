"""Real isolated browser checks for evidence-scope UI and local OCR tables.

Uses fresh SQLite and blank provider credentials; never reads the working .env.
Outputs and failures are append-only. BGE/pgvector tests are excluded explicitly.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_clean_checkout import free_port, isolated_environment  # noqa: E402


def validate(output, fixture, ocr_python, models, *, reference_only=False):
    import httpx
    output, fixture, ocr_python, models = (p.resolve() for p in (output, fixture, ocr_python, models))
    if not fixture.is_file() or not ocr_python.is_file() or not models.is_dir():
        raise ValueError('Existing fixture, OCR runtime and pinned models required')
    output.mkdir(parents=True, exist_ok=False)
    processes, handles = [], []
    env = isolated_environment() | {'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    report = {'schema_version': 'reference-ocr-browser-v1', 'status': 'running',
              'model_provider_enabled': False, 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
              'scope': ('4 reference UI checks with synthetic intercepted responses' if reference_only else
                        '4 reference UI checks with synthetic intercepted responses; 4 real local OCR table cases'),
              'excluded': ['persistent BGE/pgvector integration'] + (['OCR cases'] if reference_only else []),
              'expected_tests': 4 if reference_only else 8}
    started = time.monotonic()

    def start(name, args, cwd, extra):
        stream = (output / (name + '.log')).open('x', encoding='utf-8')
        handles.append(stream)
        child = subprocess.Popen(args, cwd=cwd, env=env | extra, stdout=stream,
                                 stderr=subprocess.STDOUT, creationflags=flags)
        processes.append(child)
        return child

    def ready(url, process):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Owned validation service exited before readiness')
            try:
                response = httpx.get(url, timeout=2, trust_env=False)
                if response.status_code == 200:
                    return response
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        raise TimeoutError('Owned validation service did not become ready')

    try:
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node runtime required')
        api_port, ui_port = free_port(), free_port()
        while api_port == ui_port:
            ui_port = free_port()
        api_url, ui_url = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{ui_port}'
        api = start('api', [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
                           '--port', str(api_port)], output,
                    {'PYTHONPATH': str(ROOT / 'backend') + os.pathsep + str(ROOT),
                     'DATA_DIR': str(output / 'runtime'),
                     'DATABASE_URL': 'sqlite:///' + (output / 'runtime/app.sqlite').as_posix(),
                     'DEEPSEEK_API_KEY': '', 'TASK_MODE': 'local', 'AUTH_MODE': 'single_user',
                     'TELEMETRY_ENABLED': 'false', 'OCR_PYTHON': str(ocr_python),
                     'OCR_MODELS_DIR': str(models), 'OCR_TIMEOUT_SECONDS': '180'})
        if ready(api_url + '/api/v1/health', api).json().get('model_configured') is not False:
            raise RuntimeError('Paid model provider must be disabled')
        web = start('web', [node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1',
                           '--port', str(ui_port), '--strictPort'], ROOT / 'frontend', {'API_PROXY_TARGET': api_url})
        ready(ui_url, web)
        with (output / 'browser.json').open('x', encoding='utf-8') as log:
            specs = ['reference-indexes.spec.ts'] + ([] if reference_only else ['ocr-tables.spec.ts'])
            result = subprocess.run([node, str(ROOT / 'frontend/node_modules/@playwright/test/cli.js'), 'test',
                *specs, '--grep-invert=真实本地 BGE', '--workers=1',
                '--reporter=json', '--output=' + str(output / 'artifacts')], cwd=ROOT / 'frontend',
                env=env | {'UI_URL': ui_url, 'API_PROXY_TARGET': api_url, 'OCR_TABLE_TESTS': '1',
                           'OCR_WRAPPED_FIXTURE': str(fixture)},
                stdout=log, stderr=subprocess.STDOUT, timeout=900, creationflags=flags)
        raw = (output / 'browser.json').read_text(encoding='utf-8')
        browser = json.loads(raw[raw.index('{'):])
        stats = browser['stats']
        report.update(stats=stats, exit_code=result.returncode)
        passed = result.returncode == 0 and stats.get('expected') == report['expected_tests'] and all(
            stats.get(key) == 0 for key in ('skipped', 'unexpected', 'flaky')) and not browser.get('errors')
        report['status'] = 'passed' if passed else 'failed'
        report['evidence'] = [{'path': p.relative_to(output).as_posix(),
                               'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                              for p in sorted((output / 'artifacts').rglob('*')) if p.is_file()]
    except Exception as exc:
        report.update(status='failed', error=repr(exc))
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
        for handle in handles:
            handle.close()
        report['owned_services_stopped'] = all(p.poll() is not None for p in processes)
        report['duration_seconds'] = round(time.monotonic() - started, 3)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in ('status', 'stats', 'error', 'owned_services_stopped')}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'fixture', 'ocr-python', 'models'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--reference-only', action='store_true')
    args = parser.parse_args()
    raise SystemExit(validate(args.output, args.fixture, args.ocr_python, args.models, reference_only=args.reference_only))
