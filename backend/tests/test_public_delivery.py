"""Acceptance gates must reject missing, skipped, failed or altered evidence."""
from copy import deepcopy
import json

import pytest

from scripts import validate_public_delivery as delivery
from scripts import validate_public_voucher_browser as browser


def valid_api():
    return {'schema_version': 'public-voucher-loop-v1', 'status': 'passed',
            'checks': [{'name': f'check-{index}', 'passed': True} for index in range(45)],
            'model_requests': 0, 'network_or_model_attempts': []}


def test_api_complete_evidence_gate():
    delivery.require_api(valid_api())


@pytest.mark.parametrize('defect', ['missing', 'short', 'duplicate', 'false', 'truthy', 'model', 'network', 'status'])
def test_api_gate_rejects_incomplete_or_false_success(defect):
    report = valid_api()
    if defect == 'missing':
        del report['checks']
    elif defect == 'short':
        report['checks'].pop()
    elif defect == 'duplicate':
        report['checks'][-1] = deepcopy(report['checks'][0])
    elif defect in ('false', 'truthy'):
        report['checks'][0]['passed'] = False if defect == 'false' else 'true'
    elif defect == 'model':
        report['model_requests'] = 1
    elif defect == 'network':
        report['network_or_model_attempts'] = ['external_network']
    else:
        report['status'] = 'running'
    with pytest.raises(ValueError, match='evidence'):
        delivery.require_api(report)


@pytest.mark.parametrize('exit_code,stats', [
    (1, {'expected': 16, 'skipped': 0, 'unexpected': 0, 'flaky': 0}),
    (0, {}),
    (0, {'expected': 0, 'skipped': 0, 'unexpected': 0, 'flaky': 0}),
    (0, {'expected': 15, 'skipped': 0, 'unexpected': 0, 'flaky': 0}),
    (0, {'expected': 16, 'skipped': 1, 'unexpected': 0, 'flaky': 0}),
    (0, {'expected': 16, 'skipped': 0, 'unexpected': 1, 'flaky': 0}),
    (0, {'expected': 16, 'skipped': 0, 'unexpected': 0, 'flaky': 1}),
    (0, {'expected': '16', 'skipped': 0, 'unexpected': 0, 'flaky': 0}),
])
def test_browser_gate_rejects_empty_skipped_flaky_and_failed(exit_code, stats):
    assert browser.browser_passed(exit_code, stats) is False


def test_browser_gate_accepts_complete_run():
    assert browser.browser_passed(0, {'expected': 24, 'skipped': 0, 'unexpected': 0, 'flaky': 0}) is True
    for count in (19, 20, 23):
        assert browser.browser_passed(0, {'expected': count, 'skipped': 0, 'unexpected': 0, 'flaky': 0}) is False


