"""Handwritten document observations; no production-generated expected answers.

The mocked PDF is an explicit four-column table with current 120/90/30 and
previous 70/50/20. Its cells, text lines and coordinates are independently
listed below. No credentials, model, network, accounting book or gold dataset
is involved in these tests.
"""
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import statement_store as store


NOW = datetime(2026, 10, 9, 1, 2, 3, tzinfo=timezone.utc)
OBSERVED_ID = 'annual-000001-2025'
REJECTED_ID = 'annual-000002-2025'
PDF_BYTES = b'%PDF-1.7\nindependently handwritten observed document\n'
REJECTED_PDF_BYTES = b'%PDF-1.7\nindependently handwritten unreadable document\n'
METRICS = ('total_assets', 'total_liabilities', 'total_equity')
LABELS = ('资产总计', '负债合计', '所有者权益合计')
CURRENT = ('120.00', '90.00', '30.00')
PREVIOUS = ('70.00', '50.00', '20.00')


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def line(number, text, y):
    return {'pdf_page': 1, 'line': number, 'text': text, 'bbox': [0.0, float(y), 90.0, float(y + 9)]}


def cells(y):
    return [(0.0, float(y), 30.0, float(y + 9)), (30.0, float(y), 40.0, float(y + 9)),
            (40.0, float(y), 60.0, float(y + 9)), (60.0, float(y), 80.0, float(y + 9))]


def handwritten_artifacts(unit='元'):
    sources = []
    for code, name, raw in [('000001', '手写观察企业', PDF_BYTES), ('000002', '手写失败企业', REJECTED_PDF_BYTES)]:
        checksum = sha(raw)
        sources.append({'id': f'annual-{code}-2025', 'company_code': code, 'company_name': name,
            'report_year': 2025, 'sha256': checksum, 'bytes': len(raw),
            'url': f'https://static.cninfo.com.cn/finalpage/2026-03-31/test-{code}.PDF',
            'local_relative_path': f'raw/{code}/2025/{checksum}.pdf',
            'status': 'verified_full_report', 'validation': {'valid': True}})
    coverage = {'reports': sources}
    coverage_hash = sha(wire(coverage))
    reports = []
    for source in sources:
        reports.append({'id': source['id'], 'company_code': source['company_code'],
            'company_name': source['company_name'], 'report_year': 2025, 'url': source['url'],
            'raw_sha256': source['sha256'], 'raw_bytes': source['bytes'],
            'local_relative_path': source['local_relative_path'], 'coverage_manifest_sha256': coverage_hash,
            'independent_ground_truth_checked': False, 'status': 'rejected',
            'error_code': 'table_not_readable', 'metrics': {}})
    header = {'pdf_page': 1, 'cells': ['项目', '附注', '2025年12月31日', '2024年12月31日'],
        'current_column_zero_based': 2, 'previous_column_zero_based': 3,
        'current_column_bbox': list(cells(30)[2]), 'previous_column_bbox': list(cells(30)[3]),
        'table_header_bbox': [0.0, 30.0, 80.0, 39.0]}
    context = {'statement_title': line(1, '合并资产负债表', 0),
        'statement_date': line(2, '2025年12月31日', 10), 'unit': unit, 'currency': 'CNY',
        'unit_factor_cny': '1000' if unit == '千元' else '1',
        'unit_source': line(3, f'金额单位为人民币{unit}', 20),
        'section_stop': line(8, '母公司资产负债表', 80)}
    metrics = {}
    normalized = ('120000.00', '90000.00', '30000.00') if unit == '千元' else CURRENT
    for i, key in enumerate(METRICS):
        y = 40 + 10 * i
        metrics[key] = {'amount_reported': CURRENT[i], 'previous_amount_reported': PREVIOUS[i],
            'normalized_cny': normalized[i], 'unit': unit, 'currency': 'CNY', 'pdf_page': 1,
            'table': 1, 'table_row': i + 2, 'current_table_column_zero_based': 2,
            'previous_table_column_zero_based': 3, 'current_cell': CURRENT[i], 'previous_cell': PREVIOUS[i],
            'label_cell': LABELS[i], 'current_cell_bbox': list(cells(y)[2]),
            'previous_cell_bbox': list(cells(y)[3]),
            'text_lines': [{k: v for k, v in line(i + 5, f'{LABELS[i]} {CURRENT[i]} {PREVIOUS[i]}', y).items()
                            if k != 'pdf_page'}]}
    reports[0].update(status='observed', error_code=None, metrics=metrics, context=context,
                      column_header=header, balance_check={'difference_reported_unit': '0.00', 'equal_as_reported': True})
    probe = {'schema_version': 'annual-statement-readonly-probe-v1', 'coverage_manifest_sha256': coverage_hash,
        'parser_source_sha256': 'c' * 64, 'runtime_agent_used': False, 'ocr_used': False,
        'rule_coverage_evidence': False, 'financial_accuracy_benchmark': False, 'network_or_model_calls': 0,
        'requested_ids': [OBSERVED_ID, REJECTED_ID], 'reports_attempted': 2, 'reports_observed': 1,
        'reports_rejected': 1, 'total_available_reports': 2, 'unprobed_reports': 0, 'reports': reports}
    return probe, coverage


