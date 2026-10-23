"""Run the 12-cell scenario acceptance matrix on a fresh, isolated database.

The environment is fixed before the application is imported: a new temporary
data directory and SQLite database, no model key, local tasks, single user.
External network is refused for the whole process. The report is written to a
new path only; an existing report is never overwritten. A failed cell exits 1.

    python scripts/validate_scenario_packs.py --output .local/reports/scenario-packs-<date>.json
"""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def refuse_network(event, args):
    if event != 'socket.connect' or not isinstance(args[1], tuple):
        return
    host = args[1][0]
    try:
        if host == 'localhost' or ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError('External network is refused while validating scenario packs')


def git(*args):
    try:
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f'Refusing to overwrite an existing report: {output}')
    workspace = Path(tempfile.mkdtemp(prefix='ledgerlens-scenarios-'))
    os.environ.update({'DATA_DIR': str(workspace), 'DATABASE_URL': 'sqlite:///' + (workspace / 'scenarios.sqlite').as_posix(),
                       'DEEPSEEK_API_KEY': '', 'TASK_MODE': 'local', 'AUTH_MODE': 'single_user', 'TELEMETRY_ENABLED': 'false'})
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        os.environ.pop(name, None)
    sys.addaudithook(refuse_network)
    sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.domain import rule_identity
    from app.main import app
    from app.treatment import treatment_identity
    from benchmarks.scenario_packs import run_all
    if not str(settings.database_url).endswith('scenarios.sqlite') or settings.deepseek_api_key:
        raise SystemExit('Isolation check failed: refusing to run against a configured database or model key')
    with TestClient(app) as client:
        report = run_all(client)
    report.update({'generated_at': datetime.now(timezone.utc).isoformat(), 'commit': git('rev-parse', 'HEAD'),
                   'working_tree_clean': git('status', '--porcelain') == '', 'python': platform.python_version(),
                   'execution_rules': rule_identity(), 'treatment': treatment_identity(),
                   'environment': 'fresh temporary SQLite database, no model key, external network refused',
                   'model_calls': 0})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f"{report['passed']}/{report['cell_count']} cells passed; report: {output}")
    for pack, row in report['matrix'].items():
        print(pack, ' '.join(f"{condition}={'PASS' if ok else 'FAIL'}" for condition, ok in row.items()))
    return 0 if not report['failed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
