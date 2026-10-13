from copy import deepcopy

import pytest

from benchmarks.reference_abstention import calibrate, evaluate, gate, save_new


def sample():
    segments = [{'segment_id': 'a', 'source_id': 'source-a', 'text': 'Independent full anchor'}]
    labels = {'positive': {'source_id': 'source-a', 'anchor': 'Independent full anchor', 'kind': 'answerable'},
              'negative': {'source_id': None, 'anchor': None, 'kind': 'absent_private_data'}}
    cases = [{'query_id': 'positive', 'keyword': [{'segment_id': 'a', 'score': 0.8}]},
             {'query_id': 'negative', 'keyword': [{'segment_id': 'a', 'score': 0.6}]}]
    return cases, labels, segments


def test_threshold_calibration_preserves_correct_hit_and_rejects_absent_question():
    cases, labels, segments = sample()
    before = deepcopy((cases, labels, segments))
    fitted = calibrate(cases, labels, segments, 'keyword')
    assert fitted['threshold'] == 0.8
    metrics, decisions, balanced = evaluate(cases, labels, segments, 'keyword', fitted['threshold'])
    assert balanced == 1 and metrics['correct_hit_at_6_kept'] == 1
    assert metrics['rejected_unanswerable'] == 1 and metrics['accepted_unanswerable'] == 0
    assert [d['accepted'] for d in decisions] == [True, False]
    assert (cases, labels, segments) == before


def test_failure_is_not_a_successful_rejection_and_disallows_calibration():
    cases, labels, segments = sample()
    cases[1].update(keyword=[], keyword_error={'type': 'RuntimeError'})
    metrics, _, balanced = evaluate(cases, labels, segments, 'keyword', 0.7)
    assert metrics['failures'] == 1 and metrics['rejected_unanswerable'] == 0
    assert metrics['unanswerable'] == 1 and balanced == 0.5
    with pytest.raises(ValueError, match='failed development'):
        calibrate(cases, labels, segments, 'keyword')


def test_accepting_wrong_passage_does_not_count_as_correct_retrieval():
    cases, labels, segments = sample()
    segments.append({'segment_id': 'wrong', 'source_id': 'source-b', 'text': 'Different passage'})
    cases[0]['keyword'][0]['segment_id'] = 'wrong'
    metrics, _, _ = evaluate(cases, labels, segments, 'keyword', 0.7)
    assert metrics['accepted_answerable'] == 1 and metrics['correct_hit_at_6_kept'] == 0


def test_equal_scores_cannot_be_split_using_truth_and_tie_prefers_rejection():
    cases, labels, segments = sample()
    cases[1]['keyword'][0]['score'] = 0.8
    fitted = calibrate(cases, labels, segments, 'keyword')
    assert fitted['threshold'] > 0.8
    metrics = fitted['development']
    assert metrics['accepted_answerable'] == 0 and metrics['rejected_unanswerable'] == 1
    assert metrics['correct_hit_at_6_lost'] == 1


@pytest.mark.parametrize('change', [
    {'failures': 1}, {'accepted_unanswerable': 2}, {'correct_hit_at_6_kept': 8},
    {'correct_hit_at_6_lost': 2}, {'answerable': 11}, {'unanswerable': 11}])
def test_gate_requires_coverage_rejection_no_failures_and_fixed_denominators(change):
    passing = {'failures': 0, 'accepted_unanswerable': 1, 'correct_hit_at_6_kept': 9,
               'correct_hit_at_6_lost': 1, 'answerable': 12, 'unanswerable': 12}
    assert gate(passing)
    assert not gate(passing | change)


def test_outputs_refuse_overwrite_and_reject_non_finite_json(tmp_path):
    path = tmp_path / 'plan.json'
    save_new(path, {'frozen': True})
    with pytest.raises(FileExistsError):
        save_new(path, {})
    assert path.read_text(encoding='utf-8').find('true') >= 0
    with pytest.raises(ValueError):
        save_new(tmp_path / 'bad.json', {'score': float('nan')})