def bind_coverage(probe, coverage):
    checksum = sha(wire(coverage))
    probe['coverage_manifest_sha256'] = checksum
    for report in probe['reports']:
        report['coverage_manifest_sha256'] = checksum


def mock_pdf(monkeypatch, unit='元'):
    table_rows = [['项目', '附注', '2025年12月31日', '2024年12月31日']]
    table_rows.extend([[LABELS[i], '', CURRENT[i], PREVIOUS[i]] for i in range(3)])
    geometry = [SimpleNamespace(cells=cells(y), bbox=(0.0, float(y), 80.0, float(y + 9)))
                for y in (30, 40, 50, 60)]
    table = SimpleNamespace(extract=lambda: deepcopy(table_rows), rows=geometry)
    text_rows = [line(1, '合并资产负债表', 0), line(2, '2025年12月31日', 10),
        line(3, f'金额单位为人民币{unit}', 20), line(4, '项目 附注 2025年12月31日 2024年12月31日', 30),
        *[line(i + 5, f'{LABELS[i]} {CURRENT[i]} {PREVIOUS[i]}', 40 + 10 * i) for i in range(3)],
        line(8, '母公司资产负债表', 80)]
    actual_lines = [{'text': r['text'], 'x0': r['bbox'][0], 'top': r['bbox'][1],
                     'x1': r['bbox'][2], 'bottom': r['bbox'][3]} for r in text_rows]
    page = SimpleNamespace(width=100.0, height=100.0, rotation=0,
        find_tables=lambda: [table], extract_text_lines=lambda: deepcopy(actual_lines))

    class PDF:
        pages = [page]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    opened = []

    def open_pdf(handle):
        opened.append(handle.getvalue())
        return PDF()

    monkeypatch.setattr(store.pdfplumber, 'open', open_pdf)
    return opened


