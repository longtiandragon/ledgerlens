from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from test_knowledge_provenance import source as source, build
from app import knowledge, main, reference_selection, reference_workflow as workflow
from app.config import settings
from app.db import ReferenceSearch, ReferenceChoice, Snapshot, Case, session
from app.investigation import ToolContext
from app.model_gateway import RunBudget
from app.reference_sources import digest
from app.retrieval_index import manifest_for
from app.retrieval_vectors import segment_chunks


@pytest.fixture
def context(source, tmp_path, monkeypatch):
    parents = build(source)
    def tokenizer(text, **kwargs):
        return {'offset_mapping': [(i, i + 1) for i in range(len(text))]}
    segments = segment_chunks(parents, tokenizer)
    manifest = manifest_for(segments, [[1.0] + [0.0] * 511 for _ in segments], parents=parents,
                            engine={'fixture': 'manual-only'}, corpus_sha256=digest(source['output'].read_bytes()),
                            registry_sha256=digest(source['registry'].read_bytes()))
    monkeypatch.setattr(reference_selection, 'REGISTRIES', {'fixture': source['registry']})
    retained = tmp_path / 'build.json'
    retained.write_text(json.dumps({'status': 'completed', 'operation': 'build', 'manifest': manifest}), encoding='utf-8')
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'schema_version': 'reference-index-catalog-v1', 'indexes': [
        {'title': '手写来源索引', 'manifest': str(retained), 'corpus': str(source['output']),
         'source_root': str(source['root']), 'model_root': str(tmp_path / 'models'), 'registry_key': 'fixture'}]}), encoding='utf-8')
    monkeypatch.setattr(settings, 'reference_index_catalog', catalog)
    monkeypatch.setattr(settings, 'retrieval_python', Path(sys.executable))
    monkeypatch.setattr(settings, 'task_mode', 'local')
    data_dir = tmp_path / 'api-data'
    data_dir.mkdir()
    monkeypatch.setattr(settings, 'data_dir', data_dir)
    engine = create_engine(f'sqlite:///{data_dir / "test.sqlite"}', connect_args={'check_same_thread': False})
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(main, 'Session', factory)
    monkeypatch.setattr(workflow, 'Session', factory)
    def dependency():
        with factory() as s:
            yield s
    main.app.dependency_overrides[session] = dependency
    def infer(run):
        hits = [{**s, 'index_id': manifest['index_id'], 'score': 0.9,
                 'parent_reference': knowledge.freeze_reference(parents[0], registry=source['registry'])} for s in segments]
        return {'status': 'completed', 'operation': 'query', 'manifest': manifest,
                'query': {'text': run.query, 'framework': run.framework, 'period': run.period, 'limit': 6},
                'verified_segments': len(segments), 'ledger_writes': 0, 'paid_model_requests': 0, 'hits': hits}
    monkeypatch.setattr(workflow, 'infer', infer)
    with TestClient(main.app) as client:
        book = client.post('/api/v1/books', json={'name': '参考选择边界', 'framework': 'CAS', 'period': '2026-09'}).json()['id']
        yield {'client': client, 'factory': factory, 'book': book, 'manifest': manifest, 'source': source}
    main.app.dependency_overrides.pop(session)
    engine.dispose()


def start(context):
    client, book = context['client'], context['book']
    response = client.post('/api/v1/reference-indexes/searches', json={
        'book_id': book, 'index_id': context['manifest']['index_id'], 'query': '凭证来源如何核对'})
    assert response.status_code == 202, response.text
    return client.get(f'/api/v1/reference-indexes/searches/{response.json()["id"]}?book_id={book}').json()


def choice(context, run, **overrides):
    state = context['client'].get('/api/v1/reference-indexes/choices', params={'book_id': context['book']}).json()
    return {'book_id': context['book'], 'mode': 'persistent_bge', 'search_id': run['id'],
            'expected_result_digest': run['result_digest'], 'expected_book_revision': state['book_revision'],
            'expected_parent_id': state['choices'][0]['id'] if state['choices'] else None,
            'selected_parent_ids': [run['result']['hits'][0]['parent_chunk_id']], 'confirmed': True, **overrides}


def choose(context, run, **overrides):
    return context['client'].post('/api/v1/reference-indexes/choices', json=choice(context, run, **overrides))