def test_failed_collection_keeps_failed_manifest_and_does_not_start_api(tmp_path, monkeypatch):
    archive = tmp_path / 'invalid.zip'
    archive.write_bytes(b'not the pinned official archive')
    monkeypatch.setattr(delivery, 'run_api', lambda *args: pytest.fail('API must not start'))
    output = tmp_path / 'output'
    assert delivery.validate(output, archive) == 1
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert report['stages'] == [{'name': 'collect_and_verify_sources', 'status': 'failed'}]
    manifest = json.loads((output / 'sources/manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'failed'


def test_existing_output_is_preserved(tmp_path):
    target = tmp_path / 'prior'
    target.mkdir()
    (target / 'report.json').write_text('original report', encoding='utf-8')
    with pytest.raises(FileExistsError):
        delivery.validate(target)
    assert (target / 'report.json').read_text(encoding='utf-8') == 'original report'


def test_browser_missing_source_fails_before_starting_any_process(tmp_path, monkeypatch):
    monkeypatch.setattr(browser.subprocess, 'Popen', lambda *args, **kw: pytest.fail('Service must not start'))
    output = tmp_path / 'browser'
    assert browser.validate(output, tmp_path / 'missing') == 1
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert 'FileNotFoundError' in report['error']


@pytest.fixture
def simulated_delivery(tmp_path, monkeypatch):
    """Exercise the real delivery orchestration without processes, network or app imports."""
    root = tmp_path / 'checkout'
    original_files = (
        'scripts/validate_public_delivery.py', 'datasets/mof_vouchers.py',
        'scripts/validate_public_voucher_loop.py', 'scripts/validate_public_voucher_browser.py',
        'backend/app/domain.py', 'backend/app/policy_consistency.py', 'backend/app/policy_contract.py',
        'backend/app/imports.py', 'backend/app/main.py', 'frontend/src/App.vue',
        'frontend/tests/public-vouchers.spec.ts', 'frontend/tests/policy-checks.spec.ts',
        'frontend/tests/policy-borrowing-live.spec.ts', 'frontend/tests/workbench.spec.ts',
        'frontend/src/components/PolicyChecks.vue', 'frontend/vite.config.ts',
        'frontend/package-lock.json', 'frontend/package.json', 'pyproject.toml', 'uv.lock',
    )
    for name in original_files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original ' + name, encoding='utf-8')
    monkeypatch.setattr(delivery, 'ROOT', root)
    monkeypatch.setattr(delivery, 'SAMPLES', ('sample',))
    monkeypatch.setattr(delivery, 'verify_package', lambda value: None)

    def collect(bundle, archive):
        bundle.mkdir()
        (bundle / 'sample.json').write_text('{}', encoding='utf-8')
        return {'status': 'complete'}

    def api(bundle, output):
        output.mkdir()
        (output / 'report.json').write_text(
            json.dumps(valid_api() | {'artifacts': []}), encoding='utf-8')

    stats = {'expected': 24, 'skipped': 0, 'unexpected': 0, 'flaky': 0}
    state = {'root': root, 'change': lambda output, report: None}

    def browser_workflow(output, bundle):
        output.mkdir()
        specs = []
        for index, name in enumerate(('范围未知', '辅助费用', '选择利息但未人工确认', '人工确认专门借款利息')):
            target = output / 'artifacts' / f'case-{index}' / 'download.json'
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({'case': name}), encoding='utf-8')
            attachments = [{'name': '真实政策底稿', 'contentType': 'application/json', 'path': str(target)}]
            if index == 3:
                image = target.with_name('mobile.png')
                image.write_bytes(b'\x89PNG\r\n\x1a\n')
                attachments.append({'name': '利息条件修订需要重新确认', 'contentType': 'image/png', 'path': str(image)})
            specs.append({'title': '真实借款政策闭环：' + name, 'file': 'policy-borrowing-live.spec.ts', 'ok': True,
                          'tests': [{'projectId': '', 'status': 'expected', 'expectedStatus': 'passed',
                                     'results': [{'status': 'passed', 'attachments': attachments}]}]})
        revenue_specs = []
        for index, name in enumerate(('运输未到收入确认时点', '运输收入时点未知', '本期履约运输成本摊销', '长期租赁本金与短租租金混付')):
            target = output / 'artifacts' / f'revenue-{index}' / 'download.json'
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({'case': name}), encoding='utf-8')
            revenue_specs.append({'title': '真实收入与现金流边界：' + name,
                                  'file': 'policy-revenue-cashflow-live.spec.ts', 'ok': True,
                                  'tests': [{'projectId': '', 'status': 'expected', 'expectedStatus': 'passed',
                                             'results': [{'status': 'passed', 'attachments': [{
                                                 'name': '收入与现金流边界底稿', 'contentType': 'application/json',
                                                 'path': str(target)}]}]}]})
        other_specs = [{'title': f'other-{index}', 'file': 'public-vouchers.spec.ts', 'ok': True,
                        'tests': [{'projectId': '', 'status': 'expected', 'expectedStatus': 'passed',
                                   'results': [{'status': 'passed', 'attachments': []}]}]} for index in range(16)]
        # Nested suites prevent accidentally collecting only top-level specs.
        raw = {'suites': [{'suites': [{'specs': specs}, {'specs': revenue_specs}], 'specs': other_specs}],
               'stats': stats, 'errors': []}
        state['change'](output, raw)
        (output / 'browser.json').write_text(json.dumps(raw), encoding='utf-8')
        (output / 'report.json').write_text(json.dumps({
            'status': 'passed', 'exit_code': 0, 'stats': stats}), encoding='utf-8')
        return 0

    monkeypatch.setattr(delivery, 'collect', collect)
    monkeypatch.setattr(delivery, 'run_api', api)
    monkeypatch.setattr(delivery, 'validate_browser', browser_workflow)

    def run():
        output = tmp_path / 'delivery'
        code = delivery.validate(output)
        return code, json.loads((output / 'report.json').read_text(encoding='utf-8')), output

    state['run'] = run
    return state


