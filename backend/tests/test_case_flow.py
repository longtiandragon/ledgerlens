"""Case flow through the API: draft -> simulation -> review, and continuation across snapshots."""
import pytest
from fastapi.testclient import TestClient

from conftest import reset_test_database
from app.db import Base, Run, Session, engine
from app.main import app


@pytest.fixture
def client():
    reset_test_database(Base.metadata, engine)
    with TestClient(app) as client:
        yield client


def demo_book(client, framework='CAS'):
    book = client.post('/api/v1/books', json={'name': '流程', 'framework': framework, 'period': '2026-09'}).json()['id']
    snapshot = client.post(f'/api/v1/books/{book}/demo').json()['snapshot_id']
    cases = client.post('/api/v1/check-runs', json={'book_id': book, 'snapshot_id': snapshot}).json()['cases']
    return book, snapshot, {c['rule_id']: c for c in cases}


def test_month_end_cases_cover_purchase_and_funds_packs(client):
    _, _, cases = demo_book(client)
    assert {rule: (case['status'], case['scenario']) for rule, case in cases.items()} == {
        'DUPLICATE_INVOICE': ('supported', 'purchase'), 'RECEIPT_GAP': ('insufficient', 'purchase'),
        # Split payments that pair one way only are a normal funds observation, not ambiguity.
        'BANK_CANDIDATES_ALIGNED': ('normal', 'funds')}


def test_draft_simulation_records_basis_and_review_freezes_open_items(client):
    book, snapshot, cases = demo_book(client)
    case = cases['DUPLICATE_INVOICE']
    before = client.get(f'/api/v1/books/{book}/records').json()
    draft = client.get(f"/api/v1/cases/{case['id']}/treatment-draft").json()
    assert draft['status'] == 'proposed' and draft['case_id'] == case['id'] and draft['snapshot_id'] == snapshot
    opened = client.get(f"/api/v1/cases/{case['id']}").json()
    assert opened['treatment']['digest'] == draft['digest']
    assert [item['kind'] for item in opened['open_items']] == ['treatment_not_simulated']

    tampered = client.post('/api/v1/simulations', json={'case_id': case['id'], 'adjustments': draft['lines'],
                                                         'basis': {'source': 'treatment_draft', 'draft_digest': '0' * 64}})
    assert tampered.status_code == 409

    simulated = client.post('/api/v1/simulations', json={
        'case_id': case['id'], 'adjustments': draft['lines'],
        'basis': {'source': 'treatment_draft', 'draft_digest': draft['digest']}})
    assert simulated.status_code == 200, simulated.text
    sim = simulated.json()
    assert sim['basis']['source'] == 'treatment_draft' and sim['basis']['edited'] is False
    assert sim['basis']['draft_digest'] == draft['digest'] and sim['basis']['summary'] == draft['summary']
    assert sim['target']['status'] == 'resolved' and sim['comparison']['introduced'] == []
    assert client.get(f'/api/v1/books/{book}/records').json() == before   # the source ledger is untouched

    edited_lines = [dict(line) for line in draft['lines']]
    edited_lines[0]['credit'], edited_lines[1]['debit'] = '50000.00', '50000.00'
    edited = client.post('/api/v1/simulations', json={'case_id': case['id'], 'adjustments': edited_lines,
                                                       'basis': {'source': 'treatment_draft', 'draft_digest': draft['digest']}}).json()
    assert edited['basis']['edited'] is True and edited['target']['status'] == 'persisting'
    manual = client.post('/api/v1/simulations', json={'case_id': case['id'], 'adjustments': draft['lines']}).json()
    assert manual['basis'] == {'source': 'manual'}

    # The review freezes the open items of the treatment it looked at.
    partial = client.post('/api/v1/reviews', json={'case_id': case['id'], 'expected_snapshot_id': snapshot,
                                                    'decision': 'reserved', 'comment': '半额不足', 'simulation_id': edited['id']})
    assert partial.status_code == 200, partial.text
    assert [item['kind'] for item in partial.json()['open_items']] == ['target_persisting']
    accepted = client.post('/api/v1/reviews', json={'case_id': case['id'], 'expected_snapshot_id': snapshot,
                                                     'decision': 'accepted', 'comment': '', 'simulation_id': sim['id']}).json()
    assert accepted['open_items'] == [] and accepted['simulation_id'] == sim['id']
    other = demo_book(client)[2]['DUPLICATE_INVOICE']
    foreign = client.post('/api/v1/reviews', json={'case_id': other['id'], 'expected_snapshot_id': other['snapshot_id'],
                                                    'decision': 'accepted', 'simulation_id': sim['id']})
    assert foreign.status_code == 409


def test_failed_investigation_is_an_open_item_not_a_silent_success(client):
    _, snapshot, cases = demo_book(client)
    case = cases['DUPLICATE_INVOICE']
    with Session() as s:
        s.add(Run(case_id=case['id'], mode='single', status='budget_exhausted', result={'status': 'budget_exhausted'}))
        s.commit()
    detail = client.get(f"/api/v1/cases/{case['id']}").json()
    assert 'investigation_incomplete' in [item['kind'] for item in detail['open_items']]
    draft = client.get(f"/api/v1/cases/{case['id']}/treatment-draft").json()
    assert draft['investigation']['status'] == 'budget_exhausted' and draft['investigation']['warning']