def test_explicit_selection_creates_snapshot_and_preserves_historical_context(context, monkeypatch):
    client, factory, book = context['client'], context['factory'], context['book']
    demo = client.post(f'/api/v1/books/{book}/demo').json()
    before = client.get(f'/api/v1/snapshots/{demo["snapshot_id"]}').json()
    records = client.get(f'/api/v1/books/{book}/records').json()
    run = start(context)
    assert run['status'] == 'complete' and 'worker_token' not in run
    # Searching does not change the book snapshot or its business facts.
    assert client.get(f'/api/v1/books/{book}/overview').json()['latest_snapshot_id'] == before['id']
    result = choose(context, run)
    assert result.status_code == 201, result.text
    saved = result.json()
    snapshot = client.get(f'/api/v1/snapshots/{saved["snapshot_id"]}').json()
    assert snapshot['id'] != before['id'] and snapshot['record_ids'] == before['record_ids']
    assert client.get(f'/api/v1/books/{book}/records').json() == records
    assert client.get(f'/api/v1/snapshots/{before["id"]}').json() == before
    reference_selection.verify_snapshot_references(snapshot['policy'])
    # Today’s index, weights and originals are not required to read frozen references.
    monkeypatch.setattr(settings, 'reference_index_catalog', None)
    context['source']['raw'].write_bytes(b'original changed after snapshot')
    checked = client.post('/api/v1/check-runs', json={'book_id': book, 'snapshot_id': snapshot['id']})
    assert checked.status_code == 200, checked.text
    with factory() as s:
        case = s.scalar(select(Case).where(Case.snapshot_id == snapshot['id']))
        tool_context = ToolContext(records, snapshot['policy'], case.finding, RunBudget())
        assert tool_context.reference_ids == set(run['result']['hits'][i]['parent_chunk_id'] for i in range(len(run['result']['hits'])))
    later = client.post('/api/v1/snapshots', json={'book_id': book}).json()
    assert later['id'] == snapshot['id']


@pytest.mark.parametrize('mutation', ['foreign_parent', 'duplicate_parent', 'empty', 'unconfirmed', 'numeric_confirm', 'digest', 'revision', 'parent_version'])
def test_invalid_selection_cannot_create_new_snapshot(context, mutation):
    client, factory = context['client'], context['factory']
    run = start(context)
    payload = choice(context, run)
    if mutation == 'foreign_parent':
        payload['selected_parent_ids'] = ['another-query-parent']
    elif mutation == 'duplicate_parent':
        payload['selected_parent_ids'] *= 2
    elif mutation == 'empty':
        payload['selected_parent_ids'] = []
    elif mutation == 'unconfirmed':
        payload['confirmed'] = False
    elif mutation == 'numeric_confirm':
        payload['confirmed'] = 1
    elif mutation == 'digest':
        payload['expected_result_digest'] = 'f' * 64
    elif mutation == 'revision':
        payload['expected_book_revision'] += 1
    else:
        payload['expected_parent_id'] = 'stale'
    response = client.post('/api/v1/reference-indexes/choices', json=payload)
    assert response.status_code in {409, 422}, response.text
    with factory() as s:
        assert s.scalar(select(ReferenceChoice)) is None


def test_new_business_data_invalidates_search_even_after_refreshing_revision(context):
    run = start(context)
    context['client'].post(f'/api/v1/books/{context["book"]}/demo')
    assert choose(context, run).status_code == 409


def test_other_book_cannot_read_or_select_search(context):
    client = context['client']
    run = start(context)
    other = client.post('/api/v1/books', json={'name': '另一个账套', 'framework': 'CAS', 'period': '2026-09'}).json()['id']
    assert client.get(f'/api/v1/reference-indexes/searches/{run["id"]}?book_id={other}').status_code == 404
    assert choose(context, run, book_id=other).status_code == 404


def test_keyword_restore_appends_version_and_old_snapshot_stays_frozen(context):
    client, book = context['client'], context['book']
    run = start(context)
    saved = choose(context, run).json()
    original = client.get(f'/api/v1/snapshots/{saved["snapshot_id"]}').json()
    # Same captured search is stale after selecting it; no duplicate active choice.
    assert choose(context, run).status_code == 409
    restored = client.post('/api/v1/reference-indexes/choices', json={'book_id': book, 'mode': 'keyword',
        'expected_parent_id': saved['choice']['id'], 'expected_book_revision': saved['book_revision'], 'confirmed': True})
    assert restored.status_code == 201, restored.text
    assert restored.json()['choice']['version'] == 2
    new = client.get(f'/api/v1/snapshots/{restored.json()["snapshot_id"]}').json()
    assert 'reference_choice' not in new['policy']
    assert client.get(f'/api/v1/snapshots/{original["id"]}').json() == original
    assert client.get('/api/v1/reference-indexes/choices', params={'book_id': book}).json()['choices'][1]['id'] == saved['choice']['id']


def test_policy_change_expires_choice_explicitly_without_blocking_edit(context):
    client, book = context['client'], context['book']
    saved = choose(context, start(context)).json()
    old = client.get(f'/api/v1/snapshots/{saved["snapshot_id"]}').json()
    changed = client.put(f'/api/v1/books/{book}', json={'name': '新期间', 'framework': 'CAS', 'period': '2026-10'})
    assert changed.status_code == 200, changed.text
    overview = client.get(f'/api/v1/books/{book}/overview').json()
    new = client.get(f'/api/v1/snapshots/{overview["latest_snapshot_id"]}').json()
    assert new['policy']['reference_choice_status'] == 'expired_by_policy_change'
    assert 'reference_choice' not in new['policy']
    assert client.get(f'/api/v1/snapshots/{old["id"]}').json() == old


