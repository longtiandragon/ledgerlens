import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import numeric_evaluation as module


def replay():
    return {'schema_version': 'finqa-reference-program-replay-v1',
            'metric_scope': 'arithmetic_execution_of_supplied_official_dev_programs_not_model_QA',
            'source_sha256': 'a' * 64, 'created_at': '2026-10-08T10:00:00+00:00',
            'calculator_version': 'example-v1', 'records': 3, 'executed': 3,
            'exact_matches_at_5_decimal_places': 2, 'model_requests': 0}


def pilot():
    return {'schema_version': 'finqa-numeric-model-pilot-v1', 'source_sha256': 'a' * 64,
            'created_at': '2026-10-08T11:00:00+00:00', 'status': 'completed_with_failures',
            'split': 'dev', 'prompt_version': 'example-v2', 'paid_requests': 4,
            'summary': {'samples': 3, 'scorable': 3, 'unscorable': 0, 'answered': 2,
                        'failed': 1, 'abstained': 0, 'exact_matches': 1,
                        'exact_match_rate': 1/3, 'tokens': 100},
            'private_reasoning': 'PRIVATE_SENTINEL', 'api_key': 'SECRET_SENTINEL',
            'list_price_estimate': {'samples': 3, 'physical_attempts': 4,
                'unknown_attempts': 0, 'complete_cost_coverage': True,
                'known_usage_minimum_usd': '0.01', 'known_usage_maximum_usd': '0.02'}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'REPORTS_DIR', tmp_path)
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app) as client:
        yield client, tmp_path


def save(directory, name, data):
    (directory / name).write_text(json.dumps(data), encoding='utf-8')


def test_history_read_never_exposes_key_reasoning_raw_answers_or_calls_model(client):
    api, directory = client
    save(directory, 'finqa-program-replay-v3-summary.json', replay())
    save(directory, 'finqa-model-pilot-v1-summary.json', pilot())
    first = api.get('/api/v1/evaluations/numeric')
    second = api.get('/api/v1/evaluations/numeric')
    assert first.status_code == 200 and first.json() == second.json()
    assert 'SENTINEL' not in first.text
    actual = first.json()
    assert actual['arithmetic_replay']['exact_matches'] == 2
    model = actual['model_pilots'][0]
    assert model['samples'] == 3 and model['failed'] == 1 and model['exact_match_rate'] == 1/3
    assert model['cost_min_usd'] == '0.01' and model['cost_max_usd'] == '0.02'
    assert len(actual['issues']) == 2
    assert api.post('/api/v1/evaluations/numeric').status_code == 405


@pytest.mark.parametrize('bad_counts', [
    {'exact_matches': 4}, {'samples': True}, {'exact_match_rate': 0.9},
    {'scorable': 2}, {'tokens': -1}, {'failed': 4},
])
def test_invalid_history_is_quarantined_without_adjusting_answers_or_other_reports(client, bad_counts):
    api, directory = client
    bad = pilot()
    bad['summary'].update(bad_counts)
    save(directory, 'finqa-model-pilot-v1-summary.json', bad)
    save(directory, 'finqa-model-pilot-v2-summary.json', pilot())
    actual = api.get('/api/v1/evaluations/numeric').json()
    assert [row['id'] for row in actual['model_pilots']] == ['finqa-v2']
    assert {'report_id': 'finqa-v1', 'code': 'invalid_or_unreadable'} in actual['issues']


def test_unscorable_samples_are_not_given_zero_accuracy(client):
    api, directory = client
    value = pilot()
    value['summary'].update(scorable=0, unscorable=3, exact_matches=0, exact_match_rate=None)
    save(directory, 'finqa-model-pilot-v1-summary.json', value)
    row = api.get('/api/v1/evaluations/numeric').json()['model_pilots'][0]
    assert row['exact_match_rate'] is None and row['scorable'] == 0 and row['unscorable'] == 3
    assert row['paid_requests'] == 4


@pytest.mark.parametrize('mutations', [
    {'complete_cost_coverage': False}, {'unknown_attempts': 1}, {'physical_attempts': 0},
    {'known_usage_minimum_usd': '0.99'}, {'known_usage_maximum_usd': 'SECRET_SENTINEL'},
])
def test_incomplete_invalid_or_wrong_scope_cost_is_unknown_not_zero(client, mutations):
    api, directory = client
    value = pilot()
    value['list_price_estimate'].update(mutations)
    save(directory, 'finqa-model-pilot-v1-summary.json', value)
    row = api.get('/api/v1/evaluations/numeric').json()['model_pilots'][0]
    assert row['cost_min_usd'] is None and row['cost_max_usd'] is None


def test_malformed_large_and_inflight_summaries_are_not_completed_results(client):
    api, directory = client
    (directory / 'finqa-program-replay-v3-summary.json').write_text('{"records":1,"records":2}')
    (directory / 'finqa-model-pilot-v1-summary.json').write_text('x' * 256_001)
    value = pilot()
    value['status'] = 'running'
    save(directory, 'finqa-model-pilot-v2-summary.json', value)
    actual = api.get('/api/v1/evaluations/numeric').json()
    assert actual['arithmetic_replay'] is None and actual['model_pilots'] == []
    assert sum(item['code'] == 'invalid_or_unreadable' for item in actual['issues']) == 3