def write_sources(tmp_path, probe, coverage):
    raw_root, root = tmp_path / 'annual', tmp_path / 'snapshots'
    probe_path, coverage_path = tmp_path / 'probe.json', tmp_path / 'coverage.json'
    probe_path.write_bytes(wire(probe))
    coverage_path.write_bytes(wire(coverage))
    for source, data in zip(coverage['reports'], (PDF_BYTES, REJECTED_PDF_BYTES), strict=True):
        target = raw_root / source['local_relative_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root, probe_path, coverage_path, raw_root


@pytest.fixture
def artifacts():
    return handwritten_artifacts()


@pytest.fixture
def saved(tmp_path, monkeypatch):
    probe, coverage = handwritten_artifacts()
    opened = mock_pdf(monkeypatch)
    root, probe_path, coverage_path, raw_root = write_sources(tmp_path, probe, coverage)
    result = store.save_statement_snapshot(root, probe_path, coverage_path, raw_root)
    return SimpleNamespace(root=root, raw_root=raw_root, probe_path=probe_path, coverage_path=coverage_path,
        probe=probe, coverage=coverage, opened=opened, result=result,
        snapshot_id=result['dataset']['snapshot_id'], scope_id=result['dataset']['dataset_scope_id'])


@pytest.mark.parametrize('unit,expected', [('元', ['120.00', '90.00', '30.00']),
                                         ('千元', ['120000.00', '90000.00', '30000.00'])])
def test_projection_preserves_literal_current_prior_and_unverified_state(unit, expected):
    probe, coverage = handwritten_artifacts(unit)
    actual = store.project(wire(probe), wire(coverage), observed_at=NOW)
    assert actual.dataset.report_count == 2 and actual.dataset.observed_count == actual.dataset.rejected_count == 1
    assert actual.dataset.read_only is True and actual.dataset.scope == 'public_consolidated_balance_sheet'
    report = actual.reports[0]
    assert [o.amount_reported for o in report.observations] == ['120.00', '90.00', '30.00']
    assert [o.previous_amount_reported for o in report.observations] == ['70.00', '50.00', '20.00']
    assert [o.normalized_cny for o in report.observations] == expected
    assert all(o.semantic_status == 'unverified_extraction' for o in report.observations)
    assert report.balance_check.difference_reported_unit == '0.00' and report.balance_check.equal_as_reported is True
    rejected = actual.reports[1]
    assert rejected.status == 'rejected' and rejected.error_code == 'table_not_readable'
    assert rejected.observations == [] and rejected.balance_check is None
    assert 'book_id' not in actual.model_dump(mode='json')['dataset']


@pytest.mark.parametrize('field,value', [
    ('amount_reported', 120.0), ('amount_reported', 'NaN'), ('amount_reported', '1e2'),
    ('amount_reported', '120,00'), ('previous_amount_reported', 'Infinity'),
    ('previous_amount_reported', '71.00'), ('current_cell', '121.00'), ('previous_cell', '69.00'),
    ('normalized_cny', '120000.01'), ('normalized_cny', '120000'),
    ('current_table_column_zero_based', 3), ('unit', '元'), ('currency', 'USD'),
    ('current_cell_bbox', [0.0, 0.0, 0.0, 1.0]),
])
def test_projection_rejects_money_unit_column_or_cell_mismatch(field, value):
    probe, coverage = handwritten_artifacts('千元')
    probe['reports'][0]['metrics']['total_assets'][field] = value
    with pytest.raises(ValueError):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('field,value', [
    ('reports_attempted', 1), ('reports_observed', 2), ('reports_rejected', 0),
    ('total_available_reports', 3), ('unprobed_reports', 1), ('reports_observed', True),
    ('network_or_model_calls', True), ('network_or_model_calls', 1), ('runtime_agent_used', True),
    ('ocr_used', True), ('rule_coverage_evidence', True), ('financial_accuracy_benchmark', True),
    ('coverage_manifest_sha256', '0' * 64),
])
def test_project_rejects_counts_capability_and_coverage_binding_corruption(artifacts, field, value):
    probe, coverage = artifacts
    probe[field] = value
    with pytest.raises(ValueError):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('mutation', ['id', 'company', 'source_sha', 'source_bytes', 'path', 'origin',
                                     'duplicate_source', 'duplicate_request', 'source_unverified', 'truth'])
def test_projection_rejects_forged_membership_source_and_hidden_truth(artifacts, mutation):
    probe, coverage = artifacts
    row = probe['reports'][0]
    if mutation == 'id':
        row['id'] = 'annual-999999-2025'
        probe['requested_ids'][0] = row['id']
    elif mutation == 'company':
        row['company_name'] = '伪造企业'
    elif mutation == 'source_sha':
        row['raw_sha256'] = '0' * 64
    elif mutation == 'source_bytes':
        row['raw_bytes'] += 1
    elif mutation == 'path':
        row['local_relative_path'] = coverage['reports'][0]['local_relative_path'] = '../outside.pdf'
    elif mutation == 'origin':
        row['url'] = coverage['reports'][0]['url'] = 'https://static.cninfo.com.cn.evil.example/a.pdf'
    elif mutation == 'duplicate_source':
        coverage['reports'].append(deepcopy(coverage['reports'][0]))
    elif mutation == 'duplicate_request':
        probe['requested_ids'][1] = probe['requested_ids'][0]
    elif mutation == 'source_unverified':
        coverage['reports'][0]['validation']['valid'] = False
    else:
        row['independent_ground_truth_checked'] = True
    if mutation in {'path', 'origin', 'duplicate_source', 'source_unverified'}:
        bind_coverage(probe, coverage)
    with pytest.raises(ValueError):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('mutation', ['amounts', 'no_error', 'wrong_status'])
def test_rejected_report_is_retained_without_fabricated_amounts(artifacts, mutation):
    probe, coverage = artifacts
    report = probe['reports'][1]
    if mutation == 'amounts':
        report['metrics'] = {'total_assets': deepcopy(probe['reports'][0]['metrics']['total_assets'])}
    elif mutation == 'no_error':
        report['error_code'] = None
    else:
        report['status'] = 'success'
    with pytest.raises(ValueError):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('payload', [b'{"reports":[],"reports":[]}', b'{"value":NaN}',
                                     b'{"value":Infinity}', b'[]'])
def test_input_json_refuses_duplicate_nonfinite_or_nonobject(payload):
    with pytest.raises(ValueError):
        store.read_json(payload)


def test_projection_requires_timezone_and_actual_arithmetic(artifacts):
    probe, coverage = artifacts
    with pytest.raises(ValueError, match='timezone'):
        store.project(wire(probe), wire(coverage), observed_at=NOW.replace(tzinfo=None))
    probe['reports'][0]['balance_check']['difference_reported_unit'] = '1.00'
    with pytest.raises(ValueError, match='Arithmetic'):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


def test_save_load_is_append_only_and_identical_import_does_not_overwrite(saved):
    snapshot, _, _ = store.load_statement_snapshot(saved.root, saved.snapshot_id)
    directory = saved.root / saved.snapshot_id
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    imported = store.save_statement_snapshot(saved.root, saved.probe_path, saved.coverage_path, saved.raw_root)
    assert imported['status'] == 'existing_verified' and imported['dataset'] == saved.result['dataset']
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert store.list_statement_snapshots(saved.root) == [snapshot.dataset]
    manifest = json.loads(before['manifest.json'])
    assert manifest['financial_truth_verified'] is False
    assert manifest['raw_source_checked_at_import'] is manifest['pdf_locators_checked_at_import'] is True


@pytest.mark.parametrize('name', ['probe', 'coverage', 'snapshot'])
def test_artifact_bytes_are_checked_on_every_load(saved, name):
    target = saved.root / saved.snapshot_id / f'{name}.json'
    target.write_bytes(target.read_bytes() + b' ')
    with pytest.raises(ValueError, match='artifact bytes'):
        store.load_statement_snapshot(saved.root, saved.snapshot_id)


def test_forged_snapshot_with_updated_hash_still_fails_projection_comparison(saved):
    directory = saved.root / saved.snapshot_id
    value = json.loads((directory / 'snapshot.json').read_bytes())
    value['reports'][0]['observations'][0]['amount_reported'] = '121.00'
    forged = wire(value)
    (directory / 'snapshot.json').write_bytes(forged)
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    manifest['snapshot_sha256'] = sha(forged)
    (directory / 'manifest.json').write_bytes(wire(manifest))
    with pytest.raises(ValueError, match='projection'):
        store.load_statement_snapshot(saved.root, saved.snapshot_id)


@pytest.mark.parametrize('field,value', [('snapshot_id', 'statement_snapshot_' + '0' * 64),
                                         ('schema_version', 'unknown-v9')])
def test_manifest_identity_or_version_tampering_is_rejected(saved, field, value):
    path = saved.root / saved.snapshot_id / 'manifest.json'
    manifest = json.loads(path.read_bytes())
    manifest[field] = value
    path.write_bytes(wire(manifest))
    with pytest.raises(ValueError, match='manifest identity'):
        store.load_statement_snapshot(saved.root, saved.snapshot_id)


def test_source_pdf_reads_verified_bytes_and_refuses_changed_or_missing_file(saved):
    snapshot, _, coverage = store.load_statement_snapshot(saved.root, saved.snapshot_id)
    report = snapshot.reports[0]
    assert store.source_pdf(saved.raw_root, report, coverage) == PDF_BYTES
    path = saved.raw_root / coverage['reports'][0]['local_relative_path']
    path.write_bytes(PDF_BYTES.replace(b'observed', b'changed!'))
    with pytest.raises(ValueError, match='bytes have changed'):
        store.source_pdf(saved.raw_root, report, coverage)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        store.source_pdf(saved.raw_root, report, coverage)


def test_path_escapes_and_read_budgets_are_rejected_without_reading_contents(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='escapes'):
        store.safe_path(tmp_path / 'store', '../outside.json')
    with pytest.raises(ValueError, match='escapes'):
        store.safe_path(tmp_path / 'store', str(tmp_path / 'outside.json'))
    path = tmp_path / 'large.json'
    path.write_bytes(b'12345')
    with pytest.raises(ValueError, match='read budget'):
        store.read_bounded(path, limit=4)
    assert store.read_bounded(path, limit=5) == b'12345'


def symlink_or_skip(path, target):
    try:
        path.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f'Host cannot create test symlink: {type(exc).__name__}')