@pytest.mark.parametrize('name', [
    'frontend/src/components/PolicyChecks.vue', 'backend/app/main.py', 'frontend/vite.config.ts',
    'frontend/package-lock.json', 'pyproject.toml', 'uv.lock',
])
def test_delivery_rejects_runtime_source_changes(simulated_delivery, name):
    def change(output, report):
        (simulated_delivery['root'] / name).write_text('changed during run', encoding='utf-8')
    simulated_delivery['change'] = change
    code, report, _ = simulated_delivery['run']()
    assert code == 1
    assert report['status'] == 'failed'
    assert 'Source' in report['error']


@pytest.mark.parametrize('operation', ['add', 'delete'])
def test_delivery_rejects_source_inventory_changes(simulated_delivery, operation):
    def change(output, report):
        root = simulated_delivery['root']
        if operation == 'add':
            (root / 'frontend/src/added.ts').write_text('export const injected = true', encoding='utf-8')
        else:
            (root / 'frontend/src/components/PolicyChecks.vue').unlink()
    simulated_delivery['change'] = change
    code, report, _ = simulated_delivery['run']()
    assert code == 1
    assert 'Source' in report['error']


def test_delivery_records_complete_sources_and_downloaded_attachments(simulated_delivery):
    code, report, output = simulated_delivery['run']()
    assert code == 0
    assert report['source_integrity']['status'] == 'verified'
    assert 'frontend/src/components/PolicyChecks.vue' in report['implementation_sha256']
    assert 'frontend/package-lock.json' in report['implementation_sha256']
    attachment_files = {path.relative_to(output).as_posix() for path in (output / 'browser/artifacts').rglob('*')
                        if path.is_file()}
    recorded = {item['file']: item for item in report['evidence']}
    assert len(attachment_files) == 9
    assert attachment_files <= recorded.keys()
    for name in attachment_files:
        assert recorded[name]['sha256'] == delivery.sha(output / name)


@pytest.mark.parametrize('defect', ['missing_file', 'outside', 'omitted', 'empty', 'duplicate_conflict'])
def test_delivery_rejects_invalid_or_missing_browser_attachments(simulated_delivery, defect):
    def change(output, raw):
        result = raw['suites'][0]['suites'][0]['specs'][0]['tests'][0]['results'][0]
        attachment = result['attachments'][0]
        if defect == 'missing_file':
            (output / 'artifacts/case-0/download.json').unlink()
        elif defect == 'outside':
            outside = output.parent / 'outside.json'
            outside.write_text('{}', encoding='utf-8')
            attachment['path'] = str(outside)
        elif defect == 'omitted':
            result['attachments'] = []
        elif defect == 'empty':
            attachment.pop('path')
        else:
            result['attachments'].append(attachment | {'contentType': 'text/plain'})
    simulated_delivery['change'] = change
    code, report, output = simulated_delivery['run']()
    assert code == 1
    assert report['stages'][-1]['status'] == 'failed'
    assert 'attachment' in report['error'].lower()
    assert (output / 'api/report.json').is_file()


def test_delivery_ignores_environment_dependencies_and_generated_runtime(simulated_delivery):
    root = simulated_delivery['root']
    ignored = ('.env', 'frontend/.env.local', '.local/working.py', 'frontend/node_modules/package/index.js',
               'backend/__pycache__/main.pyc', 'backend/runtime/app.sqlite', 'backend/app/.env',
               'frontend/src/.venv/ignored.py', 'frontend/src/cache.tsbuildinfo')
    for name in ignored:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('private or generated data', encoding='utf-8')

    def change(output, raw):
        for name in ignored:
            (root / name).write_text('generated during run', encoding='utf-8')
    simulated_delivery['change'] = change
    code, report, _ = simulated_delivery['run']()
    assert code == 0
    assert not set(ignored) & report['implementation_sha256'].keys()


def test_delivery_collects_relative_and_new_test_attachments(simulated_delivery):
    def change(output, raw):
        first = raw['suites'][0]['suites'][0]['specs'][0]['tests'][0]['results'][0]['attachments'][0]
        first['path'] = 'artifacts/case-0/download.json'
        extra = output / 'artifacts' / 'new-policy.json'
        extra.write_text('{"policy": "another public case"}', encoding='utf-8')
        other = raw['suites'][0]['specs'][0]
        other['file'] = 'new-live-policy.spec.ts'
        other['tests'][0]['results'][0]['attachments'] = [
            {'name': 'additional workpaper', 'contentType': 'application/json', 'path': str(extra)}]
    simulated_delivery['change'] = change
    code, report, _ = simulated_delivery['run']()
    assert code == 0
    assert len(report['browser_attachments']) == 10
    assert 'browser/artifacts/new-policy.json' in {item['file'] for item in report['evidence']}


