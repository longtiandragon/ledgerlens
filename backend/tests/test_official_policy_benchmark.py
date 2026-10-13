"""Failure-sensitive evaluation tests with hand-authored source bindings.

Mocked archive verification is a unit-test boundary, not source authenticity
evidence. The final local integration test invokes the actual archive verifier.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app import policy_consistency as engine
from benchmarks import official_policy_consistency as benchmark


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = 'mof-qa-3861813'
SOURCE_SHA = 'c2a124f48ff46df0807a46da04de6ceaba341a9401e0d12318ed7a76535d4837'
SOURCE_URL = 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/chzzsswd/202301/t20230103_3861813.htm'
SELECTED_IDS = {'mof-qa-3861813', 'mof-qa-3762834', 'mof-qa-3962191',
                'mof-qa-3664183', 'mof-qa-3664190', 'mof-qa-3634036', 'mof-qa-3819697', 'mof-qa-3896761',
                'mof-qa-3819699', 'mof-qa-3819702', 'mof-qa-3762844', 'mof-qa-3691864', 'mof-qa-3691861',
                'mof-qa-3762833', 'mof-qa-3634031', 'mof-qa-3634035', 'mof-qa-3691863', 'mof-qa-3664191', 'mof-qa-3664186', 'mof-qa-3634029',
                'mof-qa-3634037', 'mof-qa-3664187', 'mof-qa-3691695', 'mof-qa-3717408', 'mof-qa-3895086', 'mof-qa-3895087', 'mof-qa-3946594', 'mof-qa-3962188', 'mof-qa-3962193', 'mof-qa-3991864', 'mof-qa-3991866', 'mof-qa-3994211'}


def handwritten_case(identity='independent-inventory'):
    names = ['ordinary_course', 'held_for_sale', 'economic_benefits_probable', 'cost_reliably_measurable']
    observation = {'observation_id': identity, 'book_id': 'book-unit', 'snapshot_id': 'snapshot-unit',
        'topic': 'resale_inventory', 'conditions': {name: {'value': 'yes', 'confirmation': 'human_confirmed',
        'evidence_ids': ['ev-business']} for name in names}, 'reported_treatment': 'inventory',
        'treatment_evidence_ids': ['ev-treatment']}
    context = {'book_id': 'book-unit', 'snapshot_id': 'snapshot-unit', 'framework': 'CAS', 'period': '2026-10',
        'as_of': '2026-10-02T08:00:00+00:00', 'evidence': [{'evidence_id': identity,
        'book_id': 'book-unit', 'snapshot_id': 'snapshot-unit', 'known_at': '2026-10-01T08:00:00+00:00'}
        for identity in ('ev-business', 'ev-treatment')]}
    return {'id': identity, 'group': 'anchor', 'source_id': SOURCE_ID, 'observation': observation,
            'context': context, 'expected': {'status': 'normal', 'expected_treatment': 'inventory'}}


@pytest.fixture
def mocked_archive(tmp_path, monkeypatch):
    collection = tmp_path / 'archive'
    collection.mkdir()
    rows = [{'id': SOURCE_ID, 'status': 'fetched', 'sha256': SOURCE_SHA, 'url': SOURCE_URL,
             'published_at': '2023-01-03', 'question_sha256': '1' * 64, 'answer_sha256': '2' * 64}]
    verification = {'checked': 1, 'passed': 1, 'failed': 0, 'results': [{'id': SOURCE_ID, 'passed': True}]}
    cases = [handwritten_case()]
    monkeypatch.setattr(benchmark, 'verify_local_collection', lambda *args: deepcopy(verification))
    monkeypatch.setattr(benchmark.official_policy_cases, 'cases', lambda: cases)

    def run():
        (collection / 'acquisition-report.json').write_text(json.dumps(rows), encoding='utf-8')
        return benchmark.evaluate(manifest=tmp_path / 'manifest.json', collection=collection)

    return {'rows': rows, 'verification': verification, 'cases': cases, 'run': run}


def test_correct_handwritten_projection_passes_all_independent_evaluation_gates(mocked_archive):
    report = mocked_archive['run']()
    assert (report['tasks'], report['passed'], report['failed']) == (1, 1, 0)
    assert all(report['results'][0]['checks'].values())
    assert report['scope'] == 'hand_authored_structured_archived_QA_developer_consistency_not_business_accuracy'
    assert report['model_requests'] == report['actual_ledger_records_read'] == report['ledger_writes'] == 0


def test_failed_archive_verification_does_not_call_engine_or_disappear(mocked_archive, monkeypatch):
    mocked_archive['verification'].update(passed=0, failed=1, results=[{'id': SOURCE_ID, 'passed': False}])
    calls = []
    monkeypatch.setattr(engine, 'evaluate_policy', lambda *args: calls.append(args))
    report = mocked_archive['run']()
    assert calls == []
    assert (report['tasks'], report['passed'], report['failed']) == (1, 0, 1)
    assert report['results'][0]['error']['type'] == 'ValueError'
    assert 'verification' in report['results'][0]['error']['message']


@pytest.mark.parametrize('field,bad', [('sha256', '0' * 64), ('url', 'https://example.org/not-mof'),
                                     ('published_at', '2023-01-04')])
def test_tampered_archived_binding_rejected_before_engine(mocked_archive, monkeypatch, field, bad):
    mocked_archive['rows'][0][field] = bad
    calls = []
    monkeypatch.setattr(engine, 'evaluate_policy', lambda *args: calls.append(args))
    report = mocked_archive['run']()
    assert calls == []
    assert (report['tasks'], report['passed'], report['failed']) == (1, 0, 1)
    assert 'bound' in report['results'][0]['error']['message']


def test_uncompiled_source_is_a_failed_task_in_the_denominator(mocked_archive):
    mocked_archive['cases'][0]['source_id'] = 'mof-qa-unimplemented'
    report = mocked_archive['run']()
    assert (report['tasks'], report['passed'], report['failed']) == (1, 0, 1)
    assert report['results'][0]['error']['type'] == 'ValueError'


@pytest.mark.parametrize('error_type', [ValueError, TimeoutError, RuntimeError])
def test_engine_exceptions_are_retained_as_failures(mocked_archive, monkeypatch, error_type):
    def fail(*args):
        raise error_type('independent injected failure')

    monkeypatch.setattr(engine, 'evaluate_policy', fail)
    report = mocked_archive['run']()
    assert (report['tasks'], report['passed'], report['failed']) == (1, 0, 1)
    assert report['results'][0]['error']['type'] == error_type.__name__


@pytest.mark.parametrize('alteration,failed_gate', [
    ('wrong_status', 'literal_expected_result'), ('wrong_treatment', 'literal_expected_result'),
    ('wrong_source', 'archived_source_binding'), ('wrong_pack', 'fixed_pack_identity'),
    ('foreign_citation', 'snapshot_citations'), ('missing_citation', 'snapshot_citations'),
    ('empty_citations', 'snapshot_citations'), ('duplicate_citation', 'snapshot_citations'),
])
def test_plausible_but_wrong_outputs_fail_the_specific_gate(mocked_archive, monkeypatch, alteration, failed_gate):
    original = engine.evaluate_policy

    def altered(observation, context):
        result = original(observation, context)
        if alteration == 'wrong_status':
            result['status'] = 'supported'
        elif alteration == 'wrong_treatment':
            result['expected_treatment'] = 'fixed_asset'
        elif alteration == 'wrong_source':
            result['source']['source_sha256'] = '0' * 64
        elif alteration == 'wrong_pack':
            result['policy_rule_identity']['source_sha256'] = '0' * 64
        elif alteration == 'foreign_citation':
            result['evidence_ids'].append('outside-snapshot')
        elif alteration == 'missing_citation':
            result['evidence_ids'].pop()
        elif alteration == 'empty_citations':
            result['evidence_ids'] = []
        else:
            result['evidence_ids'].append(result['evidence_ids'][0])
        return result

    monkeypatch.setattr(engine, 'evaluate_policy', altered)
    report = mocked_archive['run']()
    assert (report['tasks'], report['passed'], report['failed']) == (1, 0, 1)
    assert report['results'][0]['checks'][failed_gate] is False


def test_changed_parsed_inputs_cannot_pass_after_correct_output(mocked_archive, monkeypatch):
    original = engine.evaluate_policy

    def mutate(observation, context):
        result = original(observation, context)
        observation.treatment_evidence_ids.append('ev-business')
        return result

    monkeypatch.setattr(engine, 'evaluate_policy', mutate)
    report = mocked_archive['run']()
    assert report['failed'] == 1
    assert report['results'][0]['checks']['parsed_inputs_unchanged'] is False
    assert report['results'][0]['checks']['literal_expected_result'] is True


def test_changed_original_projection_cannot_pass_after_correct_output(mocked_archive, monkeypatch):
    original = engine.evaluate_policy

    def mutate(observation, context):
        result = original(observation, context)
        mocked_archive['cases'][0]['observation']['reported_treatment'] = 'fixed_asset'
        return result

    monkeypatch.setattr(engine, 'evaluate_policy', mutate)
    report = mocked_archive['run']()
    assert report['failed'] == 1
    assert report['results'][0]['checks']['original_projection_unchanged'] is False
    assert report['results'][0]['checks']['literal_expected_result'] is True


def test_invalid_contract_is_failed_before_engine(mocked_archive, monkeypatch):
    mocked_archive['cases'][0]['observation']['conditions']['ordinary_course']['value'] = True
    calls = []
    monkeypatch.setattr(engine, 'evaluate_policy', lambda *args: calls.append(args))
    report = mocked_archive['run']()
    assert calls == []
    assert report['failed'] == 1
    assert report['results'][0]['error']['type'] == 'ValidationError'


def test_outside_snapshot_context_is_a_failure_not_an_unsupported_success(mocked_archive):
    mocked_archive['cases'][0]['context']['evidence'][0]['snapshot_id'] = 'foreign-snapshot'
    report = mocked_archive['run']()
    assert report['failed'] == 1
    assert report['results'][0]['error']['type'] == 'ValidationError'


@pytest.mark.parametrize('bad_output', [{}, None, {'status': 'normal'}])
def test_empty_or_incomplete_output_does_not_become_success(mocked_archive, monkeypatch, bad_output):
    monkeypatch.setattr(engine, 'evaluate_policy', lambda *args: deepcopy(bad_output))
    report = mocked_archive['run']()
    assert (report['tasks'], report['passed'], report['failed']) == (1, 0, 1)
    assert 'error' in report['results'][0]


def test_mixed_failures_keep_every_task_and_group_in_denominator(mocked_archive, monkeypatch):
    normal = handwritten_case('normal')
    wrong = handwritten_case('wrong')
    wrong['group'] = 'contrast'
    thrown = handwritten_case('thrown')
    thrown['group'] = 'contrast'
    malformed = handwritten_case('malformed')
    malformed['observation']['conditions']['cost_reliably_measurable']['value'] = 0
    mocked_archive['cases'][:] = [normal, wrong, thrown, malformed]
    original = engine.evaluate_policy

    def varied(observation, context):
        if observation.observation_id == 'thrown':
            raise RuntimeError('failed work remains in denominator')
        result = original(observation, context)
        if observation.observation_id == 'wrong':
            result['expected_treatment'] = 'period_expense'
        return result

    monkeypatch.setattr(engine, 'evaluate_policy', varied)
    report = mocked_archive['run']()
    assert (report['tasks'], report['passed'], report['failed']) == (4, 1, 3)
    assert len(report['results']) == 4
    assert report['groups'] == {'anchor': {'tasks': 2, 'passed': 1}, 'contrast': {'tasks': 2, 'passed': 0}}
    assert report['frameworks']['CAS'] == {'tasks': 4, 'passed': 1}
    assert report['expected_status_distribution'] == {'normal': 4}


def test_all_failed_tasks_do_not_shrink_to_an_empty_success(mocked_archive, monkeypatch):
    mocked_archive['cases'][:] = [handwritten_case('failed-' + str(i)) for i in range(3)]

    def fail(*args):
        raise RuntimeError('all fail independently')

    monkeypatch.setattr(engine, 'evaluate_policy', fail)
    report = mocked_archive['run']()
    assert (report['tasks'], report['passed'], report['failed']) == (3, 0, 3)
    assert report['groups']['anchor'] == {'tasks': 3, 'passed': 0}
    assert len(report['results']) == 3


LOCAL_ARCHIVE = ROOT / '.local/official-cases'
LOCAL_ARCHIVE_PRESENT = (LOCAL_ARCHIVE / 'acquisition-report.json').is_file() and all(
    (LOCAL_ARCHIVE / 'raw' / identity).is_dir() for identity in SELECTED_IDS)


@pytest.mark.skipif(not LOCAL_ARCHIVE_PRESENT, reason='Original official HTML stays in optional local storage')
def test_actual_six_source_benchmark_reverifies_archive_and_all_thirty_projections():
    report = benchmark.evaluate()
    assert (report['tasks'], report['passed'], report['failed']) == (191, 191, 0)
    assert report['selected_official_source_count'] == 32
    assert report['collected_source_count'] == 32
    assert report['unimplemented_source_count'] == 0
    assert report['source_verification']['failed'] == 0
    assert {item['source_id'] for item in report['results']} == SELECTED_IDS
    assert all(all(item['checks'].values()) for item in report['results'])
    assert report['model_requests'] == report['ledger_writes'] == 0
