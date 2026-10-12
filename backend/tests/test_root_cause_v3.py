"""Structural independence and hand-authored accounting checks for fresh v3 data."""
import hashlib
import json
from decimal import Decimal

import pytest

from datasets import generate_v2 as old
from datasets import generate_v3 as new


def test_new_wording_and_topologies_do_not_reuse_any_v2_split():
    old_text = {t for by_split in old.TEXT.values() for texts in by_split.values() for t in texts}
    new_text = [t for by_split in new.TEXT.values() for texts in by_split.values() for t in texts]
    assert len(new_text) == len(set(new_text)) == 48
    assert not old_text.intersection(new_text)
    assert not set(map(tuple, old.TOPOLOGIES)).intersection(map(tuple, new.TOPOLOGIES))
    assert all(sum(parts) == 100 for parts in new.TOPOLOGIES)


def test_frozen_dataset_is_reproducible_hashed_and_refuses_overwrite(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    a, b = new.write_dataset(first), new.write_dataset(second)
    assert a['manifest_sha256'] == b['manifest_sha256']
    manifest = json.loads((first / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['case_count'] == 270
    assert len({entry['case_id'] for entry in manifest['cases']}) == 270
    for entry in manifest['cases']:
        for folder, digest in entry['file_sha256'].items():
            data = (first / folder / f"{entry['case_id']}.json").read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest
            if folder != 'truth':
                assert not any(cause.encode() in data for cause in old.CAUSES)
    with pytest.raises(ValueError, match='overwrite'):
        new.write_dataset(first)


@pytest.mark.parametrize('cause', sorted(old.CAUSES))
def test_development_records_have_fresh_identity_and_independently_checked_amounts(cause):
    observed, withheld, truth = new.build_case(cause, 'development', 0)
    previous, previous_withheld, _ = old.build_case(cause, 'development', 0)
    fresh = observed['records'] + withheld
    assert not {r['evidence_id'] for r in fresh}.intersection(
        r['evidence_id'] for r in previous['records'] + previous_withheld)
    assert sum(Decimal(r.get('debit', '0')) for r in observed['records']) == sum(
        Decimal(r.get('credit', '0')) for r in observed['records'])
    if cause == 'gap_late_document':
        assert withheld[0]['amount'] == '82822.35'  # 127419 cents * 65 / 100
        assert withheld[0]['business_date'] == '2026-09-20'
        assert truth['conclusion_with_evidence'] == 'normal'
    if cause == 'gap_in_transit':
        assert withheld[0]['business_date'] == '2026-10-02'
        assert truth['conclusion_with_evidence'] == 'supported'
    if cause == 'gap_order_reduced':
        assert withheld[0]['amount'] == '-82822.35'
    assert all(r['observed_at'] > '2026-09-30T23:59:59+00:00' for r in withheld)
