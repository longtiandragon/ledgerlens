"""Independent runtime gates, using fresh endpoint registration and private DB.

The production app's APIRoute objects and lifespan are never attached to this
test app. Every database operation uses the fixture's in-memory engine.
"""
from copy import deepcopy
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main, jobs, model_gateway, knowledge
from app.config import settings
from app.db import (Base, Book, Document, Record, Snapshot, CheckRun, Case, Run, session)
from app.domain import check_records, rule_identity, rule_identity_key
from app.investigation import ToolContext
from app.model_gateway import RunBudget
from app.schemas import LedgerInput

EXPECTED_POLICY_TEXT = ('独立政策说明\n会计凭证审核须保留原始记录和来源定位，'
                        '发现差错应核实证据，政策参考文本不自动成为执行规则。')


def verified_fixture(runtime, monkeypatch):
    """Hand-written original and expected projection; no evaluator material."""
    folder = runtime['data'] / 'public-sources' / 'runtime-fixture-policy'
    folder.mkdir(parents=True)
    title, body = EXPECTED_POLICY_TEXT.split('\n', 1)
    raw = (f'<html><head><meta charset="utf-8"><title>{title}</title>'
           '<meta name="SiteName" content="中华人民共和国财政部">'
           '<meta name="SiteIDCode" content="bm14000001">'
           f'<meta name="ArticleTitle" content="{title}"></head>'
           f'<body><div class="TRS_Editor"><p>{body}</p></div></body></html>').encode('utf-8')
    sha = hashlib.sha256(raw).hexdigest()
    raw_path = folder / (sha + '.html')
    raw_path.write_bytes(raw)
    parsed = folder / 'parsed.json'
    parsed.write_text(json.dumps([{'page': 1, 'text': EXPECTED_POLICY_TEXT}], ensure_ascii=False), encoding='utf-8')
    source = {'id': 'runtime-fixture-policy', 'title': title, 'framework': 'BOTH',
        'url': 'https://www.mof.gov.cn/jrttts/202501/t20250102_1234567.htm',
        'publisher': '财政部', 'kind': 'policy', 'redistribution': 'source_manifest_only',
        'effective_from': '2025-01-01'}
    registry = runtime['data'] / 'trusted-source-registry.json'
    registry.write_text(json.dumps({'version': 'independent-test-trust-v1', 'sources': [source]},
                                  ensure_ascii=False), encoding='utf-8')
    acquisition = runtime['data'] / 'public-sources' / 'acquisition-report.json'
    report = {**source, 'status': 'fetched', 'sha256': sha, 'bytes': len(raw),
        'raw_path': str(raw_path), 'parsed_path': str(parsed), 'final_url': source['url'],
        'content_type': 'text/html', 'parser_version': 'collector-2', 'temporal_status': 'declared'}
    acquisition.write_text(json.dumps([report], ensure_ascii=False), encoding='utf-8')
    corpus_path = runtime['data'] / 'knowledge' / 'corpus-v2.json'
    monkeypatch.setattr(knowledge, 'DEFAULT_REGISTRY', registry)
    knowledge.build_corpus(acquisition, corpus_path, registry=registry,
                           source_root=runtime['data'] / 'public-sources')
    return {'registry': registry, 'acquisition': acquisition, 'raw_path': raw_path,
            'parsed_path': parsed, 'corpus_path': corpus_path, 'report': report}