def test_symlink_cannot_escape_snapshot_artifact_storage(saved, tmp_path):
    target = saved.root / saved.snapshot_id / 'probe.json'
    external = tmp_path / 'outside-probe.json'
    external.write_bytes(target.read_bytes())
    target.unlink()
    symlink_or_skip(target, external)
    with pytest.raises(ValueError, match='escapes'):
        store.load_statement_snapshot(saved.root, saved.snapshot_id)


def test_symlink_cannot_escape_manifest_storage_even_if_bytes_are_valid(saved, tmp_path):
    target = saved.root / saved.snapshot_id / 'manifest.json'
    external = tmp_path / 'outside-manifest.json'
    external.write_bytes(target.read_bytes())
    target.unlink()
    symlink_or_skip(target, external)
    with pytest.raises(ValueError, match='escapes'):
        store.load_statement_snapshot(saved.root, saved.snapshot_id)


def test_pdf_locators_use_exact_original_table_row_cells_line_and_geometry(saved):
    snapshot, _, _ = store.load_statement_snapshot(saved.root, saved.snapshot_id)
    observation = snapshot.reports[0].observations[0]
    page = store.verify_locator(PDF_BYTES, observation)
    assert page == {'width': 100.0, 'height': 100.0, 'rotation': 0}
    assert saved.opened[-1] == PDF_BYTES
    assert observation.pdf_page == observation.table == 1 and observation.table_row == 2
    assert observation.text_lines[0].line == 5 and observation.current_table_column_zero_based == 2


