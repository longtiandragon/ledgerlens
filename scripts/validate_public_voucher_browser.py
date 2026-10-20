"""Browser regression on a fresh, isolated local API; no provider key or downloads."""
import argparse
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
from datasets.mof_vouchers import SAMPLES, verify_package  # noqa: E402


def browser_passed(exit_code, stats):
    return (exit_code == 0 and type(stats.get('expected')) is int and stats['expected'] >= 24
            and all(type(stats.get(key)) is int and stats[key] == 0 for key in ('skipped', 'unexpected', 'flaky')))


def validate(output: Path, bundle: Path):
    import httpx
    output, bundle = output.resolve(), bundle.resolve()
    output.mkdir(parents=True, exist_ok=False)
    processes, handles = [], []
    report = {'schema_version': 'public-voucher-browser-v1', 'status': 'running',
              'model_requests': 0, 'scope': 'Fresh SQLite, Chrome, public examples and policy/workbench browser regressions'}
    env = isolated_environment()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def start(name, args, cwd, extra):
        stream = (output / (name + '.log')).open('w', encoding='utf-8')
        handles.append(stream)
        child = subprocess.Popen(args, cwd=cwd, env=env | extra, stdout=stream, stderr=subprocess.STDOUT, creationflags=flags)
        processes.append(child)
        return child

    def ready(url, process):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Owned validation service exited; inspect logs')
            try:
                response = httpx.get(url, timeout=2, trust_env=False)
                if response.status_code == 200:
                    return response
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        raise TimeoutError(url)

    try:
        for filename in SAMPLES:
            verify_package(json.loads((bundle / (filename + '.json')).read_text(encoding='utf-8')))
        api_port, ui_port = free_port(), free_port()
        while api_port == ui_port:
            ui_port = free_port()
        api_url, ui_url = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{ui_port}'
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Installed Node runtime is required')
        api = start('api', [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(api_port)],
                    output, {'PYTHONPATH': str(ROOT / 'backend') + os.pathsep + str(ROOT),
                    'DATA_DIR': str(output / 'runtime'), 'DATABASE_URL': 'sqlite:///' + (output / 'runtime/app.sqlite').as_posix(),
                    'DEEPSEEK_API_KEY': '', 'TASK_MODE': 'local', 'AUTH_MODE': 'single_user', 'TELEMETRY_ENABLED': 'false'})
        health = ready(api_url + '/api/v1/health', api).json()
        if health.get('model_configured') is not False:
            raise RuntimeError('Provider must be disabled')
        # Use the installed Vite development server with an isolated API proxy.
        web = start('web', [node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1',
                           '--port', str(ui_port), '--strictPort'], ROOT / 'frontend', {'API_PROXY_TARGET': api_url})
        ready(ui_url, web)
        with (output / 'browser.json').open('w', encoding='utf-8') as log:
            result = subprocess.run([node, str(ROOT / 'frontend/node_modules/@playwright/test/cli.js'), 'test',
                'public-vouchers.spec.ts', 'policy-checks.spec.ts', 'policy-borrowing-live.spec.ts',
                'policy-revenue-cashflow-live.spec.ts',
                'workbench.spec.ts', '--reporter=json',
                '--output=' + str(output / 'artifacts')], cwd=ROOT / 'frontend',
                env=env | {'UI_URL': ui_url, 'API_PROXY_TARGET': api_url, 'MOF_VOUCHER_BUNDLE': str(bundle)},
                stdout=log, stderr=subprocess.STDOUT, timeout=600, creationflags=flags)
        raw = (output / 'browser.json').read_text(encoding='utf-8')
        browser = json.loads(raw[raw.index('{'):])
        report['stats'] = browser['stats']
        report['exit_code'] = result.returncode
        report['status'] = 'passed' if browser_passed(result.returncode, report['stats']) else 'failed'
    except Exception as exc:
        report['status'], report['error'] = 'failed', repr(exc)
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
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(validate(args.output, args.bundle))