@pytest.mark.parametrize('mutation', ['result', 'choice', 'snapshot', 'scope', 'registry'])
def test_altered_bound_evidence_is_rejected(context, mutation):
    client, factory, book = context['client'], context['factory'], context['book']
    run = start(context)
    if mutation == 'result':
        with factory() as s:
            stored = s.get(ReferenceSearch, run['id'])
            altered = deepcopy(stored.result)
            altered['hits'][0]['score'] = 0.1
            stored.result = altered
            s.commit()
        assert choose(context, run).status_code == 409
        return
    saved = choose(context, run).json()
    with factory() as s:
        snap = s.get(Snapshot, saved['snapshot_id'])
        policy = deepcopy(snap.policy)
        if mutation == 'choice':
            stored = s.get(ReferenceChoice, saved['choice']['id'])
            stored.payload = {'mode': 'keyword'}
            s.commit()
            assert client.post('/api/v1/snapshots', json={'book_id': book}).status_code == 409
            return
        if mutation == 'snapshot':
            policy['reference_passages'][0]['text'] = 'forged'
        elif mutation == 'scope':
            policy['period'] = '2026-10'
        else:
            context['source']['registry'].write_text('{}')
        with pytest.raises(ValueError):
            reference_selection.verify_snapshot_references(policy)


def test_failed_and_interrupted_workers_do_not_publish_choices(context, monkeypatch):
    original = workflow.infer
    def fail(run):
        raise TimeoutError('local only')
    monkeypatch.setattr(workflow, 'infer', fail)
    failed = start(context)
    assert failed['status'] == 'failed' and failed['result'] == {} and 'TimeoutError' in failed['error']
    def interrupted(run):
        result = original(run)
        with context['factory']() as s:
            workflow.recover(s)
        return result
    monkeypatch.setattr(workflow, 'infer', interrupted)
    interrupted_run = start(context)
    assert interrupted_run['status'] == 'failed' and interrupted_run['result'] == {}
    assert '中断' in interrupted_run['error']


def test_duplicate_dispatch_and_unconfigured_mode(context, monkeypatch):
    run = start(context)
    def unexpected(run):
        pytest.fail('A completed query must not be executed again')
    monkeypatch.setattr(workflow, 'infer', unexpected)
    workflow.execute(run['id'])
    monkeypatch.setattr(settings, 'reference_index_catalog', None)
    caps = context['client'].get('/api/v1/reference-indexes/capabilities').json()
    assert caps['available'] is False
    response = context['client'].post('/api/v1/reference-indexes/searches', json={
        'book_id': context['book'], 'index_id': context['manifest']['index_id'], 'query': '不允许默默回退'})
    assert response.status_code == 503


def test_frozen_choices_invalidate_prior_review_after_reference_change(context):
    client, book = context['client'], context['book']
    initial = client.post(f'/api/v1/books/{book}/demo').json()['snapshot_id']
    checks = client.post('/api/v1/check-runs', json={'book_id': book, 'snapshot_id': initial}).json()
    case_id = checks['cases'][0]['id']
    review = {'case_id': case_id, 'expected_snapshot_id': initial, 'decision': 'reserved', 'comment': '已有意见'}
    assert client.post('/api/v1/reviews', json=review).status_code == 200
    assert choose(context, start(context)).status_code == 201
    assert client.post('/api/v1/reviews', json=review).status_code == 409


@pytest.mark.parametrize('mutation', ['parent_text', 'segment_text', 'framework', 'manifest', 'nan_score', 'wrong_query'])
def test_worker_cannot_publish_malformed_or_foreign_results(context, monkeypatch, mutation):
    original = workflow.infer
    def altered(run):
        result = deepcopy(original(run))
        if mutation == 'parent_text':
            result['hits'][0]['parent_reference']['text'] = 'invented source'
        elif mutation == 'segment_text':
            result['hits'][0]['text'] = 'invented segment'
        elif mutation == 'framework':
            result['hits'][0]['framework'] = 'SCAS'
        elif mutation == 'manifest':
            result['manifest']['index_id'] = 'f' * 64
        elif mutation == 'nan_score':
            result['hits'][0]['score'] = float('nan')
        else:
            result['query']['text'] = 'another query'
        return result
    monkeypatch.setattr(workflow, 'infer', altered)
    run = start(context)
    assert run['status'] == 'failed' and not run['result'] and run['result_digest'] is None


def test_copied_or_malformed_snapshot_choice_is_rejected(context):
    saved = choose(context, start(context)).json()
    snapshot = context['client'].get(f'/api/v1/snapshots/{saved["snapshot_id"]}').json()
    with pytest.raises(ValueError, match='another book'):
        reference_selection.verify_snapshot_references(snapshot['policy'], 'another-book')
    for malformed in ({'payload': {}}, {'id': 'x', 'version': 1, 'digest': None, 'payload': None}, []):
        policy = {**snapshot['policy'], 'reference_choice': malformed}
        with pytest.raises(ValueError):
            reference_selection.verify_snapshot_references(policy)
