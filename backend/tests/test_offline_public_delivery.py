"""Offline report checks; fixture sources are mocked, not authenticity evidence.

Real pinned source verification is exercised by the explicit isolated acceptance
run. These tests run with no app/database/process and hand-built report mutations.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from test_public_delivery import simulated_delivery  # noqa: F401
from scripts import verify_public_delivery as offline


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def refresh(folder, report):
    for item in report['evidence']:
        path = folder / item['file']
        item.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)
    report['stages'][-1]['evidence'] = len(report['evidence'])
    write_json(folder / 'report.json', report)


@pytest.fixture
def saved_bundle(simulated_delivery, monkeypatch, tmp_path):  # noqa: F811
    code, report, folder = simulated_delivery['run']()
    assert code == 0
    # The orchestration fixture uses one mocked example. Expand the evidence
    # shape here; never claim these dummy bytes pass the pinned-source verifier.
    report['evidence'] = [i for i in report['evidence'] if not i['file'].startswith('sources/')]
    known = {i['file'] for i in report['evidence']}
    for name in offline.BASE_EVIDENCE - known:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
        report['evidence'].append({'file': name})
    report['stages'][0]['sample_count'] = 2
    api = json.loads((folder / 'api/report.json').read_text(encoding='utf-8'))
    api['artifacts'] = [{'file': name, 'sha256': offline.sha(folder / 'api' / name)}
                        for name in ('training-input.json', 'workpaper.json', 'workpaper.md', 'historical-workpaper.json')]
    write_json(folder / 'api/report.json', api)
    browser_summary = json.loads((folder / 'browser/report.json').read_text(encoding='utf-8'))
    browser_summary.update(schema_version='public-voucher-browser-v1', model_requests=0)
    write_json(folder / 'browser/report.json', browser_summary)
    browser = json.loads((folder / 'browser/browser.json').read_text(encoding='utf-8'))
    browser['config'] = {'projects': [{'id': '', 'outputDir': (folder / 'browser/artifacts').as_posix()}]}
    write_json(folder / 'browser/browser.json', browser)
    refresh(folder, report)
    monkeypatch.setattr(offline, 'verify_public_sources', lambda _: 2)
    target = tmp_path / 'copied-evidence'
    # Only public evidence, not runtime databases or logs, is needed.
    target.mkdir()
    for name in ['report.json', *[i['file'] for i in report['evidence']]]:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / name, destination)
    return folder, target, report, simulated_delivery['root']


def test_moved_copy_reads_no_original_files(saved_bundle, monkeypatch):
    original, target, report, source = saved_bundle
    old_digest = offline.sha(target / 'report.json')
    real = Path.open

    def no_original(path, *args, **kwargs):
        assert not path.is_relative_to(original), 'Must never open the original artifact directory'
        return real(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_original)
    result = offline.verify(target, expected_report_sha256=old_digest, source_root=source)
    assert result['status'] == 'verified' and result['execution_repeated'] is False
    assert result['external_anchor_supplied'] is True
    assert result['source_check']['status'] == 'verified'
    assert result['browser_attachments_verified'] == 9


def test_copy_does_not_fall_back_to_correct_original_attachment(saved_bundle):
    original, target, report, _ = saved_bundle
    name = 'browser/' + report['browser_attachments'][0]['file']
    (target / name).write_text('corrupt copy', encoding='utf-8')
    assert (original / name).read_bytes() != (target / name).read_bytes()
    with pytest.raises(ValueError, match='digest mismatch'):
        offline.verify(target)


def test_external_digest_detects_coordinated_rewrite(saved_bundle):
    _, target, report, _ = saved_bundle
    pinned = offline.sha(target / 'report.json')
    (target / 'api/workpaper.md').write_text('changed together with manifest', encoding='utf-8')
    api_path = target / 'api/report.json'
    api = json.loads(api_path.read_text(encoding='utf-8'))
    for item in api['artifacts']:
        if item['file'] == 'workpaper.md':
            item['sha256'] = offline.sha(target / 'api/workpaper.md')
    write_json(api_path, api)
    refresh(target, report)
    assert offline.verify(target)['external_anchor_supplied'] is False
    assert offline.verify(target)['source_check']['status'] == 'not_checked'
    with pytest.raises(ValueError, match='external anchor'):
        offline.verify(target, expected_report_sha256=pinned)


@pytest.mark.parametrize('path', ['../outside', 'C:/outside', 'C:outside', '/outside', '\\\\server\\share',
                                  'api/../report.json', 'api\\report.json', 'api/report.json:stream', './report.json'])
def test_untrusted_paths_rejected_before_file_access(tmp_path, path):
    with pytest.raises(ValueError, match='relative evidence'):
        offline.relative_file(tmp_path, path)


@pytest.mark.parametrize('defect', ['stage', 'duplicate', 'missing', 'unhashed_attachment', 'foreign_origin',
                                  'missing_case', 'true_bytes', 'true_model_calls', 'unexpected_runtime'])
def test_report_mutations_fail_closed(saved_bundle, defect):
    _, target, report, _ = saved_bundle
    if defect == 'stage':
        report['stages'][0]['status'] = 'running'
    elif defect == 'duplicate':
        report['evidence'].append(deepcopy(report['evidence'][0]))
    elif defect == 'missing':
        report['evidence'] = [i for i in report['evidence'] if i['file'] != 'api/workpaper.json']
    elif defect == 'unhashed_attachment':
        name = 'browser/' + report['browser_attachments'][0]['file']
        report['evidence'] = [i for i in report['evidence'] if i['file'] != name]
    elif defect in ('foreign_origin', 'missing_case'):
        browser = json.loads((target / 'browser/browser.json').read_text(encoding='utf-8'))
        if defect == 'foreign_origin':
            browser['config']['projects'][0]['outputDir'] = 'Z:/foreign/browser/artifacts'
        else:
            browser['suites'][0]['suites'][0]['specs'][0]['title'] = 'unrelated passing test'
        write_json(target / 'browser/browser.json', browser)
    elif defect == 'unexpected_runtime':
        report['evidence'].append({'file': 'api/runtime/secret.sqlite', 'bytes': 0, 'sha256': '0' * 64})
        write_json(target / 'report.json', report)
        with pytest.raises(ValueError, match='Unexpected evidence'):
            offline.verify(target)
        return
    refresh(target, report)
    if defect == 'true_bytes':
        report['evidence'][0]['bytes'] = True
    elif defect == 'true_model_calls':
        report['model_requests'] = False
    write_json(target / 'report.json', report)
    with pytest.raises(ValueError):
        offline.verify(target)


@pytest.mark.parametrize('text', ['{"status":"passed","status":"failed"}', '{"number":NaN}',
                                '{"number":Infinity}', '{"number":1e999}'])
def test_ambiguous_json_rejected(tmp_path, text):
    path = tmp_path / 'report.json'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        offline.read_json(path)


def test_pinned_source_is_not_trusted_from_manifest_alone(tmp_path):
    manifest = {'schema_version': offline.VERSION, 'status': 'complete', 'page_url': offline.PAGE,
                'archive_url': offline.URL, 'archive_sha256': offline.ZIP_SHA256}
    write_json(tmp_path / 'manifest.json', manifest)
    (tmp_path / 'source.zip').write_bytes(b'fake archive despite self-declared official digest')
    with pytest.raises(ValueError, match='Pinned public archive'):
        offline.verify_public_sources(tmp_path)


@pytest.mark.parametrize('defect', ['browser_calls', 'browser_schema', 'browser_exit_bool', 'api_empty',
                                  'api_missing', 'api_duplicate'])
def test_subreport_contracts_cannot_disagree_with_root(saved_bundle, defect):
    _, target, report, _ = saved_bundle
    path = target / ('browser/report.json' if defect.startswith('browser') else 'api/report.json')
    child = json.loads(path.read_text(encoding='utf-8'))
    if defect == 'browser_calls':
        child['model_requests'] = 1
    elif defect == 'browser_schema':
        child['schema_version'] = 'unknown'
    elif defect == 'browser_exit_bool':
        child['exit_code'] = False
    elif defect == 'api_empty':
        child['artifacts'] = []
    elif defect == 'api_missing':
        child['artifacts'].pop()
    else:
        child['artifacts'].append(deepcopy(child['artifacts'][0]))
    write_json(path, child)
    refresh(target, report)
    with pytest.raises(ValueError):
        offline.verify(target)