@pytest.fixture
def runtime(tmp_path, monkeypatch, request):
    data = tmp_path / 'runtime'
    data.mkdir()
    monkeypatch.setattr(settings, 'data_dir', data)
    monkeypatch.setattr(settings, 'deepseek_api_key', 'isolated-test-key-never-sent')
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    endpoints = [
        ('/api/v1/books', main.create_book, ['POST']),
        ('/api/v1/knowledge/search', main.search_knowledge, ['GET']),
        ('/api/v1/snapshots', main.snapshot, ['POST']),
        ('/api/v1/snapshots/{identity}', main.read_snapshot, ['GET']),
        ('/api/v1/check-runs', main.check, ['POST']),
        ('/api/v1/cases/{identity}', main.read_case, ['GET']),
        ('/api/v1/cases/{identity}/investigate', main.start_investigation, ['POST']),
        ('/api/v1/exports/cases/{identity}', main.export_case, ['GET']),
    ]
    for path, endpoint, methods in endpoints:
        app.add_api_route(path, endpoint, methods=methods)

    def isolated_session():
        with sessions() as s:
            yield s

    app.dependency_overrides[session] = isolated_session
    monkeypatch.setattr(jobs, 'Session', sessions)
    calls = {'dispatch': 0, 'gateway': 0}

    def no_dispatch(*args, **kwargs):
        calls['dispatch'] += 1
        raise AssertionError('No model job should be dispatched by a rejected preflight')

    def no_gateway(*args, **kwargs):
        calls['gateway'] += 1
        raise AssertionError('No paid gateway should be constructed')

    monkeypatch.setattr(jobs, 'dispatch', no_dispatch)
    monkeypatch.setattr(model_gateway, 'DeepSeekGateway', no_gateway)
    with TestClient(app) as client:
        response = client.post('/api/v1/books', json={'name': 'private knowledge fixture',
            'framework': getattr(request, 'param', 'CAS'), 'period': '2026-09', 'accounts': ['1002', '6001']})
        assert response.status_code == 200, response.text
        book_id = response.json()['id']
        with sessions() as s:
            raw = b'private isolated journal source'
            source_path = data / 'source.txt'
            source_path.write_bytes(raw)
            doc = Document(id='private-document', book_id=book_id, name='source.txt',
                sha256=hashlib.sha256(raw).hexdigest(), storage_path=str(source_path), synthetic=True)
            s.add(doc)
            for index, account, debit, credit in [('a', '1002', '100.00', '0.00'),
                                                  ('b', '6001', '0.00', '99.00')]:
                original = LedgerInput.model_validate({'source_id': 'source-' + index,
                    'kind': 'journal', 'voucher_id': 'V1', 'business_date': '2026-09-01',
                    'period': '2026-09', 'account': account, 'debit': debit, 'credit': credit
                }).model_dump(mode='json', exclude_none=True)
                identity, evidence = 'record-' + index, 'evidence-' + index
                payload = {**original, 'id': identity, 'evidence_id': evidence,
                           'observed_at': '2026-10-01T00:00:00+00:00'}
                s.add(Record(id=identity, book_id=book_id, document_id=doc.id,
                    source_id=original['source_id'], kind='journal', evidence_id=evidence,
                    fingerprint=hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                    payload=payload, locator={'row': 1 if index == 'a' else 2}))
            s.commit()
        yield {'client': client, 'sessions': sessions, 'book_id': book_id,
               'data': data, 'calls': calls}
    engine.dispose()


def record_state(runtime):
    with runtime['sessions']() as s:
        return [(record.id, record.fingerprint, deepcopy(record.payload)) for record in s.scalars(
            select(Record).order_by(Record.id))]


def search(runtime):
    return runtime['client'].get('/api/v1/knowledge/search', params={
        'book_id': runtime['book_id'], 'query': '凭证'})


def snapshot(runtime):
    return runtime['client'].post('/api/v1/snapshots', json={'book_id': runtime['book_id']})


def legacy_corpus(runtime):
    path = runtime['data'] / 'knowledge' / 'corpus.json'
    path.parent.mkdir()
    path.write_text(json.dumps([{'chunk_id': 'legacy-answer', 'source_id': 'test-policy',
        'framework': 'BOTH', 'url': 'https://www.mof.gov.cn/test',
        'text': '凭证审核答案标记 LEGACY_ANSWER_SENTINEL', 'effective_from': None,
        'effective_to': None, 'temporal_status': 'unverified'}], ensure_ascii=False), encoding='utf-8')


