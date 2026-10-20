"""Capture README screenshots or a captioned demo recording on a fresh, isolated API (Apache-2.0).

Starts its own API on a new SQLite file inside the output directory (no model key, rules mode)
and its own Vite server, runs frontend/tests/demo-capture.spec.ts in Chrome, then stops only
the processes it started. Never touches the everyday database (see
docs/TEST_ISOLATION_INCIDENT_20261014.md). The output directory must not exist yet.

Usage:
  python scripts/capture_demo.py --output .local/demo/shots
  python scripts/capture_demo.py --output .local/demo/video --video --pause-ms 1800
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


def capture(output: Path, video: bool, pause_ms: int) -> int:
    import httpx
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    processes, handles = [], []
    env = isolated_environment()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    report = {'schema_version': 'demo-capture-v1', 'status': 'running', 'video': video, 'model_requests': 0}

    def start(name, args, cwd, extra):
        stream = (output / f'{name}.log').open('w', encoding='utf-8')
        handles.append(stream)
        child = subprocess.Popen(args, cwd=cwd, env=env | extra, stdout=stream, stderr=subprocess.STDOUT,
                                 creationflags=flags)
        processes.append(child)
        return child

    def ready(url, process):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('An owned capture service exited; inspect its log')
            try:
                response = httpx.get(url, timeout=2, trust_env=False)
                if response.status_code == 200:
                    return response
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        raise TimeoutError(url)

    try:
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Installed Node runtime is required')
        api_port = free_port()
        ui_port = free_port()
        while ui_port == api_port:
            ui_port = free_port()
        api_url, ui_url = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{ui_port}'
        api = start('api', [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(api_port)],
                    output, {'PYTHONPATH': str(ROOT / 'backend') + os.pathsep + str(ROOT),
                             'DATA_DIR': str(output / 'runtime'),
                             'DATABASE_URL': 'sqlite:///' + (output / 'runtime/app.sqlite').as_posix(),
                             'DEEPSEEK_API_KEY': '', 'TASK_MODE': 'local', 'AUTH_MODE': 'single_user',
                             'TELEMETRY_ENABLED': 'false'})
        if ready(api_url + '/api/v1/health', api).json().get('model_configured') is not False:
            raise RuntimeError('The model provider must be disabled for capture')
        web = start('web', [node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1',
                            '--port', str(ui_port), '--strictPort'], ROOT / 'frontend', {'API_PROXY_TARGET': api_url})
        ready(ui_url, web)
        shots = output / 'shots'
        shots.mkdir()
        with (output / 'playwright.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(
                [node, str(ROOT / 'frontend/node_modules/@playwright/test/cli.js'), 'test', 'demo-capture.spec.ts',
                 '--reporter=list', '--output=' + str(output / 'artifacts')], cwd=ROOT / 'frontend',
                env=env | {'UI_URL': ui_url, 'API_PROXY_TARGET': api_url, 'DEMO_CAPTURE': '1',
                           'DEMO_OUTPUT': str(shots), 'DEMO_VIDEO': '1' if video else '0',
                           'DEMO_PAUSE_MS': str(pause_ms)},
                stdout=log, stderr=subprocess.STDOUT, timeout=900, creationflags=flags)
        files = sorted([*shots.glob('*.png'), *(output / 'artifacts').rglob('*.webm')])
        report.update(exit_code=result.returncode,
                      files={str(f.relative_to(output)).replace('\\', '/'): hashlib.sha256(f.read_bytes()).hexdigest()
                             for f in files})
        report['status'] = 'passed' if result.returncode == 0 and files else 'failed'
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
    print(json.dumps({k: report.get(k) for k in ('status', 'exit_code', 'error')} |
                     {'files': len(report.get('files', {}))}, ensure_ascii=False))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--video', action='store_true', help='record a captioned video instead of screenshots')
    parser.add_argument('--pause-ms', type=int, default=1800, help='pause after each caption when recording')
    args = parser.parse_args()
    raise SystemExit(capture(args.output, args.video, args.pause_ms if args.video else 0))
