import pytest

from benchmarks.evaluate import acceptance_failures


def summary(**changes):
    # Handwritten shape of a passing summary; values are not taken from run_evaluation().
    value = {'case_count': 480, 'failures': 0, 'exact_label_match_rate': 1.0, 'model_calls': 0,
             'reference_cases': {'case_count': 30, 'passed': 30}}
    return value | changes


def test_existing_480_and_30_contract_passes():
    assert acceptance_failures(summary()) == []


@pytest.mark.parametrize(('changes', 'failed'), [
    ({'case_count': 479}, ['case_count']),
    ({'failures': 1}, ['failures']),
    ({'exact_label_match_rate': 0.9979}, ['exact_label_match_rate']),
    ({'reference_cases': {'case_count': 30, 'passed': 29}}, ['reference_cases']),
    ({'reference_cases': {'case_count': 29, 'passed': 29}}, ['reference_cases']),
    ({'model_calls': 1}, ['model_calls']),
])
def test_each_contract_violation_is_reported(changes, failed):
    assert acceptance_failures(summary(**changes)) == failed


def test_missing_fields_fail_instead_of_passing_silently():
    assert acceptance_failures({}) == ['case_count', 'failures', 'exact_label_match_rate',
                                       'reference_cases', 'model_calls']
