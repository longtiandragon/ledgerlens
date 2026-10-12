"""GET-only statement scope and original-byte responses, using handwritten data."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import statement_api as api_module
from app import statement_store as store
from test_statement_store import (
    OBSERVED_ID, PDF_BYTES, REJECTED_ID, handwritten_artifacts, mock_pdf, sha, wire, write_sources,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    probe, coverage = handwritten_artifacts()
    opened = mock_pdf(monkeypatch)
    root, probe_path, coverage_path, raw_root = write_sources(tmp_path, probe, coverage)
    imported = store.save_statement_snapshot(root, probe_path, coverage_path, raw_root)
    monkeypatch.setattr(api_module.settings, 'statement_datasets_dir', root)
    monkeypatch.setattr(api_module.settings, 'annual_reports_dir', raw_root)
    app = FastAPI()
    app.include_router(api_module.router)
    with TestClient(app) as client:
        snapshot = store.load_statement_snapshot(root, imported['dataset']['snapshot_id'])[0]
        base = f"/api/v1/statement-datasets/{snapshot.dataset.dataset_scope_id}/snapshots/{snapshot.dataset.snapshot_id}"
        yield SimpleNamespace(client=client, root=root, raw_root=raw_root, probe=probe, coverage=coverage,
            snapshot=snapshot, base=base, opened=opened, app=app)


def test_read_only_list_detail_reports_and_evidence_do_not_create_books_or_change_inputs(workspace):
    w = workspace
    original = {str(p): p.read_bytes() for p in w.root.rglob('*') if p.is_file()}
    result = w.client.get('/api/v1/statement-datasets')
    assert result.status_code == 200 and result.json()['read_only'] is True
    assert len(result.json()['snapshots']) == 1
    detail = w.client.get(w.base)
    assert detail.status_code == 200 and detail.json()['observed_count'] == detail.json()['rejected_count'] == 1
    reports = w.client.get(w.base + '/reports').json()
    assert reports['total'] == 2 and reports['items'][1]['status'] == 'rejected'
    assert reports['items'][1]['error_code'] == 'table_not_readable' and reports['items'][1]['observations'] == []
    values = reports['items'][0]['observations']
    assert [row['normalized_cny'] for row in values] == ['120.00', '90.00', '30.00']
    evidence = w.client.get(w.base + '/evidence/' + values[0]['evidence_id'])
    assert evidence.status_code == 200 and evidence.json()['semantic_status'] == 'unverified_extraction'
    assert evidence.json()['observation']['text_lines'][0]['line'] == 5
    assert evidence.json()['observation']['text_lines'][0]['text'] == '资产总计 120.00 70.00'
    assert evidence.json()['page'] == {'width': 100.0, 'height': 100.0, 'rotation': 0}
    assert all('book_id' not in response.text for response in (result, detail, evidence))
    assert original == {str(p): p.read_bytes() for p in w.root.rglob('*') if p.is_file()}
    assert not (w.root.parent / 'books').exists()
    assert all(route.methods == {'GET'} for route in api_module.router.routes)


@pytest.mark.parametrize('method', ['post', 'put', 'patch', 'delete'])
def test_api_has_no_write_or_model_dispatch_endpoints(workspace, method):
    assert getattr(workspace.client, method)(workspace.base).status_code == 405
    assert getattr(workspace.client, method)('/api/v1/statement-datasets').status_code == 405
    assert workspace.client.get('/api/v1/books').status_code == 404


def test_reports_filter_and_pagination_preserve_failure_and_actual_counts(workspace):
    w = workspace
    first = w.client.get(w.base + '/reports', params={'limit': 1}).json()
    assert first['total'] == 2 and first['next_offset'] == 1 and first['items'][0]['report_id'] == OBSERVED_ID
    second = w.client.get(w.base + '/reports', params={'offset': 1, 'limit': 1}).json()
    assert second['total'] == 2 and second['next_offset'] is None and second['items'][0]['report_id'] == REJECTED_ID
    rejected = w.client.get(w.base + '/reports', params={'status': 'rejected'}).json()
    assert rejected['total'] == 1 and rejected['items'][0]['error_code'] == 'table_not_readable'
    observed = w.client.get(w.base + '/reports', params={'status': 'observed', 'q': '观察企业'}).json()
    assert observed['total'] == 1 and observed['items'][0]['report_id'] == OBSERVED_ID
    assert w.client.get(w.base + '/reports', params={'q': 'not-visible'}).json()['total'] == 0


@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 101}, {'offset': -1}, {'status': 'verified'}, {'q': 'x' * 201}])
def test_report_query_limits_are_enforced(workspace, params):
    assert workspace.client.get(workspace.base + '/reports', params=params).status_code == 422


def test_dataset_snapshot_report_and_evidence_are_scoped_and_forged_ids_never_return_data(workspace):
    w = workspace
    other_scope = w.base.replace(w.snapshot.dataset.dataset_scope_id, 'annual_reports_' + '0' * 64)
    assert w.client.get(other_scope).status_code == 404
    assert w.client.get(other_scope + '/reports').status_code == 404
    assert w.client.get(w.base + '/reports/annual-999999-2025/file').status_code == 404
    assert w.client.get(w.base + '/evidence/statement_evidence_' + '0' * 64).status_code == 404
    missing = w.base.replace(w.snapshot.dataset.snapshot_id, 'statement_snapshot_' + '0' * 64)
    assert w.client.get(missing).status_code == 404


def test_file_serves_the_actual_verified_bytes_once_with_inline_no_store_headers(workspace, monkeypatch):
    w = workspace
    actual_source_pdf = api_module.source_pdf
    path = w.raw_root / w.coverage['reports'][0]['local_relative_path']
    calls = []

    def verified_then_path_changes(*args):
        data = actual_source_pdf(*args)
        calls.append(data)
        path.write_bytes(b'%PDF-1.7\nchanged after verification\n')
        return data

    monkeypatch.setattr(api_module, 'source_pdf', verified_then_path_changes)
    response = w.client.get(w.base + f'/reports/{OBSERVED_ID}/file')
    assert response.status_code == 200 and response.content == PDF_BYTES and calls == [PDF_BYTES]
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['content-disposition'] == f'inline; filename="{OBSERVED_ID}.pdf"'


@pytest.mark.parametrize('endpoint', ['file', 'evidence'])
@pytest.mark.parametrize('state,expected', [('changed', 409), ('missing', 404)])
def test_changed_source_hash_or_missing_original_is_never_served(workspace, endpoint, state, expected):
    w = workspace
    path = w.raw_root / w.coverage['reports'][0]['local_relative_path']
    if state == 'changed':
        path.write_bytes(PDF_BYTES.replace(b'observed', b'changed!'))
    else:
        path.unlink()
    url = (w.base + f'/reports/{OBSERVED_ID}/file' if endpoint == 'file' else
           w.base + '/evidence/' + w.snapshot.reports[0].observations[0].evidence_id)
    response = w.client.get(url)
    assert response.status_code == expected and response.headers['content-type'] != 'application/pdf'


@pytest.mark.parametrize('artifact', ['manifest', 'probe', 'coverage', 'snapshot'])
def test_tampered_snapshot_returns_explicit_conflict_for_list_and_detail(workspace, artifact):
    w = workspace
    path = w.root / w.snapshot.dataset.snapshot_id / f'{artifact}.json'
    if artifact == 'manifest':
        value = json.loads(path.read_bytes())
        value['snapshot_id'] = 'statement_snapshot_' + '0' * 64
        path.write_bytes(wire(value))
    else:
        path.write_bytes(path.read_bytes() + b' ')
    assert w.client.get(w.base).status_code == 409
    assert w.client.get('/api/v1/statement-datasets').status_code == 409


def test_duplicate_or_nonfinite_json_is_quarantined_not_returned_as_success(workspace):
    w = workspace
    directory = w.root / w.snapshot.dataset.snapshot_id
    (directory / 'manifest.json').write_bytes(b'{"snapshot_id":"a","snapshot_id":"b"}')
    assert w.client.get(w.base).status_code == 409
    (directory / 'manifest.json').write_bytes(b'{"value":NaN}')
    assert w.client.get(w.base).status_code == 409


def test_evidence_locator_failure_does_not_become_verified_or_return_source_bytes(workspace, monkeypatch):
    w = workspace

    def mismatch(*args):
        raise ValueError('independent original PDF geometry mismatch')

    monkeypatch.setattr(api_module, 'verify_locator', mismatch)
    observation = w.snapshot.reports[0].observations[0]
    response = w.client.get(w.base + '/evidence/' + observation.evidence_id)
    assert response.status_code == 409 and 'unverified_extraction' not in response.text


def test_unrecognized_context_fields_never_expose_hidden_truth_to_public_evidence(workspace):
    w = workspace
    directory = w.root / w.snapshot.dataset.snapshot_id
    probe = json.loads((directory / 'probe.json').read_bytes())
    probe['reports'][0]['context']['hidden_truth'] = 'HIDDEN_GOLD_SENTINEL'
    probe['reports'][0]['column_header']['gold_answer'] = 'HIDDEN_GOLD_SENTINEL'
    # Rebind the input hash and snapshot identity through projection, never
    # change an answer. The expected financial observations remain 120/90/30.
    probe_bytes = wire(probe)
    (directory / 'probe.json').write_bytes(probe_bytes)
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    manifest['probe_sha256'] = sha(probe_bytes)
    # A coherent newly imported snapshot is used so ordinary byte hash checks
    # cannot hide a missing output whitelist.
    coverage_bytes = (directory / 'coverage.json').read_bytes()
    try:
        projected = store.project(probe_bytes, coverage_bytes, observed_at=w.snapshot.dataset.observed_at)
    except ValueError:
        return  # Strictly rejecting unrecognized documentary metadata is safe too.
    new_dir = w.root / projected.dataset.snapshot_id
    new_dir.mkdir()
    manifest['snapshot_id'] = projected.dataset.snapshot_id
    snapshot_bytes = wire(projected.model_dump(mode='json'))
    manifest['snapshot_sha256'] = sha(snapshot_bytes)
    for name, data in [('probe', probe_bytes), ('coverage', coverage_bytes),
                       ('snapshot', snapshot_bytes), ('manifest', wire(manifest))]:
        (new_dir / f'{name}.json').write_bytes(data)
    base = f'/api/v1/statement-datasets/{projected.dataset.dataset_scope_id}/snapshots/{projected.dataset.snapshot_id}'
    evidence_id = projected.reports[0].observations[0].evidence_id
    response = w.client.get(base + '/evidence/' + evidence_id)
    assert response.status_code in {200, 409}
    assert 'HIDDEN_GOLD_SENTINEL' not in response.text


def test_old_evidence_identity_cannot_be_reused_under_a_new_snapshot(workspace):
    w = workspace
    probe = json.loads(wire(w.probe))
    probe['parser_source_sha256'] = 'd' * 64
    probe_path = w.root.parent / 'next-probe.json'
    coverage_path = w.root.parent / 'next-coverage.json'
    probe_path.write_bytes(wire(probe))
    coverage_path.write_bytes(wire(w.coverage))
    result = store.save_statement_snapshot(w.root, probe_path, coverage_path, w.raw_root)
    next_snapshot = store.load_statement_snapshot(w.root, result['dataset']['snapshot_id'])[0]
    assert next_snapshot.dataset.dataset_scope_id == w.snapshot.dataset.dataset_scope_id
    assert next_snapshot.dataset.snapshot_id != w.snapshot.dataset.snapshot_id
    old_id = w.snapshot.reports[0].observations[0].evidence_id
    next_id = next_snapshot.reports[0].observations[0].evidence_id
    assert old_id != next_id
    base = f'/api/v1/statement-datasets/{next_snapshot.dataset.dataset_scope_id}/snapshots/{next_snapshot.dataset.snapshot_id}'
    assert w.client.get(base + '/evidence/' + old_id).status_code == 404
    assert w.client.get(w.base + '/evidence/' + next_id).status_code == 404
    assert w.client.get(base + '/evidence/' + next_id).status_code == 200


def test_empty_storage_returns_explicit_empty_read_only_dataset(workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(api_module.settings, 'statement_datasets_dir', tmp_path / 'missing-storage')
    response = workspace.client.get('/api/v1/statement-datasets')
    assert response.status_code == 200 and response.json()['snapshots'] == []
    assert response.json()['read_only'] is True
    assert not (tmp_path / 'missing-storage').exists()