@pytest.mark.parametrize('field,value', [('current_cell', '121.00'), ('previous_cell', '71.00'),
    ('table_row', 3), ('current_cell_bbox', [40.0, 41.0, 60.0, 49.0]),
    ('previous_cell_bbox', [60.0, 40.0, 81.0, 49.0])])
def test_locator_rejects_source_cell_row_or_geometry_mismatch(saved, field, value):
    observation = store.load_statement_snapshot(saved.root, saved.snapshot_id)[0].reports[0].observations[0]
    values = observation.model_dump()
    values[field] = value
    modified = store.StatementObservation.model_validate(values)
    with pytest.raises(ValueError, match='locator'):
        store.verify_locator(PDF_BYTES, modified)


@pytest.mark.parametrize('field,value', [('text', '伪造原文'), ('line', 6), ('bbox', [0.0, 41.0, 90.0, 49.0])])
def test_locator_rejects_exact_text_line_or_bbox_mismatch(saved, field, value):
    observation = store.load_statement_snapshot(saved.root, saved.snapshot_id)[0].reports[0].observations[0]
    values = observation.model_dump()
    values['text_lines'][0][field] = value
    with pytest.raises(ValueError, match='text line locator'):
        store.verify_locator(PDF_BYTES, store.StatementObservation.model_validate(values))


def test_locator_rejects_forged_metric_label_even_when_amount_cells_and_lines_are_unchanged(saved):
    observation = store.load_statement_snapshot(saved.root, saved.snapshot_id)[0].reports[0].observations[0]
    values = observation.model_dump()
    values['label_cell'] = '归属于母公司所有者权益合计'
    with pytest.raises(ValueError, match='label|locator'):
        store.verify_locator(PDF_BYTES, store.StatementObservation.model_validate(values))


def test_failed_source_locator_does_not_install_any_snapshot(tmp_path, monkeypatch):
    probe, coverage = handwritten_artifacts()
    probe['reports'][0]['metrics']['total_assets']['text_lines'][0]['text'] = '伪造原文'
    root, probe_path, coverage_path, raw_root = write_sources(tmp_path, probe, coverage)
    mock_pdf(monkeypatch)
    with pytest.raises(ValueError, match='locator'):
        store.save_statement_snapshot(root, probe_path, coverage_path, raw_root)
    assert not root.exists()


@pytest.mark.parametrize('part,field,value', [
    ('statement_title', 'text', '母公司资产负债表'), ('statement_date', 'text', '2024年12月31日'),
    ('unit_source', 'text', '金额单位为美元元'), ('unit_source', 'text', '金额单位为人民币千元'),
])
def test_context_semantics_cannot_switch_consolidation_year_currency_or_unit(artifacts, part, field, value):
    probe, coverage = artifacts
    probe['reports'][0]['context'][part][field] = value
    with pytest.raises(ValueError):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('part', ['context', 'column_header'])
def test_typed_context_rejects_hidden_truth_fields_before_runtime_projection(artifacts, part):
    probe, coverage = artifacts
    probe['reports'][0][part]['hidden_answer'] = 'HIDDEN_GOLD_SENTINEL'
    with pytest.raises(ValueError, match='hidden_answer|Extra inputs'):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('mutation', ['wrong_current', 'wrong_previous', 'same_column', 'unknown_period'])