def test_delivery_deduplicates_identical_attachment_references(simulated_delivery):
    def change(output, raw):
        attachments = raw['suites'][0]['suites'][0]['specs'][0]['tests'][0]['results'][0]['attachments']
        attachments.append(deepcopy(attachments[0]))
    simulated_delivery['change'] = change
    code, report, _ = simulated_delivery['run']()
    assert code == 0
    assert len(report['browser_attachments']) == 9
    names = [item['file'] for item in report['evidence']]
    assert len(names) == len(set(names))


@pytest.mark.parametrize('defect', ['traversal', 'report_file', 'two_paths_one_name', 'cross_case_reuse',
                                  'missing_case', 'missing_screenshot', 'missing_attempt', 'failed_attempt'])
def test_delivery_rejects_conflicting_or_incomplete_attachment_reports(simulated_delivery, defect):
    def change(output, raw):
        specs = raw['suites'][0]['suites'][0]['specs']
        first = specs[0]['tests'][0]['results'][0]
        attachment = first['attachments'][0]
        if defect == 'traversal':
            attachment['path'] = 'artifacts/case-0/../case-0/download.json'
        elif defect == 'report_file':
            attachment['path'] = str(output.parent / 'api/report.json')
        elif defect == 'two_paths_one_name':
            alternate = output / 'artifacts/alternate.json'
            alternate.write_text('{}', encoding='utf-8')
            first['attachments'].append(attachment | {'path': str(alternate)})
        elif defect == 'cross_case_reuse':
            specs[1]['tests'][0]['results'][0]['attachments'] = [deepcopy(attachment)]
        elif defect == 'missing_case':
            specs.pop()
        elif defect == 'missing_screenshot':
            specs[-1]['tests'][0]['results'][0]['attachments'].pop()
        elif defect == 'missing_attempt':
            specs[0]['tests'][0]['results'] = []
        else:
            first['status'] = 'failed'
    simulated_delivery['change'] = change
    code, report, _ = simulated_delivery['run']()
    assert code == 1
    assert 'attachment' in report['error'].lower()


def test_delivery_rechecks_attachment_bytes_after_source_verification(simulated_delivery, monkeypatch):
    verify = delivery.verify_sources

    def change_after_recording(root, expected):
        result = verify(root, expected)
        target = root.parent / 'delivery/browser/artifacts/case-0/download.json'
        target.write_text('changed after attachment was hashed', encoding='utf-8')
        return result
    monkeypatch.setattr(delivery, 'verify_sources', change_after_recording)
    code, report, _ = simulated_delivery['run']()
    assert code == 1
    assert 'Evidence changed' in report['error']
    assert report['stages'][-1]['name'] == 'verify_source_and_evidence_integrity'
    assert report['stages'][-1]['status'] == 'failed'


def test_source_manifest_rejects_reparse_points(simulated_delivery, monkeypatch):
    target = simulated_delivery['root'] / 'frontend/src/components/PolicyChecks.vue'
    original = type(target).lstat

    class Reparse:
        st_file_attributes = 0x400
        st_mode = original(target).st_mode

    def reparse_stat(path):
        return Reparse() if path == target else original(path)
    monkeypatch.setattr(type(target), 'lstat', reparse_stat)
    with pytest.raises(ValueError, match='Source linked'):
        delivery.source_manifest(simulated_delivery['root'])


@pytest.mark.parametrize('case_index', range(4))
@pytest.mark.parametrize('defect', ['omitted_attachment', 'replaced_case'])
def test_delivery_requires_each_revenue_cashflow_download(simulated_delivery, case_index, defect):
    def change(output, raw):
        spec = raw['suites'][0]['suites'][1]['specs'][case_index]
        if defect == 'omitted_attachment':
            spec['tests'][0]['results'][0]['attachments'] = []
        else:
            # Preserve the aggregate passing count: a different passing test is
            # not evidence that the required accounting-boundary case executed.
            spec['file'] = 'public-vouchers.spec.ts'
            spec['title'] = 'replacement passing test'
    simulated_delivery['change'] = change
    code, report, _ = simulated_delivery['run']()
    assert code == 1
    assert 'attachment' in report['error'].lower()
