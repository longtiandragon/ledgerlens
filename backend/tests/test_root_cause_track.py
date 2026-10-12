"""Independent checks of the root-cause track: hand-written truth rows only."""
import pytest

from benchmarks.root_cause import CATEGORIES, FAULT_CAUSES, expected, release, score


def truth(fault, rules=(), family='family-x'):
    return {'fault_mechanism': fault, 'expected_rules': list(rules), 'family_id': family}


@pytest.mark.parametrize('fault,rules,released,conclusion', [
    ('normal', [], False, 'normal'),
    ('reversal', [], False, 'normal'),
    ('duplicate', ['DUPLICATE_INVOICE'], False, 'supported'),
    ('late_receipt', ['RECEIPT_GAP'], False, 'insufficient'),   # withheld: cannot conclude
    ('late_receipt', ['RECEIPT_GAP'], True, 'normal'),          # obtained: a normal late document
    ('duplicate_late', ['DUPLICATE_INVOICE', 'RECEIPT_GAP'], True, 'supported'),
    ('conflict_late', ['INVOICE_CONFLICT', 'RECEIPT_GAP'], True, 'insufficient'),
    ('period', ['PERIOD_CUTOFF'], False, 'insufficient'),
    ('partial_booking', [], False, 'supported'),
])
def test_expected_conclusion_depends_on_the_evidence_state_reached(fault, rules, released, conclusion):
    assert expected(truth(fault, rules), released=released)['conclusion'] == conclusion


def test_causes_are_fixed_by_truth_not_by_what_the_method_obtained():
    assert expected(truth('late_receipt', ['RECEIPT_GAP']), released=False)['categories'] == ['late_document']
    assert expected(truth('late_receipt', ['RECEIPT_GAP']), released=True)['categories'] == ['late_document']


def test_indeterminate_family_is_reported_separately_and_unknown_faults_fail_loudly():
    assert expected(truth('indeterminate', ['RECEIPT_GAP']), released=True) == {
        'categories': None, 'conclusion': 'insufficient', 'main_metric': False}
    with pytest.raises(ValueError):
        expected(truth('unmapped_new_fault'), released=False)


def test_every_mapped_cause_is_in_the_visible_taxonomy():
    assert set().union(*FAULT_CAUSES.values()) <= set(CATEGORIES)


def test_release_requires_a_structured_matching_kind_and_reference():
    withheld = [{'id': 'r2', 'kind': 'receipt', 'order_id': 'PO-001', 'reference': 'RCV-002'}]
    assert release(withheld, [{'kind': 'invoice'}]) == []
    assert release(withheld, [{'description': '请提供收货单'}]) == []          # free text releases nothing
    assert release(withheld, [{'kind': 'receipt', 'reference': 'PO-999'}]) == []
    assert release(withheld, [{'kind': 'receipt', 'reference': 'PO-001'}]) == withheld
    assert release(withheld, [{'kind': 'receipt'}, {'kind': 'receipt'}]) == withheld  # no duplicates


def test_score_counts_failures_in_the_denominator_and_rewards_obtained_evidence():
    late = truth('late_receipt', ['RECEIPT_GAP'], 'f1')
    rows = [
        {'family_id': 'f1', 'truth': late, 'released': True,
         'prediction': {'categories': ['late_document'], 'conclusion': 'normal', 'requests': [{'kind': 'receipt'}]}},
        {'family_id': 'f1', 'truth': late, 'released': False,
         'prediction': {'categories': [], 'conclusion': 'insufficient', 'requests': []}},
        {'family_id': 'f2', 'truth': truth('duplicate', ['DUPLICATE_INVOICE'], 'f2'), 'released': False,
         'prediction': {'error': {'code': 'budget_exhausted'}, 'categories': ['duplicate_posting'],
                        'conclusion': 'supported'}},
    ]
    result = score(rows)
    assert result['samples'] == 3 and result['failures'] == 1
    assert result['conclusion_accuracy'] == 2 / 3          # both late rows correct for their state; failure wrong
    assert result['per_category_f1']['late_document'] == 2 / 3    # tp=1, fn=1
    assert result['per_category_f1']['duplicate_posting'] == 0.0  # a failed sample earns nothing
    assert result['withheld_evidence_cases'] == 2 and result['withheld_evidence_obtained'] == 1