def seed_legacy_case(runtime):
    with runtime['sessions']() as s:
        book = s.get(Book, runtime['book_id'])
        records = list(s.scalars(select(Record).order_by(Record.id)))
        policy = {'framework': book.framework, 'version': book.version, 'period': book.period,
            'execution_rules': rule_identity(), 'currency': book.currency, 'accounts': book.accounts,
            'locked_periods': [], 'reference_passages': [{'source_id': 'unbound-legacy-reference',
            'text': '凭证审核答案标记 LEGACY_ANSWER_SENTINEL', 'framework': 'BOTH',
            'url': 'https://www.mof.gov.cn/test', 'title': 'legacy', 'effective_from': None,
            'effective_to': None, 'status': 'unverified'}]}
        manifest = {'records': [(row.id, row.fingerprint) for row in records], 'policy': policy}
        snap = Snapshot(id='legacy-ledger-snapshot', book_id=book.id, revision=book.revision,
            record_ids=[row.id for row in records], policy=policy,
            digest=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            created_at='2026-10-05T00:00:00+00:00')
        check = CheckRun(id='legacy-rule-check', book_id=book.id, snapshot_id=snap.id,
                         rule_version=rule_identity_key(), status='complete')
        finding = check_records([row.payload for row in records], policy)[0]
        case = Case(id='legacy-case', book_id=book.id, snapshot_id=snap.id,
                    check_run_id=check.id, finding=finding)
        s.add_all([snap, check, case])
        s.commit()
        return case.id, snap.id


def test_missing_v2_reports_unconfigured_and_freezes_no_passages(runtime):
    before = record_state(runtime)
    response = search(runtime)
    assert response.status_code == 200, response.text
    assert response.json()['results'] == []
    assert response.json()['reference_status'] == 'not_configured'
    created = snapshot(runtime)
    assert created.status_code == 200, created.text
    assert created.json()['policy'].get('reference_passages', []) == []
    assert record_state(runtime) == before


def test_legacy_list_never_read_by_search_or_new_snapshot(runtime):
    legacy_corpus(runtime)
    before = record_state(runtime)
    response = search(runtime)
    assert response.status_code == 200, response.text
    assert response.json()['reference_status'] == 'legacy_unverified'
    assert response.json()['results'] == [] and 'LEGACY_ANSWER_SENTINEL' not in response.text
    created = snapshot(runtime)
    assert created.status_code == 200, created.text
    assert created.json()['policy'].get('reference_passages', []) == []
    assert 'LEGACY_ANSWER_SENTINEL' not in created.text
    assert record_state(runtime) == before


@pytest.mark.parametrize('raw', [b'not JSON', b'[]', b'{"schema_version":"forged"}'])
def test_present_invalid_v2_is_not_silently_treated_as_empty(runtime, raw):
    folder = runtime['data'] / 'knowledge'
    folder.mkdir()
    (folder / 'corpus-v2.json').write_bytes(raw)
    before = record_state(runtime)
    assert search(runtime).status_code == 409
    assert snapshot(runtime).status_code == 409
    assert record_state(runtime) == before
    with runtime['sessions']() as s:
        assert s.scalar(select(func.count()).select_from(Snapshot)) == 0


@pytest.mark.parametrize('mode', ['single', 'multi', 'active'])
def test_legacy_nonempty_unbound_passages_reject_paid_preflight_but_old_report_readable(runtime, mode):
    case_id, snap_id = seed_legacy_case(runtime)
    before = record_state(runtime)
    client = runtime['client']
    assert client.get(f'/api/v1/snapshots/{snap_id}').status_code == 200
    assert client.get(f'/api/v1/cases/{case_id}').status_code == 200
    exported = client.get(f'/api/v1/exports/cases/{case_id}', params={'format': 'json'})
    assert exported.status_code == 200, exported.text
    assert 'LEGACY_ANSWER_SENTINEL' in exported.text
    result = client.post(f'/api/v1/cases/{case_id}/investigate', json={'mode': mode})
    assert result.status_code == 409, result.text
    assert runtime['calls'] == {'dispatch': 0, 'gateway': 0}
    assert record_state(runtime) == before
    with runtime['sessions']() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.asyncio
