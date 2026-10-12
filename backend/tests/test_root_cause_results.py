"""Read-only root-cause summary API: verified entries, isolated damage, no zero-filling."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import root_cause_results


def client(monkeypatch, path):
    monkeypatch.setattr(root_cause_results, 'SUMMARY', path)
    app = FastAPI()
    app.include_router(root_cause_results.router)
    return TestClient(app)


def run(**changes):
    item = {'id': 'v2-sealed-final', 'label': '最终对比', 'split': 'sealed_test', 'role': 'final', 'validity': 'valid',
            'report': 'r.json', 'report_sha256': 'a' * 64, 'repeat': 2, 'paid_requests': 103,
            'offpeak_cost_cny_estimate': '1.547', 'status': 'completed_with_failures',
            'methods': {'single': {'samples': 18, 'failures': 6, 'category_macro_f1': 0.4375,
                                   'conclusion_accuracy': 0.3889, 'decisive_evidence_obtained': '5/12'}}}
    item.update(changes)
    return item


def test_actual_repository_summary_is_verified_and_keeps_failures():
    data = TestClient(_app()).get('/api/v1/evaluations/root-cause').json()
    assert data['status'] == 'available' and data['runs']
    final = next(r for r in data['runs'] if r['id'] == 'v2-sealed-final')
    assert final['summary_status'] == 'verified' and final['methods']['single']['failures'] == 6
    invalidated = next(r for r in data['runs'] if r['id'] == 'v2-smoke-validation-v24')
    assert invalidated['validity'].startswith('作废')


def _app():
    app = FastAPI()
    app.include_router(root_cause_results.router)
    return app


def test_damaged_entries_are_isolated_not_dropped_or_repaired(monkeypatch, tmp_path):
    path = tmp_path / 'summary.json'
    broken = run(id='broken-run', methods={'single': {'samples': 2, 'failures': 5, 'decisive_evidence_obtained': '1/1'}})
    path.write_text(json.dumps({'schema_version': 'root-cause-results-v1', 'runs': [run(), broken,
                                run(id='bad-ratio', methods={'x': {'samples': 1, 'failures': 0,
                                                                  'decisive_evidence_obtained': '3/2'}})]}),
                    encoding='utf-8')
    data = client(monkeypatch, path).get('/api/v1/evaluations/root-cause').json()
    assert [(r['id'], r['summary_status']) for r in data['runs']] == [
        ('v2-sealed-final', 'verified'), ('broken-run', 'invalid'), ('bad-ratio', 'invalid')]
    assert 'methods' not in data['runs'][1]


def test_missing_or_wrong_summary_is_reported_never_zero_filled(monkeypatch, tmp_path):
    assert client(monkeypatch, tmp_path / 'absent.json').get('/api/v1/evaluations/root-cause').json() == {
        'status': 'unavailable', 'runs': [], 'notice': '原因调查摘要不存在或无法解析；未补零或推断结果。'}
    wrong = tmp_path / 'wrong.json'
    wrong.write_text(json.dumps({'schema_version': 'other', 'runs': []}), encoding='utf-8')
    assert client(monkeypatch, wrong).get('/api/v1/evaluations/root-cause').json()['status'] == 'invalid'
