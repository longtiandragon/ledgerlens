"""Read-only offline verification of a saved public-delivery-v2 evidence folder.

Only Python's standard library is needed. This does not run the application,
browser, accounting rules or a model. A separately trusted report digest can
anchor the manifest; without it the result establishes internal consistency only.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from datasets.mof_vouchers import PAGE, URL, VERSION, ZIP_SHA256, SAMPLES, verify_package  # noqa: E402
from scripts.validate_public_delivery import (  # noqa: E402
    browser_attachments, browser_passed, require_api, safe_file, sha, verify_sources,
)

DIGEST = re.compile(r'[0-9a-f]{64}')
STAGES = ('collect_and_verify_sources', 'isolated_api_workflow', 'isolated_browser_workflow',
          'verify_source_and_evidence_integrity')
BASE_EVIDENCE = {'sources/manifest.json', 'sources/source.zip', 'api/report.json',
                 'api/training-input.json', 'api/workpaper.json', 'api/workpaper.md', 'api/historical-workpaper.json',
                 'browser/report.json', 'browser/browser.json'} | {
                     'sources/' + name + suffix for name in SAMPLES for suffix in ('', '.json')}
API_ARTIFACTS = {'training-input.json', 'workpaper.json', 'workpaper.md', 'historical-workpaper.json'}


def relative_file(root, name):
    # Manifest paths are portable POSIX paths. Reject Windows drives/ADS,
    # traversal and aliases before touching the filesystem on either OS.
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Invalid relative evidence path')
    return safe_file(root / name, root, 'Offline evidence')


def read_json(path, *, playwright=False):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('JSON evidence exceeds 32 MiB')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    raw = path.read_text(encoding='utf-8')
    if playwright:
        raw = raw[raw.index('{'):]
    def finite_only(value):
        raise ValueError('Non-finite JSON number: ' + value)

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            finite_only(value)
        return result
    return json.loads(raw, object_pairs_hook=unique, parse_constant=finite_only, parse_float=finite_float)


def relocated_browser(document, browser_dir):
    """Translate declared original paths lexically; never read the old directory."""
    document = deepcopy(document)
    projects = {}
    for project in document['config']['projects']:
        identity, origin = project['id'], project['outputDir']
        cls = PureWindowsPath if PureWindowsPath(origin).drive else PurePosixPath
        path = cls(origin)
        if (identity in projects or not path.is_absolute() or path.name != 'artifacts'
                or path.parent.name != 'browser' or '..' in origin.replace('\\', '/').split('/')):
            raise ValueError('Invalid original browser artifacts directory')
        projects[identity] = path

    def visit(suite):
        for spec in suite.get('specs', []):
            for test in spec.get('tests', []):
                origin = projects[test['projectId']]
                for result in test.get('results', []):
                    for attachment in result.get('attachments', []):
                        original = attachment['path']
                        if '..' in original.replace('\\', '/').split('/'):
                            raise ValueError('Browser attachment traversal')
                        path = type(origin)(original)
                        if path.is_absolute():
                            relative = path.relative_to(origin).as_posix()
                        elif path.drive or path.root:
                            raise ValueError('Ambiguous browser attachment path')
                        else:
                            relative = path.relative_to('artifacts').as_posix()
                        target = relative_file(browser_dir, 'artifacts/' + relative)
                        attachment['path'] = str(target)
        for child in suite.get('suites', []):
            visit(child)
    for suite in document['suites']:
        visit(suite)
    return document


def verify_public_sources(bundle):
    manifest = read_json(relative_file(bundle, 'manifest.json'))
    if (manifest.get('schema_version') != VERSION or manifest.get('status') != 'complete'
            or manifest.get('page_url') != PAGE or manifest.get('archive_url') != URL
            or manifest.get('archive_sha256') != ZIP_SHA256
            or sha(relative_file(bundle, 'source.zip')) != ZIP_SHA256):
        raise ValueError('Pinned public archive or source manifest mismatch')
    samples = manifest.get('samples', [])
    if len(samples) != len(SAMPLES) or {s['file'] for s in samples} != set(SAMPLES):
        raise ValueError('Source manifest sample inventory mismatch')
    for filename, digest in SAMPLES.items():
        raw = relative_file(bundle, filename)
        package = read_json(relative_file(bundle, filename + '.json'))
        verify_package(package)
        if sha(raw) != digest or raw.read_text(encoding='utf-8') != package['source']['original_xml_utf8']:
            raise ValueError('Pinned public XML mismatch')
        sample = next(s for s in samples if s['file'] == filename)
        if (sample.get('sha256') != digest or sample.get('projection') != filename + '.json'
                or sample.get('records') != len(package['records']) or sample.get('warnings') != package['warnings']):
            raise ValueError('Source projection manifest mismatch')
    return len(samples)


def verify(folder, *, expected_report_sha256=None, source_root=None):
    folder = Path(folder).resolve()
    report_path = relative_file(folder, 'report.json')
    digest = sha(report_path)
    if expected_report_sha256 is not None:
        if not DIGEST.fullmatch(expected_report_sha256) or digest != expected_report_sha256:
            raise ValueError('Report digest differs from the supplied external anchor')
    report = read_json(report_path)
    if (report.get('schema_version') != 'public-delivery-v2' or report.get('status') != 'passed'
            or type(report.get('model_requests')) is not int or report['model_requests'] != 0):
        raise ValueError('Unsupported or unsuccessful delivery report')
    stages = report['stages']
    if ([s['name'] for s in stages] != list(STAGES) or any(s['status'] != 'passed' for s in stages)):
        raise ValueError('Delivery stages missing, duplicated or unsuccessful')
    evidence, resolved_paths = {}, set()
    for item in report['evidence']:
        name = item['file']
        if name not in BASE_EVIDENCE and not name.startswith('browser/artifacts/'):
            raise ValueError('Unexpected evidence path outside the public artifact contract')
        path = relative_file(folder, name)
        if (name in evidence or path in resolved_paths or not DIGEST.fullmatch(item['sha256'])
                or type(item['bytes']) is not int or item['bytes'] < 0
                or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']):
            raise ValueError('Evidence inventory, size or digest mismatch')
        evidence[name] = item
        resolved_paths.add(path)
    if not BASE_EVIDENCE <= evidence.keys():
        raise ValueError('Required evidence missing from manifest')
    samples = verify_public_sources(folder / 'sources')
    api = read_json(relative_file(folder, 'api/report.json'))
    require_api(api)
    if type(api['model_requests']) is not int or any(not isinstance(c['name'], str) or not c['name'] for c in api['checks']):
        raise ValueError('Invalid API count or check names')
    if len(api['artifacts']) != len(API_ARTIFACTS) or {item['file'] for item in api['artifacts']} != API_ARTIFACTS:
        raise ValueError('API artifact inventory is incomplete or duplicated')
    for item in api['artifacts']:
        name = 'api/' + item['file']
        relative_file(folder, name)
        if name not in evidence or evidence[name]['sha256'] != item['sha256']:
            raise ValueError('API artifact missing or inconsistent')
    browser = read_json(relative_file(folder, 'browser/report.json'))
    if (browser.get('schema_version') != 'public-voucher-browser-v1' or browser.get('status') != 'passed'
            or type(browser.get('model_requests')) is not int or browser['model_requests'] != 0
            or type(browser.get('exit_code')) is not int
            or not browser_passed(browser['exit_code'], browser.get('stats', {}))):
        raise ValueError('Unsuccessful browser evidence')
    document = relocated_browser(read_json(relative_file(folder, 'browser/browser.json'), playwright=True), folder / 'browser')
    attachments = browser_attachments(folder / 'browser', browser['stats'], document=document)
    if attachments != report['browser_attachments']:
        raise ValueError('Browser attachment inventory differs from execution report')
    if BASE_EVIDENCE | {'browser/' + item['file'] for item in attachments} != evidence.keys():
        raise ValueError('Browser attachment missing from hashed evidence or unexpected artifact')
    sources = report['implementation_sha256']
    if not sources or any(not DIGEST.fullmatch(value) for value in sources.values()):
        raise ValueError('Invalid source inventory')
    if (report['source_integrity'] != {'status': 'verified', 'files': len(sources), 'added': [], 'removed': [], 'changed': []}
            or stages[0].get('sample_count') != samples or stages[1].get('checks') != len(api['checks'])
            or stages[2].get('stats') != browser['stats']
            or stages[3].get('sources') != len(sources) or stages[3].get('evidence') != len(evidence)):
        raise ValueError('Cross-report stage totals or integrity result mismatch')
    source_check = verify_sources(Path(source_root), sources) if source_root is not None else {'status': 'not_checked'}
    return {
        'schema_version': 'offline-public-delivery-verification-v1', 'status': 'verified',
        'report_sha256': digest, 'external_anchor_supplied': expected_report_sha256 is not None,
        'source_projections_verified': samples, 'evidence_files_verified': len(evidence),
        'browser_attachments_verified': len(attachments), 'required_policy_cases_verified': 8,
        'reported_api_checks': len(api['checks']), 'reported_browser_passed': browser['stats']['expected'],
        'source_check': source_check, 'execution_repeated': False,
        'limitations': [
            'Hashes alone prove internal consistency, not authorship or that reported tests actually executed.',
            'An external anchor is trusted only as much as the channel that supplied it.',
            'API names and non-policy browser cases are counted, not independently certified as a full test inventory.',
            'No application, browser, accounting rule or model was executed; no business or professional opinion is established.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--expected-report-sha256')
    parser.add_argument('--source-root', type=Path, help='Optional exact source snapshot; additions also fail')
    args = parser.parse_args()
    try:
        result = verify(args.folder, expected_report_sha256=args.expected_report_sha256, source_root=args.source_root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result = {'schema_version': 'offline-public-delivery-verification-v1', 'status': 'failed', 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