def test_context_header_distinguishes_current_previous_periods(artifacts, mutation):
    probe, coverage = artifacts
    header = probe['reports'][0]['column_header']
    if mutation == 'wrong_current':
        header['cells'][2] = '2024年12月31日'
    elif mutation == 'wrong_previous':
        header['cells'][3] = '2025年12月31日'
    elif mutation == 'same_column':
        header['previous_column_zero_based'] = 2
    else:
        header['cells'][2] = '本年度任意期间'
    with pytest.raises(ValueError):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('part,field,value', [
    ('statement_title', 'line', 2), ('statement_title', 'bbox', [1.0, 0.0, 90.0, 9.0]),
    ('unit_source', 'line', 2), ('section_stop', 'line', 7),
])
def test_context_locator_checks_source_line_number_and_geometry(saved, part, field, value):
    row = deepcopy(saved.probe['reports'][0])
    row['context'][part][field] = value
    with pytest.raises(ValueError, match='context locator'):
        store.verify_context(PDF_BYTES, row)


def test_header_geometry_cannot_be_forged_while_retaining_same_current_amounts(saved):
    row = deepcopy(saved.probe['reports'][0])
    row['column_header']['current_column_bbox'] = [40.0, 31.0, 60.0, 39.0]
    with pytest.raises(ValueError, match='header locator'):
        store.verify_context(PDF_BYTES, row)


def test_literal_currency_unit_with_colon_is_also_explicit_not_guessed(artifacts):
    probe, coverage = artifacts
    probe['reports'][0]['context']['unit_source']['text'] = '金额单位：人民币元'
    projected = store.project(wire(probe), wire(coverage), observed_at=NOW)
    assert [item.normalized_cny for item in projected.reports[0].observations] == ['120.00', '90.00', '30.00']


def test_pdf_budget_is_checked_before_parsing_and_duplicate_import_rechecks_source(saved, monkeypatch):
    snapshot, _, coverage = store.load_statement_snapshot(saved.root, saved.snapshot_id)
    monkeypatch.setattr(store, 'MAX_PDF_BYTES', len(PDF_BYTES) - 1)
    with pytest.raises(ValueError, match='read budget'):
        store.source_pdf(saved.raw_root, snapshot.reports[0], coverage)
    monkeypatch.setattr(store, 'MAX_PDF_BYTES', 32_000_000)
    path = saved.raw_root / coverage['reports'][0]['local_relative_path']
    path.write_bytes(b'%PDF-1.7\nchanged original\n')
    with pytest.raises(ValueError, match='bytes have changed'):
        store.save_statement_snapshot(saved.root, saved.probe_path, saved.coverage_path, saved.raw_root)


def test_maximum_report_budget_is_enforced_before_source_projection(artifacts):
    probe, coverage = artifacts
    probe['reports'] = [deepcopy(probe['reports'][1]) for _ in range(301)]
    probe['requested_ids'] = [f'annual-{index:06d}-2025' for index in range(301)]
    for row, report_id in zip(probe['reports'], probe['requested_ids'], strict=True):
        row['id'] = report_id
    with pytest.raises(ValueError, match='membership'):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


@pytest.mark.parametrize('value', [[0.0, float('nan'), 1.0, 2.0], [0.0, 0.0, float('inf'), 2.0]])
def test_geometry_model_refuses_nonfinite_coordinates_independently_of_json_parser(value):
    with pytest.raises(ValueError, match='geometry'):
        store.TextLine(line=1, text='literal text', bbox=value)


def split_label_artifacts():
    probe, coverage = handwritten_artifacts()
    equity = probe['reports'][0]['metrics']['total_equity']
    equity['table_rows'] = [4, 5]
    equity['label_fragments'] = [
        {'table_row': 4, 'table_column_zero_based': 0, 'text': '所有者权益', 'bbox': [0.0, 60.0, 30.0, 69.0]},
        {'table_row': 5, 'table_column_zero_based': 0, 'text': '合计', 'bbox': [0.0, 69.0, 30.0, 78.0]},
    ]
    equity['text_lines'] = [
        {'line': 7, 'text': '所有者权益 30.00 20.00', 'bbox': [0.0, 60.0, 90.0, 69.0]},
        {'line': 8, 'text': '合计', 'bbox': [0.0, 69.0, 90.0, 78.0]},
    ]
    probe['reports'][0]['context']['section_stop'] = line(9, '母公司资产负债表', 80)
    return probe, coverage


