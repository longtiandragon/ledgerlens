"""Independent two-record fixtures test batch boundaries, not OCR accuracy."""
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import ocr_workflow as workflow
from app.db import ImportBatch, OcrReview, Record
from app.ocr_observations import normalize
from test_ocr_workflow import context as context, start, body, save


@pytest.fixture
def batch_context(context, monkeypatch):
    def infer(doc):
        return normalize({'res': {
            'rec_texts': ['2026-09-12', '合计 1394.99', '发票 A017', '2026-09-13', '合计 35.01', '发票 B002'],
            'rec_scores': [0.9] * 6,
            'rec_polys': [[[1, y], [90, y], [90, y + 9], [1, y + 9]] for y in (1, 16, 31, 46, 61, 76)]}},
            source_hash=doc.sha256, width=100, height=100, engine={'test_double': 'two-manual-records'})
    monkeypatch.setattr(workflow, 'infer', infer)
    return context


def batch_body(run):
    single = body(run)
    first = {k: single.pop(k) for k in ('record', 'field_evidence')}
    second = deepcopy(first)
    second['record'].update(source_id='scan-invoice-2', amount='35.01', business_date='2026-09-13', invoice_id='B002')
    for field, index in [('business_date', 3), ('amount', 4), ('invoice_id', 5)]:
        second['field_evidence'][field] = [run['result']['observations'][index]['observation_id']]
    return {**single, 'schema_version': 'ocr-batch-review-v1', 'entries': [first, second]}