def test_new_snapshot_continues_cases_and_follows_up_findings_it_no_longer_shows(client):
    book, snapshot, cases = demo_book(client)
    duplicate, gap = cases['DUPLICATE_INVOICE'], cases['RECEIPT_GAP']
    assert client.post(f"/api/v1/cases/{duplicate['id']}/investigate", json={'mode': 'rules'}).status_code == 200
    request = client.post(f"/api/v1/cases/{gap['id']}/evidence-requests",
                          json={'kind': 'receipt', 'reference': 'PO-001', 'description': '剩余收货单'}).json()
    assert client.get(f"/api/v1/cases/{gap['id']}").json()['lineage']['state'] == 'current'

    later = client.post(f'/api/v1/books/{book}/supplement-demo').json()['snapshot_id']
    stale = client.get(f"/api/v1/cases/{gap['id']}").json()
    assert stale['stale'] and stale['lineage']['state'] == 'not_rechecked'
    recheck = client.post('/api/v1/check-runs', json={'book_id': book, 'snapshot_id': later}).json()['cases']
    rules = sorted(c['rule_id'] for c in recheck)
    assert rules == ['BANK_CANDIDATES_ALIGNED', 'DUPLICATE_INVOICE', 'PRIOR_FINDING_CLEARED']

    continued = next(c for c in recheck if c['rule_id'] == 'DUPLICATE_INVOICE')
    prior = client.get(f"/api/v1/cases/{continued['id']}").json()['lineage']['prior']
    assert [(p['case_id'], p['match'], p['runs']) for p in prior] == [(duplicate['id'], 'same_records', 1)]
    assert client.get(f"/api/v1/cases/{duplicate['id']}").json()['lineage']['state'] == 'continued'

    followup = next(c for c in recheck if c['rule_id'] == 'PRIOR_FINDING_CLEARED')
    assert followup['status'] == 'normal' and followup['scenario'] == 'purchase'
    assert followup['metrics']['prior_case_id'] == gap['id']
    assert client.get(f"/api/v1/cases/{gap['id']}").json()['lineage']['state'] == 'not_reproduced'
    assert client.post(f"/api/v1/cases/{followup['id']}/investigate", json={'mode': 'rules'}).status_code == 409
    detail = client.get(f"/api/v1/cases/{followup['id']}").json()
    assert detail['treatment']['status'] == 'not_applicable'
    assert [(i['kind'], i['case_id'], i['candidates']) for i in detail['open_items']] == [('evidence_request', gap['id'], 1)]

    state = client.get(f"/api/v1/cases/{gap['id']}/evidence-requests").json()['requests'][0]
    assert state['id'] == request['id']
    confirmed = client.post(f"/api/v1/evidence-requests/{request['id']}/fulfil",
                            json={'snapshot_id': state['candidate_snapshot_id'],
                                  'record_ids': [state['candidates'][0]['record_id']], 'comment': '迟到收货单'})
    assert confirmed.status_code == 200
    assert client.get(f"/api/v1/cases/{followup['id']}").json()['open_items'] == []
    review = client.post('/api/v1/reviews', json={'case_id': followup['id'], 'expected_snapshot_id': later,
                                                   'decision': 'accepted', 'comment': '已核对迟到收货单'})
    assert review.status_code == 200 and review.json()['open_items'] == []
    # The original case stays history: it cannot be reviewed on its old snapshot.
    assert client.post('/api/v1/reviews', json={'case_id': gap['id'], 'expected_snapshot_id': snapshot,
                                                'decision': 'accepted'}).status_code == 409
    # Re-checking the same snapshot reuses the run and does not duplicate follow-ups.
    again = client.post('/api/v1/check-runs', json={'book_id': book, 'snapshot_id': later}).json()['cases']
    assert sorted(c['id'] for c in again) == sorted(c['id'] for c in recheck)


def test_workpaper_carries_lineage_requests_treatment_and_frozen_open_items(client):
    book, snapshot, cases = demo_book(client)
    gap = cases['RECEIPT_GAP']
    client.post(f"/api/v1/cases/{gap['id']}/evidence-requests",
                json={'kind': 'receipt', 'reference': 'PO-001', 'description': '剩余收货单'})
    client.post('/api/v1/reviews', json={'case_id': gap['id'], 'expected_snapshot_id': snapshot,
                                         'decision': 'reserved', 'comment': '等待收货单'})
    data = client.get(f"/api/v1/exports/cases/{gap['id']}?format=json").json()
    assert data['treatment_draft']['status'] == 'needs_evidence'
    assert [r['status'] for r in data['evidence_requests']] == ['open']
    assert {item['kind'] for item in data['reviews'][0]['open_items']} == {'evidence_request', 'treatment_pending'}
    assert data['lineage']['state'] == 'current'
    markdown = client.get(f"/api/v1/exports/cases/{gap['id']}").text
    for heading in ('## 接续关系', '## 补证事项', '## 处理草稿与模拟', '## 未决事项（当前）', '## 复核与审批'):
        assert heading in markdown
    assert '复核时未决事项 2 项' in markdown and '剩余收货单（open）' in markdown
