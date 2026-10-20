"""Independence, wording-rule and hand-authored accounting checks for fresh v4 data."""
import hashlib
import json
from decimal import Decimal

import pytest

from benchmarks.root_cause_v2 import KEYWORDS
from datasets import generate_v2 as v2
from datasets import generate_v3 as v3
from datasets import generate_v4 as new

OWN_KEYWORDS = {'order_reduced': KEYWORDS['order_change'], 'invoice_voided': KEYWORDS['invoice_voided'],
                'price_adjustment': KEYWORDS['price_adjustment'], 'benign': ()}
ALL_KEYWORDS = {word for words in KEYWORDS.values() for word in words}


def phrases(module):
    return [t for by_split in module.TEXT.values() for texts in by_split.values() for t in texts]


def test_wording_and_topologies_are_new_relative_to_v2_and_v3():
    fresh = phrases(new)
    assert len(fresh) == len(set(fresh)) == 48
    assert not set(phrases(v2)).union(phrases(v3)).intersection(fresh)
    used = set(map(tuple, v2.TOPOLOGIES)) | set(map(tuple, v3.TOPOLOGIES))
    assert not used.intersection(map(tuple, new.TOPOLOGIES))
    assert all(sum(parts) == 100 for parts in new.TOPOLOGIES)


@pytest.mark.parametrize('split', ['validation', 'sealed_test'])
def test_keyword_balance_rule_holds_for_evaluated_splits(split):
    for meaning, own in OWN_KEYWORDS.items():
        texts = new.TEXT[meaning][split]
        with_own = [t for t in texts if any(word in t for word in own)]
        assert len(with_own) == (0 if meaning == 'benign' else 2), (meaning, split)
        others = ALL_KEYWORDS - set(own)
        assert not [t for t in texts if any(word in t for word in others)], (meaning, split)
        assert all(not any(word in t for word in ALL_KEYWORDS) for t in texts if t not in with_own)


def test_no_phrase_carries_another_cause_keyword_in_development():
    for meaning, own in OWN_KEYWORDS.items():
        others = ALL_KEYWORDS - set(own)
        assert not [t for t in new.TEXT[meaning]['development'] if any(word in t for word in others)], meaning


def test_frozen_dataset_is_reproducible_hashed_distinct_and_refuses_overwrite(tmp_path):
    first, second, old = tmp_path / 'first', tmp_path / 'second', tmp_path / 'v3'
    a, b = new.write_dataset(first), new.write_dataset(second)
    assert a['manifest_sha256'] == b['manifest_sha256'] and a['version'] == 'root-cause-v4.0.0'
    manifest = json.loads((first / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['case_count'] == 270
    ids = {entry['case_id'] for entry in manifest['cases']}
    assert len(ids) == 270 and all(case_id.startswith('rc4-c-') for case_id in ids)
    v3.write_dataset(old)
    previous = json.loads((old / 'manifest.json').read_text(encoding='utf-8'))
    assert not ids.intersection(entry['case_id'] for entry in previous['cases'])
    for entry in manifest['cases']:
        for folder, digest in entry['file_sha256'].items():
            data = (first / folder / f"{entry['case_id']}.json").read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest
            if folder != 'truth':
                assert not any(cause.encode() in data for cause in v2.CAUSES)
    with pytest.raises(ValueError, match='overwrite'):
        new.write_dataset(first)


@pytest.mark.parametrize('cause', sorted(v2.CAUSES))
def test_development_records_have_fresh_identity_and_independently_checked_amounts(cause):
    observed, withheld, truth = new.build_case(cause, 'development', 0)
    previous, previous_withheld, _ = v3.build_case(cause, 'development', 0)
    fresh = observed['records'] + withheld
    assert not {r['evidence_id'] for r in fresh}.intersection(
        r['evidence_id'] for r in previous['records'] + previous_withheld)
    assert sum(Decimal(r.get('debit', '0')) for r in observed['records']) == sum(
        Decimal(r.get('credit', '0')) for r in observed['records'])
    # Hand computation: unit 100000 + 58331 = 158331 cents per percent; the last
    # development receipt is 55 percent, so 158331 * 55 / 100 = 87082.05.
    if cause == 'gap_late_document':
        assert withheld[0]['amount'] == '87082.05'
        assert withheld[0]['business_date'] == '2026-09-20'
        assert truth['conclusion_with_evidence'] == 'normal'
    if cause == 'gap_in_transit':
        assert withheld[0]['business_date'] == '2026-10-02'
        assert truth['conclusion_with_evidence'] == 'supported'
    if cause == 'gap_order_reduced':
        assert withheld[0]['amount'] == '-87082.05'
    assert all(r['observed_at'] > '2026-09-30T23:59:59+00:00' for r in withheld)
