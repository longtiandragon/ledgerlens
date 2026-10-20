"""Collect pinned public examples and run the isolated API/browser delivery checks.

Use --archive for a fully offline run after dependencies are installed. Otherwise
only collection downloads from the pinned MOF URL. Each run requires a new output
directory; failures and all earlier stage evidence remain available for inspection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from datasets.mof_vouchers import collect, SAMPLES, verify_package  # noqa: E402
from scripts.validate_clean_checkout import isolated_environment  # noqa: E402
from scripts.validate_public_voucher_browser import browser_passed, validate as validate_browser  # noqa: E402

VERSION = 'public-delivery-v2'
# Positive scope works in both a Git checkout and an extracted clean source archive.
# Include runtime modules/assets, validator/test code, configuration and dependency
# locks; never enumerate the working databases, dependency trees or environment files.
SOURCE_TREES = ('backend', 'datasets', 'scripts', 'frontend/src', 'frontend/tests', 'frontend/public')
SOURCE_PATTERNS = ('pyproject.toml', 'uv.lock', 'alembic.ini', 'compose.yaml',
                   'frontend/package*.json', 'frontend/*config.*', 'frontend/tsconfig*.json',
                   'frontend/index.html', 'frontend/Dockerfile', 'frontend/nginx.conf', 'docs/*.json')
EXCLUDED_PARTS = {'.git', '.local', '.cache', '.venv', 'runtime', 'node_modules', '__pycache__',
                  'dist', '.pytest_cache', '.ruff_cache', '.hypothesis'}
BORROWING_CASES = ('范围未知', '辅助费用', '选择利息但未人工确认', '人工确认专门借款利息')
REVENUE_CASHFLOW_CASES = ('运输未到收入确认时点', '运输收入时点未知', '本期履约运输成本摊销', '长期租赁本金与短租租金混付')
ATTACHMENT_REQUIREMENTS = {
    'policy-borrowing-live.spec.ts': {
        '真实借款政策闭环：' + name: {('真实政策底稿', 'application/json')}
        | ({('利息条件修订需要重新确认', 'image/png')} if name == '人工确认专门借款利息' else set())
        for name in BORROWING_CASES},
    'policy-revenue-cashflow-live.spec.ts': {
        '真实收入与现金流边界：' + name: {('收入与现金流边界底稿', 'application/json')}
        for name in REVENUE_CASHFLOW_CASES},
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_file(path: Path, boundary: Path, label: str):
    """Require a regular file inside the boundary, with no symlink/junction traversal."""
    if '..' in path.parts or not path.is_relative_to(boundary):
        raise ValueError(f'{label} path escapes its boundary: {path}')
    for candidate in (path, *path.parents):
        if candidate == boundary:
            break
        if (candidate.is_symlink()
                or (candidate.exists() and getattr(candidate.lstat(), 'st_file_attributes', 0) & 0x400)):
            raise ValueError(f'{label} linked path is forbidden: {path}')
    if not path.is_file() or not path.resolve().is_relative_to(boundary):
        raise ValueError(f'{label} file is missing or outside its boundary: {path}')
    return path.resolve()


def source_manifest(root: Path):
    """Deterministic initial/final inventory, including untracked and newly added sources."""
    root = root.resolve()
    paths = set()

    def excluded(path):
        parts = path.relative_to(root).parts
        return (any(part in EXCLUDED_PARTS or part.startswith('.env') for part in parts)
                or '.sqlite' in path.name or path.suffix in {'.pyc', '.pyo', '.tsbuildinfo'})

    def walk(directory):
        if excluded(directory):
            return
        if (directory.is_symlink()
                or (directory.exists() and getattr(directory.lstat(), 'st_file_attributes', 0) & 0x400)):
            raise ValueError(f'Source linked directory is forbidden: {directory}')
        if not directory.exists():
            return
        for path in sorted(directory.iterdir()):
            if excluded(path):
                continue
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError(f'Source linked path is forbidden: {path}')
            if path.is_dir():
                walk(path)
            else:
                paths.add(path)

    for name in SOURCE_TREES:
        walk(root / name)
    for pattern in SOURCE_PATTERNS:
        paths.update(path for path in root.glob(pattern) if not excluded(path))
    manifest = {path.relative_to(root).as_posix(): sha(safe_file(path, root, 'Source')) for path in sorted(paths)}
    if not manifest:
        raise ValueError('Source inventory is empty')
    return manifest


def verify_sources(root, expected):
    actual = source_manifest(root)
    added, removed = sorted(actual.keys() - expected.keys()), sorted(expected.keys() - actual.keys())
    changed = sorted(name for name in actual.keys() & expected.keys() if actual[name] != expected[name])
    if added or removed or changed:
        raise ValueError(f'Source inventory changed: added={added}, removed={removed}, changed={changed}')
    return {'status': 'verified', 'files': len(actual), 'added': [], 'removed': [], 'changed': []}


def browser_attachments(browser_dir: Path, expected_stats: dict, *, document=None):
    """Parse real Playwright results and require the public download evidence contract."""
    if document is None:
        raw = (browser_dir / 'browser.json').read_text(encoding='utf-8')
        document = json.loads(raw[raw.index('{'):])
    if document.get('stats') != expected_stats or document.get('errors'):
        raise ValueError('Browser attachment report has inconsistent stats or runner errors')
    artifacts = browser_dir / 'artifacts'
    records, seen_paths, seen_cases = [], {}, set()
    tests_seen = 0

    def list_value(owner, key, default=None):
        value = owner.get(key, default)
        if not isinstance(value, list):
            raise ValueError(f'Browser attachment report must contain a {key} list')
        return value

    def visit(suite):
        nonlocal tests_seen
        for spec in list_value(suite, 'specs', []):
            for test in list_value(spec, 'tests'):
                tests_seen += 1
                if (spec.get('ok') is not True or test.get('status') != 'expected'
                        or test.get('expectedStatus') != 'passed'):
                    raise ValueError('Browser attachment report contains an unsuccessful test')
                results = list_value(test, 'results')
                if len(results) != 1 or results[0].get('status') != 'passed':
                    raise ValueError('Browser attachment report requires one passing attempt per test')
                identity = (spec.get('file'), spec.get('title'), test.get('projectId'))
                attached = set()
                for item in list_value(results[0], 'attachments'):
                    if (not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key]
                                                             for key in ('name', 'contentType', 'path'))
                            or 'body' in item):
                        raise ValueError('Browser attachment requires a named file path and content type')
                    path = Path(item['path'])
                    if not path.is_absolute():
                        path = browser_dir / path
                    path = safe_file(path, browser_dir, 'Browser attachment')
                    if not path.is_relative_to(artifacts):
                        raise ValueError('Browser attachment is outside this run artifacts directory')
                    metadata = (identity, item['name'], item['contentType'])
                    if path in seen_paths:
                        if seen_paths[path] != metadata:
                            raise ValueError('Browser attachment has conflicting duplicate references')
                        continue
                    pair = (item['name'], item['contentType'])
                    if pair in attached:
                        raise ValueError('Browser attachment name has conflicting duplicate files')
                    seen_paths[path] = metadata
                    attached.add(pair)
                    records.append({'file': path.relative_to(browser_dir).as_posix(), 'name': item['name'],
                                    'content_type': item['contentType'], 'test_file': identity[0],
                                    'test_title': identity[1], 'project_id': identity[2]})
                test_file = Path(spec.get('file', '')).name
                if test_file in ATTACHMENT_REQUIREMENTS:
                    title = spec.get('title')
                    if title not in ATTACHMENT_REQUIREMENTS[test_file] or (test_file, title) in seen_cases:
                        raise ValueError('Browser attachment contract has an unexpected or duplicate required case')
                    seen_cases.add((test_file, title))
                    required = ATTACHMENT_REQUIREMENTS[test_file][title]
                    if not required <= attached:
                        raise ValueError(f'Browser attachment evidence is missing for {title}')
        for child in list_value(suite, 'suites', []):
            visit(child)

    for suite in list_value(document, 'suites'):
        visit(suite)
    required_cases = {(name, title) for name, cases in ATTACHMENT_REQUIREMENTS.items() for title in cases}
    if seen_cases != required_cases or tests_seen != expected_stats['expected']:
        raise ValueError('Browser attachment report has missing cases or inconsistent executed-test counts')
    return sorted(records, key=lambda item: item['file'])


def require_api(report):
    checks = report.get('checks', [])
    if (report.get('schema_version') != 'public-voucher-loop-v1' or report.get('status') != 'passed'
            or len(checks) < 45 or any(check.get('passed') is not True for check in checks)
            or len({check.get('name') for check in checks}) != len(checks)
            or report.get('model_requests') != 0 or report.get('network_or_model_attempts') != []):
        raise ValueError('API evidence missing, incomplete or failed')


def run_api(bundle, output):
    # The existing API harness creates a fresh database in its own child process.
    with (output.parent / 'api-command.log').open('x', encoding='utf-8') as stream:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/validate_public_voucher_loop.py'),
                                 '--bundle', str(bundle), '--output', str(output)],
                                cwd=output.parent, env=isolated_environment(), stdout=stream,
                                stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError(f'API workflow exited with code {result.returncode}; inspect api-command.log')


def validate(output: Path, archive: Path | None = None):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': VERSION, 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'mode': 'offline_archive' if archive else 'pinned_official_download',
              'stages': [], 'evidence': [], 'model_requests': 0,
              'limitations': ['Installed Python dependencies, Node dependencies and Chrome are required.',
                              'Public illustrative XML plus explicitly synthetic faults, not enterprise data.',
                              'Automated teaching review, not professional human signoff or model-quality evaluation.',
                              'Current working sources; not a clean-machine installation or a published release.']}

    def save():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def evidence(path):
        # Only purpose-built public/teaching artifacts; no .env or pre-existing working database.
        path = safe_file(path, output, 'Evidence')
        item = {'file': path.relative_to(output).as_posix(), 'sha256': sha(path), 'bytes': path.stat().st_size}
        previous = next((value for value in report['evidence'] if value['file'] == item['file']), None)
        if previous is not None and previous != item:
            raise ValueError('Evidence has conflicting duplicate references')
        if previous is None:
            report['evidence'].append(item)

    def stage(name):
        record = {'name': name, 'status': 'running'}
        report['stages'].append(record)
        save()
        return record

    current = None
    try:
        report['source_selection'] = {'trees': SOURCE_TREES, 'patterns': SOURCE_PATTERNS,
                                      'excluded_parts': sorted(EXCLUDED_PARTS),
                                      'excluded_files': ['.env*', '*.sqlite*', '*.pyc', '*.pyo', '*.tsbuildinfo']}
        report['implementation_sha256'] = source_manifest(ROOT)
        current = stage('collect_and_verify_sources')
        bundle = output / 'sources'
        manifest = collect(bundle, archive.resolve() if archive else None)
        if manifest.get('status') != 'complete':
            raise ValueError('Collection did not complete')
        for filename in SAMPLES:
            path = bundle / (filename + '.json')
            verify_package(json.loads(path.read_text(encoding='utf-8')))
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                evidence(path)
        current.update(status='passed', sample_count=len(SAMPLES))
        current = stage('isolated_api_workflow')
        run_api(bundle, output / 'api')
        api = json.loads((output / 'api/report.json').read_text(encoding='utf-8'))
        require_api(api)
        evidence(output / 'api/report.json')
        for item in api['artifacts']:
            path = (output / 'api' / item['file']).resolve()
            if not path.is_relative_to(output / 'api') or sha(path) != item['sha256']:
                raise ValueError('API artifact path or digest mismatch')
            evidence(path)
        current.update(status='passed', checks=len(api['checks']))
        current = stage('isolated_browser_workflow')
        code = validate_browser(output / 'browser', bundle)
        browser = json.loads((output / 'browser/report.json').read_text(encoding='utf-8'))
        if code or browser.get('status') != 'passed' or not browser_passed(browser.get('exit_code'), browser.get('stats', {})):
            raise ValueError('Browser workflow failed, skipped checks, or produced insufficient evidence')
        evidence(output / 'browser/report.json')
        evidence(output / 'browser/browser.json')
        report['browser_attachments'] = browser_attachments(output / 'browser', browser['stats'])
        for item in report['browser_attachments']:
            evidence(output / 'browser' / item['file'])
        current.update(status='passed', stats=browser['stats'])
        current = stage('verify_source_and_evidence_integrity')
        report['source_integrity'] = verify_sources(ROOT, report['implementation_sha256'])
        # Re-read every selected artifact after both workflows, so stale checksums cannot pass.
        for item in report['evidence']:
            path = safe_file(output / item['file'], output, 'Evidence')
            if sha(path) != item['sha256'] or path.stat().st_size != item['bytes']:
                raise ValueError('Evidence changed during delivery validation')
        current.update(status='passed', sources=len(report['implementation_sha256']), evidence=len(report['evidence']))
        report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        report['status'], report['error'] = 'failed', repr(exc)
        if current is not None:
            current['status'] = 'failed'
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({'status': report['status'], 'report': str(output / 'report.json'),
                      'stages': report['stages']}, ensure_ascii=False))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', type=Path, help='Existing pinned MOF ZIP; no download when supplied')
    args = parser.parse_args()
    raise SystemExit(validate(args.output, args.archive))