def test_batch_review_import_is_atomic_traceable_and_idempotent(batch_context):
    client, factory, book = batch_context
    run = start(batch_context)
    saved = save(batch_context, run, batch_body(run))
    assert saved.status_code == 201, saved.text
    preview = saved.json()['preview']
    assert preview['record_count'] == 2
    assert client.get(f'/api/v1/books/{book}/records').json() == []
    assert [r['locator']['review_record_index'] for r in preview['rows']] == [1, 2]
    assert preview['rows'][0]['locator']['field_evidence']['amount'][0]['line'] == 2
    assert preview['rows'][1]['locator']['field_evidence']['amount'][0]['line'] == 5
    confirmed = client.post(f'/api/v1/imports/{preview["id"]}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['imported'] == 2 and confirmed.json()['duplicate_count'] == 0
    assert client.post(f'/api/v1/imports/{preview["id"]}/confirm').json() == confirmed.json()
    with factory() as s:
        records = s.scalars(select(Record).order_by(Record.source_id)).all()
        assert [r.payload['amount'] for r in records] == ['1394.99', '35.01']
        assert sum(Decimal(r.payload['amount']) for r in records) == Decimal('1430.00')
        assert all(r.document_id == run['document_id'] for r in records)
        assert [r.locator['review_record_index'] for r in records] == [1, 2]
    snapshot = client.get(f'/api/v1/snapshots/{confirmed.json()["snapshot_id"]}').json()
    assert set(snapshot['record_ids']) == {r.id for r in records}
    fresh = client.get(f'/api/v1/ocr/runs/{run["id"]}?book_id={book}').json()
    assert fresh['result'] == run['result']
    assert save(batch_context, fresh, batch_body(fresh)).status_code == 409


@pytest.mark.parametrize('fault', ['duplicate', 'empty', 'oversize', 'foreign_line', 'missing_field', 'period',
                                  'float_money', 'unconfirmed', 'unknown_schema', 'mixed_contract'])
def test_invalid_second_row_or_envelope_leaves_no_partial_batch(batch_context, fault):
    _, factory, _ = batch_context
    run = start(batch_context)
    payload = batch_body(run)
    second = payload['entries'][1]
    if fault == 'duplicate':
        second['record']['source_id'] = payload['entries'][0]['record']['source_id']
    elif fault == 'empty':
        payload['entries'] = []
    elif fault == 'oversize':
        payload['entries'] *= 51
    elif fault == 'foreign_line':
        second['field_evidence']['amount'] = ['ocr-from-another-run']
    elif fault == 'missing_field':
        del second['field_evidence']['amount']
    elif fault == 'period':
        second['record']['period'] = '2026-10'
    elif fault == 'float_money':
        second['record']['amount'] = 35.01
    elif fault == 'unconfirmed':
        payload['confirmed'] = False
    elif fault == 'unknown_schema':
        payload['schema_version'] = 'future-unknown'
    else:
        payload['record'] = second['record']
    assert save(batch_context, run, payload).status_code == 422
    with factory() as s:
        for cls in (ImportBatch, OcrReview, Record):
            assert s.scalars(select(cls)).all() == []


@pytest.mark.parametrize('fault', ['removed', 'added', 'reordered', 'amount', 'locator_index', 'locator_field'])
def test_changed_batch_preview_is_rejected_entirely(batch_context, fault):
    client, factory, _ = batch_context
    run = start(batch_context)
    saved = save(batch_context, run, batch_body(run)).json()
    with factory() as s:
        batch = s.get(ImportBatch, saved['preview']['id'])
        rows = deepcopy(batch.rows)
        if fault == 'removed':
            rows.pop()
        elif fault == 'added':
            rows.append(deepcopy(rows[-1]))
        elif fault == 'reordered':
            rows.reverse()
        elif fault == 'amount':
            rows[1]['record']['amount'] = '35.02'
        elif fault == 'locator_index':
            rows[1]['locator']['review_record_index'] = 1
        else:
            rows[1]['locator']['field_evidence']['amount'] = rows[0]['locator']['field_evidence']['amount']
        batch.rows = rows
        s.commit()
    assert client.post(f'/api/v1/imports/{saved["preview"]["id"]}/confirm').status_code == 409
    with factory() as s:
        assert s.scalars(select(Record)).all() == []


def test_single_history_upgrades_to_batch_and_revision_can_remove_a_draft(batch_context):
    client, factory, book = batch_context
    run = start(batch_context)
    legacy = save(batch_context, run).json()
    fresh = client.get(f'/api/v1/ocr/runs/{run["id"]}?book_id={book}').json()
    batch = save(batch_context, fresh, batch_body(fresh)).json()
    assert batch['review']['version'] == 2
    fresh = client.get(f'/api/v1/ocr/runs/{run["id"]}?book_id={book}').json()
    revised = batch_body(fresh)
    revised['entries'].pop(0)
    latest = save(batch_context, fresh, revised).json()
    assert latest['review']['version'] == 3 and latest['preview']['record_count'] == 1
    for old in (legacy, batch):
        assert client.post(f'/api/v1/imports/{old["preview"]["id"]}/confirm').status_code == 409
    with factory() as s:
        assert s.get(OcrReview, legacy['review']['id']).payload == legacy['review']['payload']
        assert s.get(OcrReview, batch['review']['id']).payload == batch['review']['payload']
    assert client.post(f'/api/v1/imports/{latest["preview"]["id"]}/confirm').json()['imported'] == 1


def test_late_source_conflict_rolls_back_other_batch_records(batch_context):
    client, factory, book = batch_context
    run = start(batch_context)
    first = save(batch_context, run).json()
    prior = client.post(f'/api/v1/imports/{first["preview"]["id"]}/confirm').json()
    run = start(batch_context)
    payload = batch_body(run)
    payload['entries'][0]['record']['source_id'] = 'new-valid-record'
    payload['entries'][1]['record']['source_id'] = 'scan-invoice-1'
    saved = save(batch_context, run, payload).json()
    response = client.post(f'/api/v1/imports/{saved["preview"]["id"]}/confirm')
    assert response.status_code == 409 and '内容冲突' in response.text
    with factory() as s:
        records = s.scalars(select(Record)).all()
        assert len(records) == 1 and records[0].source_id == 'scan-invoice-1'
        assert records[0].payload['amount'] == '1394.99'
    assert client.get(f'/api/v1/books/{book}/overview').json()['latest_snapshot_id'] == prior['snapshot_id']


def test_shared_header_references_allowed_but_record_locators_remain_distinct(batch_context):
    run = start(batch_context)
    payload = batch_body(run)
    payload['entries'][1]['record']['business_date'] = '2026-09-12'
    payload['entries'][1]['field_evidence']['business_date'] = payload['entries'][0]['field_evidence']['business_date']
    saved = save(batch_context, run, payload)
    assert saved.status_code == 201
    rows = saved.json()['preview']['rows']
    assert rows[0]['locator']['field_evidence']['business_date'] == rows[1]['locator']['field_evidence']['business_date']
    assert rows[0]['locator']['review_record_index'] != rows[1]['locator']['review_record_index']