async def test_worker_rejects_unbound_legacy_reference_before_gateway_and_releases_lease(runtime):
    case_id, _ = seed_legacy_case(runtime)
    with runtime['sessions']() as s:
        run = Run(id='queued-legacy-model-job', case_id=case_id, mode='single', status='queued',
                  active_slot=case_id, worker_token=None, heartbeat=None, steps=[], usage={}, result={}, error=None)
        s.add(run)
        s.commit()
    before = record_state(runtime)
    await jobs._execute_owned('queued-legacy-model-job')
    with runtime['sessions']() as s:
        result = s.get(Run, 'queued-legacy-model-job')
        assert result.status == 'failed'
        assert result.active_slot is None and result.worker_token is None
        assert result.error
        assert any(word in result.error.lower() for word in ('untrusted', 'reference', 'binding'))
    assert runtime['calls'] == {'dispatch': 0, 'gateway': 0}
    assert record_state(runtime) == before


@pytest.mark.parametrize('runtime', ['CAS', 'SCAS'], indirect=True)
def test_verified_both_source_full_text_reaches_search_snapshot_and_named_tool(runtime, monkeypatch):
    verified_fixture(runtime, monkeypatch)
    before = record_state(runtime)
    found = search(runtime)
    assert found.status_code == 200, found.text
    result = found.json()
    assert result['reference_status'] == 'verified' and len(result['results']) == 1
    assert result['results'][0]['framework'] == 'BOTH'
    assert result['results'][0]['text'] == EXPECTED_POLICY_TEXT
    created = snapshot(runtime)
    assert created.status_code == 200, created.text
    frozen = created.json()['policy']['reference_passages']
    assert len(frozen) == 1 and frozen[0]['framework'] == 'BOTH'
    assert frozen[0]['text'] == EXPECTED_POLICY_TEXT and frozen[0]['source_binding']
    with runtime['sessions']() as s:
        snap = s.get(Snapshot, created.json()['id'])
        rows = [row.payload for row in s.scalars(select(Record).order_by(Record.id))]
        policy = {**snap.policy, 'book_id': runtime['book_id'], 'snapshot_cutoff': snap.created_at}
        finding = check_records(rows, policy)[0]
    context = ToolContext(rows, policy, finding, RunBudget())
    requested = context.execute('retrieve_policy', {'query': '凭证'})
    assert requested['references_requested'] and requested['reference_count'] == 1
    assert requested['reference_passages'][0]['text'] == EXPECTED_POLICY_TEXT
    assert requested['reference_passages'][0]['source_binding'] == frozen[0]['source_binding']
    assert runtime['calls'] == {'dispatch': 0, 'gateway': 0}
    assert record_state(runtime) == before


@pytest.mark.parametrize('attack', ['raw_bytes', 'parsed_text', 'corpus_text', 'corpus_source', 'corpus_hash'])
def test_tampered_v2_or_source_rejected_by_both_live_endpoints(runtime, monkeypatch, attack):
    fixture = verified_fixture(runtime, monkeypatch)
    before = record_state(runtime)
    if attack == 'raw_bytes':
        fixture['raw_path'].write_bytes(fixture['raw_path'].read_bytes() + b'changed')
    elif attack == 'parsed_text':
        fixture['parsed_path'].write_text(json.dumps([{'page': 1,
            'text': '凭证审核答案 EVALUATOR_ANSWER_SENTINEL'}], ensure_ascii=False), encoding='utf-8')
    else:
        payload = json.loads(fixture['corpus_path'].read_text(encoding='utf-8'))
        chunk = payload['chunks'][0]
        if attack == 'corpus_text':
            chunk['text'] = '凭证审核答案 EVALUATOR_ANSWER_SENTINEL'
        elif attack == 'corpus_source':
            chunk['source_id'] = 'official-answer-disguised-as-policy'
        else:
            chunk['sha256'] = '0' * 64
        fixture['corpus_path'].write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    assert search(runtime).status_code == 409
    assert snapshot(runtime).status_code == 409
    assert record_state(runtime) == before
    assert runtime['calls'] == {'dispatch': 0, 'gateway': 0}
    with runtime['sessions']() as s:
        assert s.scalar(select(func.count()).select_from(Snapshot)) == 0


