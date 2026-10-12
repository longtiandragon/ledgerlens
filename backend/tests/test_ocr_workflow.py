"""Product boundary tests use a deterministic OCR double, not model accuracy claims."""
import io
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import main, ocr_workflow as workflow
from app.config import settings
from app.db import Book, Document, ImportBatch, OcrRun, OcrReview, Record, session
from app.ocr_observations import normalize


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "ocr.sqlite"}', connect_args={'check_same_thread': False})
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(main, 'Session', factory)
    monkeypatch.setattr(workflow, 'Session', factory)
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr(workflow, 'available', lambda: True)
    def dependency():
        with factory() as s:
            yield s
    main.app.dependency_overrides[session] = dependency
    def infer(doc):
        return normalize({'res': {'rec_texts': ['2026-09-12', '合计 1394.99', '发票 A017'],
                                 'rec_scores': [0.99, 0.95, 0.9],
                                 'rec_polys': [[[1, y], [90, y], [90, y + 10], [1, y + 10]] for y in [1, 20, 40]]}},
                         source_hash=doc.sha256, width=100, height=100, engine={'test_double': True})
    monkeypatch.setattr(workflow, 'infer', infer)
    with TestClient(main.app) as client:
        book = client.post('/api/v1/books', json={'name': '人工OCR边界', 'framework': 'CAS', 'period': '2026-09'}).json()['id']
        yield client, factory, book
    main.app.dependency_overrides.pop(session)
    engine.dispose()


def start(context):
    client, _, book = context
    image = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(image, format='PNG')
    response = client.post('/api/v1/ocr/runs', data={'book_id': book}, files={'file': ('scan.png', image.getvalue(), 'image/png')})
    assert response.status_code == 202, response.text
    run = client.get(f'/api/v1/ocr/runs/{response.json()["id"]}', params={'book_id': book})
    assert run.status_code == 200, run.text
    return run.json()


def body(run):
    ids = [r['observation_id'] for r in run['result']['observations']]
    return {'expected_parent_id': run['reviews'][-1]['id'] if run['reviews'] else None,
            'expected_result_digest': run['result_digest'], 'expected_book_revision': run['book_revision'],
            'record': {'source_id': 'scan-invoice-1', 'kind': 'invoice', 'business_date': '2026-09-12',
                       'period': '2026-09', 'currency': 'CNY', 'amount': '1394.99', 'invoice_id': 'A017'},
            'field_evidence': {'business_date': [ids[0]], 'amount': [ids[1]], 'invoice_id': [ids[2]]},
            'confirmed': True, 'comment': '逐项对照原件，人工填写日期、金额和发票号'}


def save(context, run, payload=None):
    client, _, book = context
    return client.post(f'/api/v1/ocr/runs/{run["id"]}/reviews', params={'book_id': book}, json=payload or body(run))


