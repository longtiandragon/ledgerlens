"""HTTP regressions with independent synthetic process sources, no model calls."""
from datetime import UTC, datetime
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.process_api import router
from app.config import settings
from app.process_events import adapt_traces
from app.process_store import save_process_snapshot


@pytest.fixture
def process_client(tmp_path, monkeypatch):
    root = tmp_path / 'process-snapshots'
    monkeypatch.setattr(settings, 'process_datasets_dir', root)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, root


def import_fixture(root, tmp_path, raw_hash='a' * 64):
    trace = {'source': 'BPI2019', 'complete_trace': True, 'accounting_framework': None, 'case_id': 'fixture-item',
             'attributes': {'concept:name': 'fixture-item', 'Item Category': '3-way match, invoice before GR',
                            'Item Type': 'Standard', 'Goods Receipt': 'true', 'GR-Based Inv. Verif.': 'false'},
             'events': [{'concept:name': activity, 'time:timestamp': '2018-01-01T00:00:00.000Z',
                         'Cumulative net worth (EUR)': value} for activity, value in (
                         ('Create Purchase Order Item', '0'), ('Record Invoice Receipt', '42.0'),
                         ('Clear Invoice', '42.0'))]}
    raw = (json.dumps(trace, ensure_ascii=False) + '\n').encode()
    subset = tmp_path / f'{raw_hash[0]}-fixture.jsonl'
    subset.write_bytes(raw)
    snapshot = adapt_traces([trace], raw_sha256=raw_hash, subset_sha256=hashlib.sha256(raw).hexdigest(),
                            observed_at=datetime(2026, 10, 8, tzinfo=UTC))
    save_process_snapshot(root, snapshot, subset)
    path = f'/api/v1/process-datasets/{snapshot.dataset.dataset_scope_id}/snapshots/{snapshot.dataset.snapshot_id}'
    return snapshot, path


def test_empty_read_only_process_workspace_does_not_create_storage(process_client):
    client, root = process_client
    response = client.get('/api/v1/process-datasets')
    assert response.status_code == 200 and response.json()['snapshots'] == []
    assert response.json()['read_only'] is True and not root.exists()
    assert client.post('/api/v1/process-datasets', json={'book_id': 'foreign'}).status_code == 405


def test_http_process_evidence_pagination_and_exact_locator(process_client, tmp_path):
    client, root = process_client
    snapshot, path = import_fixture(root, tmp_path)
    listing = client.get('/api/v1/process-datasets').json()['snapshots']
    assert len(listing) == 1 and listing[0]['trace_count'] == 1 and listing[0]['event_count'] == 3
    assert client.get(path).json()['dataset_scope_id'] == snapshot.dataset.dataset_scope_id
    first = client.get(path + '/events?limit=2').json()
    assert first['total'] == 3 and first['next_offset'] == 2
    assert first['items'][0]['source_numeric_text'] == '0'
    assert first['items'][0]['occurred_at_raw'] == '2018-01-01T00:00:00.000Z'
    assert first['items'][0]['causal_order_status'] == 'same_timestamp_undetermined'
    last = client.get(path + '/events?offset=2&limit=2').json()
    assert last['next_offset'] is None and len(last['items']) == 1
    assert last['items'][0]['event_type'] == 'invoice_clearing_recorded'
    assert not {'currency', 'debit', 'credit', 'voucher_id'} & last['items'][0].keys()
    traces = client.get(path + '/traces?q=fixture').json()
    assert traces['total'] == 1 and traces['items'][0]['event_count'] == 3
    trace = client.get(path + '/trace', params={'source_trace_id': 'fixture-item', 'limit': 1}).json()
    assert trace['trace']['business_completion'] == 'not_determined'
    assert trace['events']['total'] == 3 and len(trace['events']['items']) == 1
    evidence = client.get(path + '/evidence/' + first['items'][0]['evidence_id']).json()
    assert evidence['locator']['jsonl_line'] == 1 and evidence['locator']['source_event_ordinal'] == 1
    assert evidence['event_attributes']['Cumulative net worth (EUR)'] == '0'


def test_scope_and_evidence_cannot_cross_process_snapshots(process_client, tmp_path):
    client, root = process_client
    first, path = import_fixture(root, tmp_path)
    other, other_path = import_fixture(root, tmp_path, raw_hash='b' * 64)
    wrong_scope = path.replace(first.dataset.dataset_scope_id, other.dataset.dataset_scope_id)
    assert client.get(wrong_scope + '/events').status_code == 404
    assert client.get(other_path + '/evidence/' + first.events[0].evidence_id).status_code == 404
    assert client.get(path + '/trace?source_trace_id=foreign-item').status_code == 404
    assert client.get(path + '/evidence/book-evidence').status_code == 404


@pytest.mark.parametrize('suffix', ['/events?limit=201', '/events?limit=0', '/events?offset=-1',
                                    '/traces?limit=201', '/trace?source_trace_id=fixture-item&limit=0',
                                    '/events?occurred_from=2018-01-01T00:00:00',
                                    '/events?occurred_from=2019-01-01T00:00:00Z&occurred_to=2018-01-01T00:00:00Z'])
def test_invalid_query_is_rejected_instead_of_broadening(process_client, tmp_path, suffix):
    client, root = process_client
    _, path = import_fixture(root, tmp_path)
    assert client.get(path + suffix).status_code == 422


def test_source_tampering_is_rejected_after_prior_successful_read(process_client, tmp_path):
    client, root = process_client
    snapshot, path = import_fixture(root, tmp_path)
    assert client.get(path + '/events').status_code == 200
    source = root / snapshot.dataset.snapshot_id / 'source.jsonl'
    source.write_bytes(source.read_bytes().replace(b'42.0', b'99.0'))
    response = client.get(path + '/events')
    assert response.status_code == 409
    assert 'fixture-item' not in response.text and str(root) not in response.text
    assert client.get('/api/v1/process-datasets').status_code == 409