@pytest.mark.parametrize('attack', ['missing_binding', 'text', 'url', 'source_id', 'framework', 'binding_hash'])
def test_corrupted_frozen_passage_cannot_start_paid_work(runtime, monkeypatch, attack):
    verified_fixture(runtime, monkeypatch)
    created = snapshot(runtime)
    assert created.status_code == 200, created.text
    snap_id = created.json()['id']
    result = runtime['client'].post('/api/v1/check-runs', json={
        'book_id': runtime['book_id'], 'snapshot_id': snap_id})
    assert result.status_code == 200, result.text
    case_id = result.json()['cases'][0]['id']
    with runtime['sessions']() as s:
        snap = s.get(Snapshot, snap_id)
        policy = deepcopy(snap.policy)
        passage = policy['reference_passages'][0]
        if attack == 'missing_binding':
            passage.pop('source_binding')
        elif attack == 'binding_hash':
            passage['source_binding']['binding_sha256'] = '0' * 64
        elif attack == 'text':
            passage['text'] = '凭证审核答案 EVALUATOR_ANSWER_SENTINEL'
        elif attack == 'url':
            passage['url'] = 'https://www.mof.gov.cn/relabelled-answer'
        elif attack == 'source_id':
            passage['source_id'] = 'fake-confirmed-policy'
        else:
            passage['framework'] = 'CAS'
        snap.policy = policy
        s.commit()
    before = record_state(runtime)
    response = runtime['client'].post(f'/api/v1/cases/{case_id}/investigate', json={'mode': 'single'})
    assert response.status_code == 409, response.text
    assert runtime['calls'] == {'dispatch': 0, 'gateway': 0}
    with runtime['sessions']() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0
    assert record_state(runtime) == before


@pytest.mark.asyncio
async def test_worker_rejects_modified_verified_passage_without_gateway(runtime, monkeypatch):
    verified_fixture(runtime, monkeypatch)
    created = snapshot(runtime)
    assert created.status_code == 200, created.text
    result = runtime['client'].post('/api/v1/check-runs', json={
        'book_id': runtime['book_id'], 'snapshot_id': created.json()['id']})
    assert result.status_code == 200, result.text
    case_id = result.json()['cases'][0]['id']
    with runtime['sessions']() as s:
        snap = s.get(Snapshot, created.json()['id'])
        policy = deepcopy(snap.policy)
        policy['reference_passages'][0]['text'] = 'later substituted evaluator answer'
        snap.policy = policy
        s.add(Run(id='queued-modified-reference', case_id=case_id, mode='multi', status='queued',
                  active_slot=case_id, worker_token=None, heartbeat=None, steps=[], usage={}, result={}, error=None))
        s.commit()
    before = record_state(runtime)
    await jobs._execute_owned('queued-modified-reference')
    with runtime['sessions']() as s:
        run = s.get(Run, 'queued-modified-reference')
        assert run.status == 'failed' and run.active_slot is None and run.worker_token is None
    assert runtime['calls'] == {'dispatch': 0, 'gateway': 0}
    assert record_state(runtime) == before


def test_frozen_reference_survives_later_live_source_change_without_refetch(runtime, monkeypatch):
    fixture = verified_fixture(runtime, monkeypatch)
    created = snapshot(runtime)
    assert created.status_code == 200, created.text
    frozen = created.json()['policy']['reference_passages']
    before = deepcopy(frozen)
    fixture['raw_path'].write_bytes(b'late source version, not frozen source')
    assert search(runtime).status_code == 409
    # The frozen input is an original verified projection. Historical replay
    # checks its own immutable binding, never replaces it with later sources.
    verified = knowledge.verify_frozen_references(frozen)
    assert verified == before
    verified[0]['text'] = 'caller changed returned copy'
    assert frozen == before


def test_dangling_v2_symlink_branch_is_rejected_not_unconfigured(runtime, monkeypatch):
    from pathlib import Path
    path = runtime['data'] / 'knowledge' / 'corpus-v2.json'
    path.parent.mkdir()
    before = record_state(runtime)
    # Exercise the dangling-link branch without requiring Windows symlink
    # privileges. The loader must attempt and reject the missing artifact.
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda item: True if item == path else original_is_symlink(item))
    assert search(runtime).status_code == 409
    assert snapshot(runtime).status_code == 409
    assert record_state(runtime) == before
