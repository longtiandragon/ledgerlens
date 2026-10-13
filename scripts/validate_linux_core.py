"""Offline Linux core validation using committed source and five pure test packages.

Windows parent exports Git and installed, locked py3-none-any packages. Ubuntu
worker uses /tmp (Linux filesystem), no system install, network or existing data.
This is not a full backend/HTTP/browser or OR-Tools installation check.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distribution
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('pytest', 'pluggy', 'iniconfig', 'packaging', 'pygments')
TESTS = ('backend/tests/test_matching.py', 'backend/tests/test_restore.py',
         'backend/tests/test_backup_manifest.py', 'backend/tests/test_backup_links.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def package_bundle(output):
    locked = {(p['name'], p['version']) for p in tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))['package']}
    metadata = []
    names = set()
    with zipfile.ZipFile(output / 'test-packages.zip', 'x', zipfile.ZIP_DEFLATED) as bundle:
        for name in PACKAGES:
            installed = distribution(name)
            require((name, installed.version) in locked, 'Installed package differs from lock: ' + name)
            wheel = installed.read_text('WHEEL') or ''
            require('Root-Is-Purelib: true' in wheel and 'Tag: py3-none-any' in wheel, 'Package is not portable pure Python')
            base = Path(installed.locate_file('')).resolve()
            payload = {}
            for relative in installed.files or []:
                path = Path(installed.locate_file(relative)).resolve()
                # Windows console launchers and bytecode are not portable payload.
                if not path.is_relative_to(base) or path.suffix == '.pyc':
                    continue
                require(path.suffix.lower() not in {'.exe', '.dll', '.pyd', '.so'}, 'Native payload in pure wheel')
                data = path.read_bytes()
                if relative.hash:
                    actual = base64.urlsafe_b64encode(hashlib.new(relative.hash.mode, data).digest()).rstrip(b'=').decode()
                    require(actual == relative.hash.value, 'Installed file differs from RECORD')
                filename = path.relative_to(base).as_posix()
                require(filename not in names, 'Duplicate package file')
                names.add(filename)
                bundle.writestr(filename, data)
                payload[filename] = hashlib.sha256(data).hexdigest()
            require(payload, 'Empty test package')
            metadata.append({'name': name, 'version': installed.version,
                             'license': installed.metadata.get('License-Expression') or installed.metadata.get('License'),
                             'wheel': wheel, 'files': payload})
    return metadata


def unpack(archive, destination):
    destination.mkdir()
    with zipfile.ZipFile(archive) as packed:
        for item in packed.infolist():
            target = (destination / item.filename).resolve()
            require(target.is_relative_to(destination), 'Archive path escapes extraction root')
            require((item.external_attr >> 16) & 0o170000 != 0o120000, 'Archive links are not supported')
        packed.extractall(destination)


def linux_worker(output):
    require(sys.platform == 'linux', 'Linux worker requires Linux')
    output = output.resolve()
    bundle = json.loads((output / 'bundle.json').read_text(encoding='utf-8'))
    for name, expected in bundle['archives'].items():
        require(digest(output / name) == expected, 'Bundle archive changed')
    workspace = Path(tempfile.mkdtemp(prefix='ledgerlens-linux-core-')).resolve()
    checkout, vendor = workspace / 'checkout', workspace / 'test-packages'
    report = {'schema_version': 'linux-core-validation-v1', 'status': 'running', 'steps': [],
              'source_commit': bundle['source_commit'], 'workspace': str(workspace),
              'platform': platform.platform(), 'python': sys.version, 'started_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'Standard-library rule benchmark, bounded-enumeration matching and SQLite backup/restore on WSL Linux',
              'model_requests': 0, 'system_packages_installed': 0, 'network_downloads': 0}
    started = time.monotonic()
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(workspace), 'LANG': 'C.UTF-8', 'PYTHONUTF8': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
           'PYTHONPATH': os.pathsep.join(map(str, (vendor, checkout / 'backend', checkout))),
           'DEEPSEEK_API_KEY': '', 'DATA_DIR': str(workspace / 'data'),
           'DATABASE_URL': 'sqlite:///' + str(workspace / 'data/ledgerlens.sqlite')}

    def command(name, args):
        log = output / (name + '.log')
        clock = time.monotonic()
        with log.open('wb') as stream:
            result = subprocess.run(args, cwd=checkout, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        report['steps'].append({'name': name, 'exit_code': result.returncode,
                                'seconds': round(time.monotonic()-clock, 2), 'sha256': digest(log)})
        require(result.returncode == 0, name + ' failed; inspect retained log')

    try:
        unpack(output / 'source.zip', checkout)
        unpack(output / 'test-packages.zip', vendor)
        for package in bundle['packages']:
            for path, expected in package['files'].items():
                require(digest(vendor / path) == expected, 'Extracted package hash changed')
        source_hashes = {p.relative_to(checkout).as_posix(): digest(p) for p in checkout.rglob('*') if p.is_file()}
        require(not any((checkout / p).exists() for p in ('.env', '.local', 'runtime', '.venv', 'frontend/node_modules')), 'Source contains local state')
        require(not (vendor / 'ortools').exists(), 'Unexpected native solver')
        command('rules', [sys.executable, '-m', 'benchmarks.evaluate'])
        rules = json.loads((output / 'rules.log').read_text(encoding='utf-8'))
        require(rules['case_count'] == 480 and rules['exact_label_match_rate'] == 1 and not rules['failures'], 'Rule benchmark failed')
        require(rules['reference_cases']['case_count'] == rules['reference_cases']['passed'] == 30, 'Reference cases failed')
        report['rules'] = rules
        config = workspace / 'pytest.ini'
        config.write_text('[pytest]\n', encoding='utf-8')
        command('tests', [sys.executable, '-m', 'pytest', '-q', '-c', str(config),
                          *TESTS, '--junitxml=' + str(output / 'tests.xml')])
        suite = ET.parse(output / 'tests.xml').getroot().find('testsuite')
        report['tests'] = {k: int(suite.get(k, 0)) for k in ('tests', 'failures', 'errors', 'skipped')}
        require(report['tests']['tests'] > 44 and all(report['tests'][k] == 0 for k in ('failures', 'errors', 'skipped')), 'Tests failed or skipped')
        require(all(digest(checkout / p) == value for p, value in source_hashes.items()), 'Committed source changed during checks')
        report['source_files_unchanged'] = len(source_hashes)
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
    finally:
        report['seconds'] = round(time.monotonic()-started, 2)
        save(output / 'report.json', report)
    return report


def windows_parent(output):
    require(os.name == 'nt', 'Parent prepares this existing Windows checkout for Ubuntu WSL')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    subprocess.run(['git', 'archive', '--format=zip', '-o', str(output / 'source.zip'), commit], cwd=ROOT, check=True)
    with zipfile.ZipFile(output / 'source.zip') as archive:
        require(archive.read('scripts/validate_linux_core.py') == Path(__file__).read_bytes(), 'Commit the validator before running')
    metadata = package_bundle(output)
    save(output / 'bundle.json', {'source_commit': commit, 'packages': metadata,
                                  'archives': {p: digest(output / p) for p in ('source.zip', 'test-packages.zip')}})
    def linux_path(path):
        return subprocess.check_output(['wsl', '-d', 'Ubuntu', '--exec', 'wslpath', '-a', str(path)], text=True).strip()
    with (output / 'worker.log').open('wb') as stream:
        result = subprocess.run(['wsl', '-d', 'Ubuntu', '--exec', 'python3', linux_path(Path(__file__).resolve()),
                                 '--linux-worker', '--output', linux_path(output)], cwd=output,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    require(result.returncode == 0, 'Linux worker failed; inspect worker.log and report.json')
    return json.loads((output / 'report.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--linux-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    report = linux_worker(args.output) if args.linux_worker else windows_parent(args.output)
    print(json.dumps({k: v for k, v in report.items() if k in {'status', 'error', 'tests', 'seconds'}}, ensure_ascii=False))
    raise SystemExit(0 if report['status'] == 'passed' else 1)