def mock_split_pdf(monkeypatch):
    table_rows = [
        ['项目', '附注', '2025年12月31日', '2024年12月31日'],
        ['资产总计', '', '120.00', '70.00'], ['负债合计', '', '90.00', '50.00'],
        ['所有者权益', '', '30.00', '20.00'], ['合计', '', '', ''],
    ]
    geometry = [SimpleNamespace(cells=cells(y), bbox=(0.0, float(y), 80.0, float(y + 9)))
                for y in (30, 40, 50, 60, 69)]
    table = SimpleNamespace(extract=lambda: deepcopy(table_rows), rows=geometry)
    saved_lines = [
        line(1, '合并资产负债表', 0), line(2, '2025年12月31日', 10), line(3, '金额单位为人民币元', 20),
        line(4, '项目 附注 2025年12月31日 2024年12月31日', 30), line(5, '资产总计 120.00 70.00', 40),
        line(6, '负债合计 90.00 50.00', 50), line(7, '所有者权益 30.00 20.00', 60),
        line(8, '合计', 69), line(9, '母公司资产负债表', 80),
    ]
    actual_lines = [{'text': row['text'], 'x0': row['bbox'][0], 'top': row['bbox'][1],
                     'x1': row['bbox'][2], 'bottom': row['bbox'][3]} for row in saved_lines]
    page = SimpleNamespace(width=100.0, height=100.0, rotation=0,
        find_tables=lambda: [table], extract_text_lines=lambda: deepcopy(actual_lines))

    class PDF:
        pages = [page]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(store.pdfplumber, 'open', lambda data: PDF())


def test_adjacent_split_equity_label_keeps_both_literal_rows_without_merging_source_cells(monkeypatch):
    probe, coverage = split_label_artifacts()
    mock_split_pdf(monkeypatch)
    snapshot = store.project(wire(probe), wire(coverage), observed_at=NOW)
    equity = snapshot.reports[0].observations[2]
    assert equity.label_cell == '所有者权益合计'
    assert equity.amount_reported == '30.00' and equity.previous_amount_reported == '20.00'
    assert equity.table_rows == [4, 5]
    assert [item.text for item in equity.label_fragments] == ['所有者权益', '合计']
    assert [item.line for item in equity.text_lines] == [7, 8]
    assert store.verify_locator(PDF_BYTES, equity) == {'width': 100.0, 'height': 100.0, 'rotation': 0}
    store.verify_context(PDF_BYTES, probe['reports'][0])


@pytest.mark.parametrize('mutation', ['nonadjacent', 'wrong_column', 'wrong_fragment', 'wrong_bbox'])
def test_split_label_cannot_invent_a_total_from_unrelated_source_rows(mutation, monkeypatch):
    probe, coverage = split_label_artifacts()
    equity = probe['reports'][0]['metrics']['total_equity']
    if mutation == 'nonadjacent':
        equity['label_fragments'][1]['table_row'] = 7
        equity['table_rows'][1] = 7
    elif mutation == 'wrong_column':
        equity['label_fragments'][1]['table_column_zero_based'] = 1
    elif mutation == 'wrong_fragment':
        equity['label_fragments'][1]['text'] = '归母权益合计'
    else:
        equity['label_fragments'][1]['bbox'][1] = 80.0
        equity['label_fragments'][1]['bbox'][3] = 89.0
    with pytest.raises(ValueError):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


def test_split_label_original_cell_geometry_is_checked_after_structural_validation(monkeypatch):
    probe, coverage = split_label_artifacts()
    equity = probe['reports'][0]['metrics']['total_equity']
    # Both saved boxes remain mutually aligned, but differ from the explicitly
    # handwritten original PDF by one unit. Projection alone cannot detect it.
    for fragment in equity['label_fragments']:
        fragment['bbox'][0] += 0.5
        fragment['bbox'][2] += 0.5
    mock_split_pdf(monkeypatch)
    observed = store.project(wire(probe), wire(coverage), observed_at=NOW).reports[0].observations[2]
    with pytest.raises(ValueError, match='Split label locator'):
        store.verify_locator(PDF_BYTES, observed)


