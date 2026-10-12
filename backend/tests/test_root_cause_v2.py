"""Independent checks for the v2 root-cause dataset and evaluator.

Expected values are hand-written from the dataset design, not produced by the
baselines under test.
"""
import json
from pathlib import Path

import pytest

from app.domain import check_records
from benchmarks.root_cause_v2 import KEYWORDS, expected, keyword_request, release, rule_fixed_request, score
from datasets.generate_v2 import CATEGORIES, CAUSES, SPLITS, TEXT, VARIANTS, build_case, write_dataset

SIGNAL = {'duplicate_signal': {'DUPLICATE_INVOICE'}, 'gap_signal': {'RECEIPT_GAP'}, 'no_signal': set()}


def signal(observed):
    return {f['rule_id'] for f in check_records(observed['records'], observed['policy'])
            if f['status'] in {'supported', 'insufficient'}}


@pytest.mark.parametrize('cause', sorted(CAUSES))
@pytest.mark.parametrize('split', SPLITS)
def test_every_cause_in_a_group_shows_exactly_the_same_rule_signal(cause, split):
    group = CAUSES[cause][0]
    for variant in (0, 3, 7):
        observed, _, _ = build_case(cause, split, variant)
        assert signal(observed) == SIGNAL[group]


@pytest.mark.parametrize('cause', sorted(CAUSES))
def test_decisive_evidence_exists_in_the_withheld_set_and_is_dated_later(cause):
    observed, withheld, truth = build_case(cause, 'validation', 2)
    assert {r['kind'] for r in withheld} >= set(truth['decisive_kinds'])
    assert all(r['observed_at'] > max(x['observed_at'] for x in observed['records']) for r in withheld)


def test_releasing_decisive_evidence_resolves_the_gap_by_cause():
    for cause, closes in [('gap_late_document', True), ('gap_in_transit', True), ('gap_order_reduced', True),
                          ('gap_undeterminable', False)]:
        observed, withheld, _ = build_case(cause, 'development', 1)
        assert ('RECEIPT_GAP' not in signal({**observed, 'records': observed['records'] + withheld})) is closes
    _, withheld, _ = build_case('gap_in_transit', 'development', 1)
    assert withheld[0]['business_date'] > '2026-09-30'  # transit: dated after the period
    _, withheld, _ = build_case('gap_late_document', 'development', 1)
    assert withheld[0]['business_date'] <= '2026-09-30'  # late document: goods were received in period


def test_splits_never_share_wording_and_development_keywords_come_from_development():
    for meaning, by_split in TEXT.items():
        sets = [set(by_split[split]) for split in SPLITS]
        assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]), meaning
    development = ' '.join(t for by_split in TEXT.values() for t in by_split['development'])
    assert all(any(word in development for word in words) for words in KEYWORDS.values())


def test_observed_files_carry_no_cause_and_identifiers_are_opaque(tmp_path):
    write_dataset(tmp_path)
    manifest = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['case_count'] == len(CAUSES) * len(SPLITS) * VARIANTS
    for path in (tmp_path / 'observed').iterdir():
        text = path.read_text(encoding='utf-8')
        assert not any(cause in text for cause in CAUSES), path.name
    assert set().union(*(set(c[1]) for c in CAUSES.values())) <= set(CATEGORIES)


def test_release_is_structured_bounded_and_explicitly_empty_on_no_match():
    withheld = [{'kind': 'invoice', 'invoice_id': 'INV-002', 'order_id': 'PO-001', 'reference': 'INV-002'}]
    assert release(withheld, None) == [] and release(withheld, {'kind': 'note'}) == []
    assert release(withheld, {'description': '请提供发票'}) == []
    assert release(withheld, {'kind': 'invoice', 'reference': 'PO-999'}) == []
    assert release(withheld, {'kind': 'invoice', 'reference': 'PO-001'}) == withheld


