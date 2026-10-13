"""Public summary integrity and read-only retrieval evaluation display contract."""
import json
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import retrieval_results as module

BASELINE = 'retrieval-validation.json'
ABSTENTION = 'retrieval-abstention-validation.json'


def client():
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


@pytest.fixture
def summaries(monkeypatch, tmp_path):
    for filename in (BASELINE, ABSTENTION):
        shutil.copyfile(module.DOCS / filename, tmp_path / filename)
    monkeypatch.setattr(module, 'DOCS', tmp_path)
    return tmp_path


def edit(path, change):
    raw = json.loads(path.read_text(encoding='utf-8'))
    change(raw)
    path.write_text(json.dumps(raw), encoding='utf-8')


def test_repository_summaries_show_negative_results_and_read_no_other_files(summaries, monkeypatch):
    from pathlib import Path
    original = Path.open
    reads = []

    def open_summary(self, mode='r', *args, **kwargs):
        assert self.parent == summaries and self.name in (BASELINE, ABSTENTION) and mode == 'rb'
        reads.append(self.name)
        return original(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_summary)
    response = client().get('/api/v1/evaluations/retrieval')
    assert response.status_code == 200
    data = response.json()
    assert reads == [BASELINE, ABSTENTION]
    assert all(data[key]['status'] == 'available' for key in ('baseline', 'abstention'))
    assert [r['correct_hit_at_6'] for r in data['baseline']['rows']] == [16, 15]
    assert [r['returned_unanswerable'] for r in data['baseline']['rows']] == [2, 2]
    assert [r['correct_hit_at_6_kept'] for r in data['abstention']['rows']] == [11, 10]
    assert all(r['accepted_unanswerable'] == 2 and r['candidate_meets_gate'] is False for r in data['abstention']['rows'])
    assert 'query_id' not in response.text and 'decisions' not in response.text


@pytest.mark.parametrize('filename,key', [(BASELINE, 'baseline'), (ABSTENTION, 'abstention')])
@pytest.mark.parametrize('fault', ['missing', 'json', 'duplicate', 'oversize', 'utf8', 'deep'])
def test_unreadable_summary_is_isolated(summaries, filename, key, fault):
    path = summaries / filename
    if fault == 'missing':
        path.unlink()
    else:
        content = {'json': b'{', 'duplicate': b'{"status":"completed","status":"completed"}',
                   'oversize': b' ' * 1_000_001, 'utf8': b'\xff', 'deep': b'[' * 2000 + b']' * 2000}[fault]
        path.write_bytes(content)
    data = client().get('/api/v1/evaluations/retrieval').json()
    assert data[key]['status'] == ('unavailable' if fault == 'missing' else 'invalid')
    assert 'rows' not in data[key]
    assert data['abstention' if key == 'baseline' else 'baseline']['status'] == 'available'


@pytest.mark.parametrize('fault', ['gate', 'metrics', 'duplicate', 'missing', 'bool_count', 'nan', 'method', 'scope', 'default', 'labels'])
def test_abstention_tampering_cannot_hide_negative_result(summaries, fault):
    def change(raw):
        method = raw['methods']['keyword']
        if fault == 'gate':
            method['candidate_meets_gate'] = True
        elif fault == 'metrics':
            method['heldout']['accepted_unanswerable'] = 0
        elif fault == 'duplicate':
            method['decisions'][0] = method['decisions'][1]
        elif fault == 'missing':
            method['decisions'].pop()
        elif fault == 'bool_count':
            method['heldout']['failures'] = False
        elif fault == 'nan':
            method['threshold'] = float('nan')
        elif fault == 'method':
            del raw['methods']['bge_numpy']
        elif fault == 'scope':
            raw['engine_scope'] = 'PostgreSQL'
        elif fault == 'default':
            raw['default_changed'] = True
        elif fault == 'labels':
            method['decisions'][0]['query_id'] = 'other-query'
    edit(summaries / ABSTENTION, change)
    data = client().get('/api/v1/evaluations/retrieval').json()
    assert data['abstention']['status'] == 'invalid' and 'rows' not in data['abstention']


@pytest.mark.parametrize('fault', ['hit', 'returned', 'rank', 'pg', 'duplicate'])
def test_baseline_inconsistent_counts_are_invalid(summaries, fault):
    def change(raw):
        if fault == 'hit':
            raw['totals']['keyword']['hit_at_6'] = 1.0
        elif fault == 'returned':
            raw['totals']['keyword']['no_answer_returned_candidates'] = 0
        elif fault == 'rank':
            raw['cases'][0]['keyword_scores']['first_relevant_rank'] = None
        elif fault == 'pg':
            raw['pg_numpy_verified_queries'] = 0
        elif fault == 'duplicate':
            raw['cases'][0] = raw['cases'][1]
    edit(summaries / BASELINE, change)
    data = client().get('/api/v1/evaluations/retrieval').json()
    assert data['baseline']['status'] == 'invalid' and 'rows' not in data['baseline']


def test_failed_query_is_not_counted_as_correct_rejection(summaries):
    def change(raw):
        method = raw['methods']['keyword']
        decision = next(d for d in method['decisions'] if d['no_answer'] and not d['accepted'])
        decision['failed'] = True
        method['heldout'].update(failures=1, rejected_unanswerable=9,
                                 balanced_answerability_accuracy=(11 / 12 + 9 / 12) / 2)
    edit(summaries / ABSTENTION, change)
    data = client().get('/api/v1/evaluations/retrieval').json()['abstention']
    assert data['status'] == 'available'
    row = data['rows'][0]
    assert row['unanswerable'] == 12 and row['rejected_unanswerable'] == 9
    assert row['failures'] == 1 and row['candidate_meets_gate'] is False


def test_endpoint_does_not_accept_mutations():
    assert client().post('/api/v1/evaluations/retrieval', json={}).status_code == 405
