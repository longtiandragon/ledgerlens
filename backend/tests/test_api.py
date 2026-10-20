import json

import pytest
from fastapi.testclient import TestClient
from conftest import reset_test_database
from app.db import Base, engine
from app.main import app


@pytest.fixture
def client():
    reset_test_database(Base.metadata, engine)
    with TestClient(app) as client:
        yield client


def book(client, framework='CAS'):
    response = client.post('/api/v1/books', json={'name': '验证账套', 'framework': framework, 'period': '2026-09'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def upload(client, identity, rows):
    response = client.post('/api/v1/imports', data={'book_id': identity},
                           files={'file': ('ledger.json', json.dumps(rows).encode(), 'application/json')})
    assert response.status_code == 200, response.text
    return response.json()


def test_full_demo_snapshot_review_and_export(client):
    identity = book(client)
    demo = client.post(f'/api/v1/books/{identity}/demo')
    assert demo.status_code == 200, demo.text
    snap = demo.json()['snapshot_id']
    checks = client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': snap})
    assert checks.status_code == 200, checks.text
    cases = checks.json()['cases']
    assert cases
    case = next((x for x in cases if 'duplicate' in x['rule_id'].lower()), cases[0])
    run = client.post(f'/api/v1/cases/{case["id"]}/investigate', json={'mode': 'rules'})
    assert run.status_code == 200, run.text
    finished = client.get(f'/api/v1/runs/{run.json()["id"]}').json()
    assert finished['status'] == 'complete', finished
    assert finished['usage'].get('calls', 0) == 0
    evidence = client.get(f'/api/v1/cases/{case["id"]}/evidence').json()
    assert evidence and all(x['synthetic'] for x in evidence)
    before = client.get(f'/api/v1/books/{identity}/records').json()
    adjustment = [{'voucher_id': 'SIM1', 'account': '2202', 'debit': '100000.00', 'credit': '0.00',
                   'business_date': '2026-09-30', 'period': '2026-09'},
                  {'voucher_id': 'SIM1', 'account': '1405', 'debit': '0.00', 'credit': '100000.00',
                   'business_date': '2026-09-30', 'period': '2026-09'}]
    sim = client.post('/api/v1/simulations', json={'case_id': case['id'], 'adjustments': adjustment})
    assert sim.status_code == 200, sim.text
    assert sim.json()['balanced']
    assert client.get(f'/api/v1/books/{identity}/records').json() == before
    review = {'case_id': case['id'], 'expected_snapshot_id': snap, 'decision': 'reserved', 'comment': '模拟验证'}
    assert client.post('/api/v1/reviews', json=review).status_code == 200
    report = client.get(f'/api/v1/exports/cases/{case["id"]}?format=json')
    assert report.status_code == 200 and report.json()['evidence']
    supplement = client.post(f'/api/v1/books/{identity}/supplement-demo')
    assert supplement.status_code == 200, supplement.text
    assert client.post('/api/v1/reviews', json=review).status_code == 409
    # Prior snapshot and evidence must not gain late records.
    assert client.get(f'/api/v1/snapshots/{snap}').json()['record_ids'] == sorted(r['id'] for r in before)
    assert client.get(f'/api/v1/cases/{case["id"]}/evidence').json() == evidence


def test_model_and_selector_references_include_other_chains_but_never_future_or_foreign_records(client):
    from app.db import Session, Run
    identity = book(client)
    client.post(f'/api/v1/books/{identity}/demo')
    unrelated = {'source_id': 'unrelated-bank', 'kind': 'bank', 'business_date': '2026-09-03',
                 'period': '2026-09', 'amount': '12.00', 'reference': 'OTHER-CHAIN'}
    imported = upload(client, identity, [unrelated])
    snapshot = client.post(f'/api/v1/imports/{imported["id"]}/confirm').json()['snapshot_id']
    case = client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': snapshot}).json()['cases'][0]
    visible = next(r for r in client.get(f'/api/v1/books/{identity}/records').json() if r['source_id'] == 'unrelated-bank')
    assert visible['id'] not in {r['record_id'] for r in client.get(f'/api/v1/cases/{case["id"]}/evidence').json()}
    foreign_book = book(client)
    foreign_import = upload(client, foreign_book, [{**unrelated, 'source_id': 'foreign-bank'}])
    client.post(f'/api/v1/imports/{foreign_import["id"]}/confirm')
    foreign = client.get(f'/api/v1/books/{foreign_book}/records').json()[0]
    later_import = upload(client, identity, [{**unrelated, 'source_id': 'later-bank'}])
    client.post(f'/api/v1/imports/{later_import["id"]}/confirm')
    later = next(r for r in client.get(f'/api/v1/books/{identity}/records').json() if r['source_id'] == 'later-bank')
    refs = [visible['evidence_id'], foreign['evidence_id'], later['evidence_id']]
    with Session() as session:
        session.add(Run(case_id=case['id'], mode='active', status='complete', result={
            'proposals': [{'claims': [{'evidence_ids': refs}], 'hypotheses': []}],
            'evidence_selections': [{'execution': {'performed': True, 'record_evidence_ids': refs}}],
            'arbitrary_nested_text': {'evidence_ids': [foreign['evidence_id']]}}))
        session.commit()
    before = client.get(f'/api/v1/books/{identity}/records').json()
    result = client.get(f'/api/v1/cases/{case["id"]}/evidence').json()
    returned = {r['id'] for r in result}
    assert visible['evidence_id'] in returned
    assert foreign['evidence_id'] not in returned and later['evidence_id'] not in returned
    export = client.get(f'/api/v1/exports/cases/{case["id"]}?format=json').json()
    assert any(r['id'] == visible['evidence_id'] and r['locator'] for r in export['evidence'])
    assert {r['id'] for r in export['evidence']} == returned
    assert client.get(f'/api/v1/books/{identity}/records').json() == before


def test_invalid_money_missing_and_float_are_quarantined(client):
    identity = book(client)
    row = {'source_id': 'x', 'kind': 'bank', 'business_date': '2026-09-01', 'period': '2026-09'}
    for amount in [None, 'NaN', 'Infinity', '0.001', 1.3]:
        data = upload(client, identity, [{**row, 'amount': amount}])
        assert data['errors']
        assert client.post(f'/api/v1/imports/{data["id"]}/confirm').status_code == 422
    assert client.get(f'/api/v1/books/{identity}/records').json() == []


def test_broken_pdf_returns_validation_error_without_source_or_records(client):
    identity = book(client)
    response = client.post('/api/v1/imports', data={'book_id': identity},
                           files={'file': ('broken.pdf', b'not a PDF', 'application/pdf')})
    assert response.status_code == 422
    assert client.get(f'/api/v1/books/{identity}/documents').json() == []


def test_idempotency_conflict_and_book_scope(client):
    a, b = book(client), book(client, 'SCAS')
    row = {'source_id': 'bank1', 'kind': 'bank', 'business_date': '2026-09-01', 'period': '2026-09', 'amount': '10.00'}
    data = upload(client, a, [row])
    first = client.post(f'/api/v1/imports/{data["id"]}/confirm').json()
    assert first == client.post(f'/api/v1/imports/{data["id"]}/confirm').json()
    data = upload(client, a, [row])
    assert client.post(f'/api/v1/imports/{data["id"]}/confirm').json()['duplicate_count'] == 1
    data = upload(client, a, [{**row, 'amount': '11.00'}])
    assert client.post(f'/api/v1/imports/{data["id"]}/confirm').status_code == 409
    assert len(client.get(f'/api/v1/books/{a}/records').json()) == 1
    assert client.post('/api/v1/check-runs', json={'book_id': b, 'snapshot_id': first['snapshot_id']}).status_code == 403
    assert client.post('/api/v1/books', json={'name': 'evil', 'framework': 'CAS', 'period': '2026-09'},
                       headers={'Origin': 'https://evil.example'}).status_code == 403


def test_no_key_no_paid_mode_and_policy_version(client):
    identity = book(client)
    demo = client.post(f'/api/v1/books/{identity}/demo').json()
    cases = client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': demo['snapshot_id']}).json()['cases']
    assert client.post(f'/api/v1/cases/{cases[0]["id"]}/investigate', json={'mode': 'active'}).status_code == 409
    changed = client.put(f'/api/v1/books/{identity}', json={'name': '新名称', 'framework': 'CAS', 'period': '2026-10'})
    assert changed.status_code == 200 and changed.json()['version'] == 2
    assert client.get(f'/api/v1/cases/{cases[0]["id"]}').json()['stale']
    assert client.put(f'/api/v1/books/{identity}', json={'name': 'x', 'framework': 'SCAS', 'period': '2026-10'}).status_code == 409


def test_frozen_rules_reject_upgrade_but_allow_old_report_and_new_snapshot(client, monkeypatch):
    from app import domain
    identity = book(client)
    snap = client.post(f'/api/v1/books/{identity}/demo').json()['snapshot_id']
    checks = client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': snap}).json()
    case = checks['cases'][0]
    assert client.post('/api/v1/snapshots', json={'book_id': identity}).json()['id'] == snap
    assert client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': snap}).json()['id'] == checks['id']
    monkeypatch.setattr(domain, '_RULE_SOURCE_SHA256', 'changed-implementation')
    assert client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': snap}).status_code == 409
    assert client.post(f'/api/v1/cases/{case["id"]}/investigate', json={'mode': 'rules'}).status_code == 409
    assert client.get(f'/api/v1/cases/{case["id"]}').json()['stale']
    assert client.get(f'/api/v1/exports/cases/{case["id"]}?format=json').status_code == 200
    assert client.post('/api/v1/reviews', json={'case_id': case['id'], 'expected_snapshot_id': snap,
                                              'decision': 'accepted'}).status_code == 409
    fresh = client.post('/api/v1/snapshots', json={'book_id': identity}).json()
    assert fresh['id'] != snap
    assert client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': fresh['id']}).status_code == 200


@pytest.mark.parametrize('framework', ['CAS', 'SCAS'])
def test_common_policy_corpus_survives_snapshot_and_real_rule_investigation(client, tmp_path, monkeypatch, framework):
    from app.config import settings
    from app import knowledge
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    # Independent source bytes and expected extracted text; the fixture does
    # not certify this invented policy as actual Ministry guidance.
    raw_bytes = '<html><body>会计凭证审核与规则追溯。</body></html>'.encode()
    expected_text = '会计凭证审核与规则追溯。'
    from hashlib import sha256
    raw_hash = sha256(raw_bytes).hexdigest()
    source = {'id': 'test-policy', 'kind': 'policy', 'framework': 'BOTH',
              'url': 'https://www.mof.gov.cn/example', 'title': '独立测试来源', 'effective_from': '2025-01-01'}
    registry = tmp_path / 'trusted-sources.json'
    registry.write_text(json.dumps({'sources': [source]}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(knowledge, 'DEFAULT_REGISTRY', registry)
    source_dir = tmp_path / 'public-sources' / 'test-policy'
    source_dir.mkdir(parents=True)
    raw = source_dir / f'{raw_hash}.html'
    raw.write_bytes(raw_bytes)
    parsed = source_dir / 'parsed.json'
    parsed.write_text(json.dumps([{'page': 1, 'text': expected_text}], ensure_ascii=False), encoding='utf-8')
    acquisition = source_dir.parent / 'acquisition-report.json'
    acquisition.write_text(json.dumps([{**source, 'status': 'fetched', 'sha256': raw_hash,
                                       'raw_path': str(raw), 'parsed_path': str(parsed)}]), encoding='utf-8')
    corpus_dir = tmp_path / 'knowledge'
    corpus_dir.mkdir()
    knowledge.build_corpus(acquisition, corpus_dir / 'corpus-v2.json', registry=registry)
    identity = book(client, framework)
    snap = client.post(f'/api/v1/books/{identity}/demo').json()['snapshot_id']
    frozen = client.get(f'/api/v1/snapshots/{snap}').json()
    assert frozen['policy']['reference_passages'][0]['framework'] == 'BOTH'
    assert frozen['policy']['reference_passages'][0]['text'] == expected_text
    assert frozen['policy']['reference_passages'][0]['source_binding']['raw_sha256'] == raw_hash
    case = client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': snap}).json()['cases'][0]
    task = client.post(f'/api/v1/cases/{case["id"]}/investigate', json={'mode': 'rules'}).json()
    result = client.get(f'/api/v1/runs/{task["id"]}').json()
    assert result['status'] == 'complete', result
    assert result['result']['execution_rules'] == frozen['policy']['execution_rules']


def test_evidence_request_candidates_come_from_later_snapshot_and_need_human_confirmation(client):
    identity = book(client)
    demo = client.post(f'/api/v1/books/{identity}/demo').json()
    cases = client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': demo['snapshot_id']}).json()['cases']
    gap = next(c for c in cases if c['rule_id'] == 'RECEIPT_GAP')
    listed = client.get(f"/api/v1/cases/{gap['id']}/evidence-requests").json()
    assert listed['suggestion'] == {'kind': 'receipt', 'rule_id': 'RECEIPT_GAP'} and listed['requests'] == []
    assert listed['raised_needs'] == []
    client.post(f"/api/v1/cases/{gap['id']}/investigate", json={'mode': 'rules'}).raise_for_status()
    raised = client.get(f"/api/v1/cases/{gap['id']}/evidence-requests").json()
    assert raised['raised_needs'] and raised['requests'] == []   # suggestions only; nothing auto-registered
    assert client.post(f"/api/v1/cases/{gap['id']}/evidence-requests",
                       json={'kind': 'receipt', 'reference': 'PO-999', 'description': 'x'}).status_code == 422
    created = client.post(f"/api/v1/cases/{gap['id']}/evidence-requests",
                          json={'kind': 'receipt', 'reference': 'PO-001', 'description': '剩余收货单'})
    assert created.status_code == 201 and created.json()['status'] == 'open' and created.json()['candidates'] == []
    request_id = created.json()['id']
    # The synthetic late receipt arrives through a normal import and a new snapshot.
    client.post(f'/api/v1/books/{identity}/supplement-demo').raise_for_status()
    later = client.post('/api/v1/snapshots', json={'book_id': identity}).json()
    state = client.get(f"/api/v1/cases/{gap['id']}/evidence-requests").json()['requests'][0]
    assert state['status'] == 'open' and state['candidate_snapshot_id'] == later['id']
    assert [c['source_id'] for c in state['candidates']] == ['RCV-002']   # nothing accepted automatically
    wrong = client.post(f'/api/v1/evidence-requests/{request_id}/fulfil',
                        json={'snapshot_id': later['id'], 'record_ids': ['not-a-candidate']})
    assert wrong.status_code == 409
    done = client.post(f'/api/v1/evidence-requests/{request_id}/fulfil',
                       json={'snapshot_id': later['id'], 'record_ids': [state['candidates'][0]['record_id']],
                             'comment': '已核对迟到收货单'})
    assert done.status_code == 200 and done.json()['status'] == 'fulfilled' and done.json()['candidates'] == []
    assert client.post(f'/api/v1/evidence-requests/{request_id}/withdraw', json={'comment': 'x'}).status_code == 409
    other = client.post(f"/api/v1/cases/{gap['id']}/evidence-requests",
                        json={'kind': 'invoice', 'description': '发票原件'}).json()
    withdrawn = client.post(f"/api/v1/evidence-requests/{other['id']}/withdraw", json={'comment': '不再需要'}).json()
    assert withdrawn['status'] == 'withdrawn' and [e['action'] for e in withdrawn['events']] == ['withdrawn']