def test_explicit_review_then_import_retains_source_and_snapshot(context):
    client, factory, book = context
    run = start(context)
    assert run['status'] == 'complete' and run['result']['confirmation'] == 'unconfirmed'
    assert 'worker_token' not in run
    assert client.get(f'/api/v1/books/{book}/records').json() == []
    response = save(context, run)
    assert response.status_code == 201, response.text
    batch = response.json()['preview']
    assert client.get(f'/api/v1/books/{book}/records').json() == []
    confirmed = client.post(f'/api/v1/imports/{batch["id"]}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['imported'] == 1
    assert client.post(f'/api/v1/imports/{batch["id"]}/confirm').json() == confirmed.json()
    with factory() as s:
        record = s.scalar(select(Record))
        assert record.document_id == run['document_id']
        assert record.locator['ocr_review_id'] == response.json()['review']['id']
        assert record.payload['amount'] == '1394.99'
        assert s.get(OcrRun, run['id']).result['confirmation'] == 'unconfirmed'
    assert client.get(f'/api/v1/snapshots/{confirmed.json()["snapshot_id"]}').json()['record_ids'] == [record.id]
    refreshed = client.get(f'/api/v1/ocr/runs/{run["id"]}', params={'book_id': book}).json()
    assert save(context, refreshed).status_code == 409


@pytest.mark.parametrize('change', ['unconfirmed', 'numeric_confirmation', 'float_money', 'foreign_line', 'missing_field', 'wrong_period', 'extra_field'])
def test_unconfirmed_or_untraceable_fields_rejected(context, change):
    run = start(context)
    payload = body(run)
    if change == 'unconfirmed':
        payload['confirmed'] = False
    elif change == 'numeric_confirmation':
        payload['confirmed'] = 1
    elif change == 'float_money':
        payload['record']['amount'] = 1394.99
    elif change == 'foreign_line':
        payload['field_evidence']['amount'] = ['ocr-foreign']
    elif change == 'missing_field':
        del payload['field_evidence']['amount']
    elif change == 'wrong_period':
        payload['record']['period'] = '2026-10'
    else:
        payload['field_evidence']['anything'] = payload['field_evidence']['amount']
    assert save(context, run, payload).status_code == 422


def test_review_versions_invalidate_prior_preview_and_forbid_remap(context):
    client, factory, book = context
    run = start(context)
    first = save(context, run).json()
    # A stale browser cannot fork the immutable history.
    assert save(context, run).status_code == 409
    fresh = client.get(f'/api/v1/ocr/runs/{run["id"]}', params={'book_id': book}).json()
    payload = body(fresh)
    payload['record']['invoice_id'] = 'A0I7'
    second = save(context, fresh, payload)
    assert second.status_code == 201, second.text
    assert second.json()['review']['version'] == 2
    assert client.post(f'/api/v1/imports/{first["preview"]["id"]}/confirm').status_code == 409
    assert client.put(f'/api/v1/imports/{second.json()["preview"]["id"]}/mapping', json={}).status_code == 409
    with factory() as s:
        assert s.get(OcrReview, first['review']['id']).payload['record']['invoice_id'] == 'A017'


@pytest.mark.parametrize('mutation', ['source', 'result', 'book_revision', 'book_version', 'preview', 'locator'])
def test_changed_evidence_or_context_blocks_import(context, mutation):
    client, factory, _ = context
    run = start(context)
    saved = save(context, run).json()
    with factory() as s:
        if mutation == 'source':
            from pathlib import Path
            Path(s.get(Document, run['document_id']).storage_path).write_bytes(b'changed')
        elif mutation == 'result':
            item = s.get(OcrRun, run['id'])
            result = deepcopy(item.result)
            result['observations'][0]['text'] = '2026-09-13'
            item.result = result
        elif mutation in {'book_revision', 'book_version'}:
            book = s.get(Book, run['book_id'])
            if mutation == 'book_revision':
                book.revision += 1
            else:
                book.version += 1
        else:
            batch = s.get(ImportBatch, saved['preview']['id'])
            rows = deepcopy(batch.rows)
            if mutation == 'preview':
                rows[0]['record']['amount'] = '1.00'
            else:
                rows[0]['locator']['field_evidence'] = {}
            batch.rows = rows
        s.commit()
    assert client.post(f'/api/v1/imports/{saved["preview"]["id"]}/confirm').status_code == 409
    with factory() as s:
        assert s.scalar(select(Record)) is None


def test_scope_disabled_failure_recovery_and_duplicate_execution(context, monkeypatch):
    client, factory, book = context
    run = start(context)
    assert client.get(f'/api/v1/ocr/runs/{run["id"]}?book_id=foreign').status_code == 404
    assert client.post(f'/api/v1/ocr/runs/{run["id"]}/reviews?book_id=foreign', json=body(run)).status_code == 404
    def fail(doc):
        raise TimeoutError('offline worker timeout')
    monkeypatch.setattr(workflow, 'infer', fail)
    workflow.execute(run['id'])  # Already complete: cannot re-run or overwrite.
    assert client.get(f'/api/v1/ocr/runs/{run["id"]}?book_id={book}').json()['status'] == 'complete'
    failed = start(context)
    assert failed['status'] == 'failed' and failed['result'] == {} and 'TimeoutError' in failed['error']
    with factory() as s:
        interrupted = s.get(OcrRun, failed['id'])
        interrupted.status, interrupted.worker_token = 'running', 'old-worker'
        s.commit()
        workflow.recover(s)
        s.refresh(interrupted)
        assert interrupted.status == 'failed' and interrupted.worker_token is None
        assert '中断' in interrupted.error
    monkeypatch.setattr(workflow, 'available', lambda: False)
    assert not client.get('/api/v1/ocr/capabilities').json()['available']
    assert client.post('/api/v1/ocr/runs', data={'book_id': book}, files={'file': ('scan.png', b'x')}).status_code == 503


def test_interrupted_worker_cannot_publish_after_recovery(context, monkeypatch):
    client, factory, book = context
    original = workflow.infer
    def interrupted(doc):
        result = original(doc)
        with factory() as s:
            workflow.recover(s)
        return result
    monkeypatch.setattr(workflow, 'infer', interrupted)
    run = start(context)
    assert run['status'] == 'failed' and run['result'] == {} and '中断' in run['error']
    assert client.get(f'/api/v1/books/{book}/records').json() == []


@pytest.mark.parametrize('mutation', ['status', 'identity', 'coordinates', 'blank'])
def test_malformed_output_and_empty_page_never_create_preview(context, monkeypatch, mutation):
    original = workflow.infer
    def malformed(doc):
        result = original(doc)
        if mutation == 'status':
            result['status'] = 'success'
        elif mutation == 'identity':
            result['observations'][0]['observation_id'] = 'unbound'
        elif mutation == 'coordinates':
            result['observations'][0]['locator']['polygon_px'][0][0] = -1
        else:
            result['observations'] = []
            result['status'] = 'no_text_detected'
        return result
    monkeypatch.setattr(workflow, 'infer', malformed)
    run = start(context)
    if mutation == 'blank':
        assert run['status'] == 'complete'
        payload = {'expected_result_digest': run['result_digest'], 'expected_book_revision': run['book_revision'],
                   'record': {'source_id': 'blank', 'kind': 'invoice', 'business_date': '2026-09-12',
                              'period': '2026-09', 'amount': '1.00'}, 'field_evidence': {}, 'confirmed': True, 'comment': '空白页'}
        assert save(context, run, payload).status_code == 422
    else:
        assert run['status'] == 'failed' and run['result'] == {}


def test_pdf_partial_preserves_failure_and_only_exposes_successful_page(context, monkeypatch):
    from app.ocr_pdf import locate_page
    original = workflow.infer
    class Converter:
        def to_page(self, x, y):
            return (x, 100 - y)
    def partial(doc):
        prediction = original(doc)
        result = {k: prediction[k] for k in ['confirmation', 'ledger_writes', 'source_sha256', 'engine']}
        page = locate_page(prediction, 1, {'width_pt': 100, 'height_pt': 100}, Converter(), doc.sha256, prediction['engine'])
        return {**result, 'schema_version': 'local-pdf-ocr-observations-v1', 'status': 'completed_with_failures',
                'page_count': 2, 'failed_pages': 1, 'pages': [page, {'page': 2, 'status': 'failed',
                  'confirmation': 'unconfirmed', 'error': {'type': 'TestFailure'}}]}
    monkeypatch.setattr(workflow, 'infer', partial)
    run = start(context)
    assert run['status'] == 'partial' and run['result']['failed_pages'] == 1
    # Same review API can only cite successful page observations.
    image_shape = {**run, 'result': {'observations': run['result']['pages'][0]['observations']}}
    assert save(context, run, body(image_shape)).status_code == 201


def test_local_queue_admission_is_bounded(context, monkeypatch):
    client, _, book = context
    monkeypatch.setattr(workflow, 'execute', lambda identity: None)
    for _ in range(4):
        assert start(context)['status'] == 'queued'
    response = client.post('/api/v1/ocr/runs', data={'book_id': book}, files={'file': ('scan.png', b'x')})
    assert response.status_code == 429