def test_rule_review_cites_only_snapshot_evidence_and_exports_locatable_workpaper(process_client, tmp_path):
    client, root = process_client
    _, path = import_fixture(root, tmp_path)
    catalog = client.get('/api/v1/process-datasets/rule-catalog').json()
    assert all(rule['rule_id'].startswith('BPI2019_PROCESS.') for rule in catalog['rules'])
    review = client.get(f'{path}/trace-review', params={'source_trace_id': 'fixture-item'}).json()
    # Hand-derived: a 3-way item with clearing but no recorded goods receipt.
    clearing = next(r for r in review['results'] if r['rule_id'] == 'BPI2019_PROCESS.CLEARING_AFTER_GOODS_RECEIPT')
    assert clearing['status'] == 'needs_review' and review['conclusion'] == 'needs_review'
    listing = client.get(f'{path}/traces', params={'conclusion': 'needs_review'}).json()
    assert [item['rule_conclusion'] for item in listing['items']] == ['needs_review']
    assert client.get(f'{path}/traces', params={'conclusion': 'compatible'}).json()['total'] == 0
    assert client.get(f'{path}/traces', params={'conclusion': 'wrong'}).status_code == 422
    paper = client.get(f'{path}/trace-review/export', params={'source_trace_id': 'fixture-item'}).json()
    for item in paper['evidence']:
        resolved = client.get(f'{path}/evidence/{item["evidence_id"]}')
        assert resolved.status_code == 200 and resolved.json()['locator'] == item['locator']
    markdown = client.get(f'{path}/trace-review/export',
                          params={'source_trace_id': 'fixture-item', 'format': 'markdown'})
    assert markdown.status_code == 200 and markdown.headers['content-type'].startswith('text/markdown')
    assert '存在待核实过程差异' in markdown.text and 'JSONL 第 1 行' in markdown.text
    summary = client.get(f'{path}/rule-summary').json()
    assert summary['conclusion_counts']['needs_review'] == 1
    assert client.get(f'{path}/trace-review', params={'source_trace_id': 'missing'}).status_code == 404


def test_rule_review_refuses_other_dataset_scope(process_client, tmp_path):
    client, root = process_client
    _, path = import_fixture(root, tmp_path)
    _, other = import_fixture(root, tmp_path, raw_hash="b" * 64)
    foreign = other.split('/snapshots/')[0] + '/snapshots/' + path.split('/snapshots/')[1]
    assert client.get(f'{foreign}/trace-review', params={'source_trace_id': 'fixture-item'}).status_code == 404
    assert client.get(f'{foreign}/rule-summary').status_code == 404


def test_model_explanation_run_is_explicit_bound_and_failures_stay_failures(process_client, tmp_path, monkeypatch):
    from app import process_investigation
    client, root = process_client
    _, path = import_fixture(root, tmp_path)
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'runtime')
    monkeypatch.setattr(settings, 'deepseek_api_key', '')
    assert client.post(f'{path}/trace-investigations', params={'source_trace_id': 'fixture-item'}).status_code == 409
    monkeypatch.setattr(settings, 'deepseek_api_key', 'offline-key')
    assert client.post(f'{path}/trace-investigations', params={'source_trace_id': 'missing'}).status_code == 404
    outcomes = iter([{'status': 'complete', 'proposal': {'conclusion': 'needs_review'}, 'usage': {'calls': 1}},
                     {'status': 'budget_exhausted', 'proposal': None, 'usage': {'calls': 6}}])

    async def fake(scope, gateway, **_):
        assert scope.trace.source_trace_id == 'fixture-item' and gateway.api_key == 'offline-key'
        return next(outcomes)

    monkeypatch.setattr(process_investigation, 'investigate_trace', fake)
    first = client.post(f'{path}/trace-investigations', params={'source_trace_id': 'fixture-item'})
    assert first.status_code == 202 and first.json()['status'] == 'running'
    assert first.json()['snapshot_binding']['rule_pack']['version'] == 'bpi2019-process-rules-v1'
    done = client.get(f"/api/v1/process-datasets/process-investigations/{first.json()['id']}").json()
    assert done['status'] == 'complete' and done['result']['proposal']['conclusion'] == 'needs_review'
    second = client.post(f'{path}/trace-investigations', params={'source_trace_id': 'fixture-item'}).json()
    failed = client.get(f"/api/v1/process-datasets/process-investigations/{second['id']}").json()
    assert failed['status'] == 'budget_exhausted'
    listing = client.get(f'{path}/trace-investigations', params={'source_trace_id': 'fixture-item'}).json()['runs']
    assert [run['status'] for run in listing] == ['budget_exhausted', 'complete']
    assert client.get('/api/v1/process-datasets/process-investigations/../../etc').status_code == 404


def test_run_left_running_by_a_restart_is_marked_interrupted(process_client, tmp_path, monkeypatch):
    import json as json_module
    client, root = process_client
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'runtime')
    folder = tmp_path / 'runtime' / 'process-investigations'
    folder.mkdir(parents=True)
    run_id = 'process-run-' + 'a' * 32
    (folder / f'{run_id}.json').write_text(json_module.dumps({'id': run_id, 'status': 'running',
        'source_trace_id': 'fixture-item', 'created_at': '2026-10-12T00:00:00+00:00'}), encoding='utf-8')
    assert client.get(f'/api/v1/process-datasets/process-investigations/{run_id}').json()['status'] == 'interrupted'