def test_all_totals_cannot_swap_current_and_prior_columns_while_balance_still_equals_zero(artifacts):
    probe, coverage = artifacts
    metrics = probe['reports'][0]['metrics']
    for key, current, previous in zip(METRICS, CURRENT, PREVIOUS, strict=True):
        observation = metrics[key]
        observation.update(amount_reported=previous, previous_amount_reported=current,
                           normalized_cny=previous, current_cell=previous, previous_cell=current,
                           current_table_column_zero_based=3, previous_table_column_zero_based=2)
        observation['current_cell_bbox'], observation['previous_cell_bbox'] = (
            observation['previous_cell_bbox'], observation['current_cell_bbox'])
    # Both years independently balance (120 = 90 + 30 and 70 = 50 + 20).
    # Therefore arithmetic alone cannot detect the false current-year reading.
    # The independently handwritten header fixes 2025 in column 2, not 3.
    with pytest.raises(ValueError, match='column|header|period'):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


def test_only_liability_cannot_read_previous_year_as_current_even_with_consistent_new_difference(artifacts):
    probe, coverage = artifacts
    report = probe['reports'][0]
    liability = report['metrics']['total_liabilities']
    liability.update(amount_reported='50.00', previous_amount_reported='90.00', normalized_cny='50.00',
                     current_cell='50.00', previous_cell='90.00',
                     current_table_column_zero_based=3, previous_table_column_zero_based=2)
    liability['current_cell_bbox'], liability['previous_cell_bbox'] = (
        liability['previous_cell_bbox'], liability['current_cell_bbox'])
    # Handwritten arithmetic in the corrupted artifact is internally consistent:
    # 120 - 50 - 30 = 40. The 2025 header still fixes current year in the
    # left amount column. A mismatch must be rejected before arithmetic alone.
    report['balance_check'] = {'difference_reported_unit': '40.00', 'equal_as_reported': False}
    with pytest.raises(ValueError, match='column|header|period'):
        store.project(wire(probe), wire(coverage), observed_at=NOW)


def test_original_continuation_cells_still_need_their_explicit_period_header(monkeypatch):
    probe, coverage = handwritten_artifacts()
    report = probe['reports'][0]
    report['context']['section_stop']['pdf_page'] = 3
    liability = report['metrics']['total_liabilities']
    liability.update(pdf_page=2, amount_reported='50.00', previous_amount_reported='90.00',
                     normalized_cny='50.00', current_cell='50.00', previous_cell='90.00',
                     current_table_column_zero_based=3, previous_table_column_zero_based=2)
    liability['current_cell_bbox'], liability['previous_cell_bbox'] = (
        liability['previous_cell_bbox'], liability['current_cell_bbox'])
    report['balance_check'] = {'difference_reported_unit': '40.00', 'equal_as_reported': False}
    mock_pdf(monkeypatch)
    original_open = store.pdfplumber.open

    def three_pages(handle):
        document = original_open(handle)
        document.pages = [document.pages[0]] * 3
        return document

    monkeypatch.setattr(store.pdfplumber, 'open', three_pages)
    snapshot = store.project(wire(probe), wire(coverage), observed_at=NOW)
    observation = snapshot.reports[0].observations[1]
    # Both amounts and their locators exist on the original continuation page.
    # Their period assignment still contradicts the explicit inherited header.
    with pytest.raises(ValueError, match='period header'):
        store.verify_locator(PDF_BYTES, observation, report_row=report)


@pytest.mark.parametrize('boundary', ['title', 'stop'])
def test_amount_cell_cannot_cross_the_consolidated_statement_boundary(monkeypatch, boundary):
    probe, coverage = handwritten_artifacts()
    report = probe['reports'][0]
    snapshot = store.project(wire(probe), wire(coverage), observed_at=NOW)
    if boundary == 'title':
        report['context']['statement_title']['bbox'] = [0.0, 45.0, 90.0, 54.0]
    else:
        report['context']['section_stop']['bbox'] = [0.0, 45.0, 90.0, 54.0]
    mock_pdf(monkeypatch)
    with pytest.raises(ValueError, match='statement boundary'):
        store.verify_locator(PDF_BYTES, snapshot.reports[0].observations[0], report_row=report)


def test_header_before_consolidated_title_is_not_borrowed(monkeypatch):
    probe, coverage = handwritten_artifacts()
    report = probe['reports'][0]
    snapshot = store.project(wire(probe), wire(coverage), observed_at=NOW)
    # Real header y30..39 is before the specified title y35. The amount y40..49
    # is inside, so only the header-boundary check can reject this borrowed row.
    report['context']['statement_title']['bbox'] = [0.0, 35.0, 90.0, 39.0]
    mock_pdf(monkeypatch)
    with pytest.raises(ValueError, match='header locator is not unique'):
        store.verify_locator(PDF_BYTES, snapshot.reports[0].observations[0], report_row=report)
