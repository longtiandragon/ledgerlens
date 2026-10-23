from datasets.finbalance_projection import project
import json
import pytest


def record():
    return dict(record_id='NEG_TAX_RATE_SECRET', period_start='2026-01-01', period_end='2026-01-31',
                opening_balance={'date': '2026-01-01', 'assets': {'cash': '100.00'},
                                 'liabilities': {}, 'equity': {}}, allowed_accounts=['cash'],
                expected_entries=['ANSWER_SECRET'], expected_inconsistency_codes=['ANSWER_SECRET'],
                metadata={'scenario': 'ANSWER_SECRET'}, documents=[
                    dict(doc_id='NEG_TAX_RATE_SECRET', title='Invoice', date='2026-01-02', doc_type='invoice',
                         ocr_text='Total 12.00', role='ANSWER_SECRET', asset_path='ANSWER_SECRET.pdf',
                         metadata={'validation': 'ANSWER_SECRET'})])


def test_answer_fields_names_roles_and_paths_are_not_observations():
    result = project(record(), 1)
    encoded = json.dumps(result)
    assert 'SECRET' not in encoded
    assert result['documents'][0]['text'] == 'Total 12.00'
    assert result['opening_balance']['assets']['cash'] == '100.00'
    assert result['documents'][0]['document_id'] == 'doc-001'


def test_answers_cannot_change_observation_projection():
    original = record()
    changed = record()
    changed.update(record_id='different', expected_entries=[], expected_inconsistency_codes=[], metadata={})
    assert project(original, 1) == project(changed, 1)


def test_duplicate_document_ids_rejected():
    row = record()
    row['documents'].append(row['documents'][0].copy())
    with pytest.raises(ValueError, match='Duplicate'):
        project(row, 1)


def test_binary_float_money_is_rejected():
    row = record()
    row['opening_balance']['assets']['cash'] = 0.1
    with pytest.raises(ValueError, match='Decimal'):
        project(row, 1)


def test_nested_balance_metadata_does_not_cross_boundary():
    row = record()
    row['opening_balance']['expected_answer'] = 'SECRET'
    assert 'SECRET' not in json.dumps(project(row, 1))