def test_expected_conclusion_follows_whether_decisive_evidence_was_obtained():
    _, withheld, truth = build_case('gap_order_reduced', 'validation', 0)
    assert expected(truth, [])['conclusion'] == 'insufficient'
    assert expected(truth, [{'kind': 'receipt'}])['conclusion'] == 'insufficient'  # wrong kind is not decisive
    assert expected(truth, withheld) == {'categories': ['order_change'], 'conclusion': 'normal',
                                         'decisive_obtained': True}


def test_fixed_protocol_cannot_reach_an_order_change_but_keyword_reading_can_on_its_wording():
    observed, withheld, _ = build_case('gap_order_reduced', 'development', 0)  # variant 0 carries a note
    fixed = rule_fixed_request(observed['records'], observed['policy'], withheld)
    assert fixed['request'] == {'kind': 'receipt'} and fixed['released'] == [] and fixed['conclusion'] == 'insufficient'
    keyword = keyword_request(observed['records'], observed['policy'], withheld)
    assert keyword['request'] == {'kind': 'order'} and keyword['categories'] == ['order_change']


def test_score_counts_failures_and_false_positive_guesses():
    _, _, undeterminable = build_case('gap_undeterminable', 'development', 0)
    _, withheld, late = build_case('gap_late_document', 'development', 0)
    rows = [
        {'family_id': 'a', 'truth': undeterminable, 'released': [],
         'prediction': {'categories': ['late_document'], 'conclusion': 'insufficient', 'request': {'kind': 'receipt'}}},
        {'family_id': 'b', 'truth': late, 'released': withheld,
         'prediction': {'categories': ['late_document'], 'conclusion': 'normal', 'request': {'kind': 'receipt'}}},
        {'family_id': 'b', 'truth': late, 'released': [],
         'prediction': {'error': {'code': 'budget_exhausted'}}},
    ]
    result = score(rows, CATEGORIES)
    assert result['failures'] == 1 and result['conclusion_accuracy'] == round(2 / 3, 4)
    assert result['false_positive_categories'] == {'late_document': 1}   # a guess on an undeterminable case
    assert result['per_category_f1'] == {'late_document': 0.5}          # tp=1, fp=1, fn=1
    assert result['decisive_evidence_obtained'] == '1/2'


def test_dataset_on_disk_matches_a_fresh_generation_when_present():
    disk = Path('.local/datasets-v2/manifest.json')
    if not disk.exists():
        pytest.skip('local v2 dataset not generated')
    observed, _, _ = build_case('dup_true', 'development', 0)
    manifest = json.loads(disk.read_text(encoding='utf-8'))
    assert observed['case_id'] in {c['case_id'] for c in manifest['cases']}


def test_reference_to_a_visible_document_of_the_same_order_chain_releases():
    """v2 smoke (validation): natural references like INV-001 released nothing."""
    visible = [{'kind': 'invoice', 'invoice_id': 'INV-001', 'order_id': 'PO-001', 'reference': 'INV-001'},
               {'kind': 'receipt', 'order_id': 'PO-001', 'reference': 'RCV-001', 'source_id': 'RCV-001'},
               {'kind': 'order', 'order_id': 'PO-777', 'reference': 'PO-777'}]
    replacement = {'kind': 'invoice', 'invoice_id': 'INV-001-R', 'order_id': 'PO-001', 'reference': 'INV-001-R'}
    late = {'kind': 'receipt', 'order_id': 'PO-001', 'reference': 'RCV-002', 'source_id': 'RCV-002'}
    assert release([replacement], {'kind': 'invoice', 'reference': 'INV-001'}, visible) == [replacement]
    assert release([late], {'kind': 'receipt', 'reference': 'RCV-001'}, visible) == [late]
    assert release([late], {'kind': 'receipt', 'reference': 'PO-777'}, visible) == []   # other chain
    assert release([late], {'kind': 'receipt', 'reference': 'UNKNOWN'}, visible) == []  # not visible
    assert release([late], {'kind': 'invoice', 'reference': 'RCV-001'}, visible) == []  # kind must match
    assert release([late], {'kind': 'receipt', 'reference': 'RCV-001'}) == []           # no visible context
