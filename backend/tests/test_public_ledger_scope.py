"""Hand-calculated public-extract boundaries; no anomaly truth or runtime model involved."""
import csv
from decimal import Decimal, localcontext
import json

import pytest

from benchmarks import public_ledger_checks as ledger
from scripts.validate_public_ledger_arithmetic import validate


def write_rows(tmp_path, rows):
    path = tmp_path / 'extract.csv'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow([*ledger.KEYS, 'ACCOUNT', ledger.AMOUNT])
        writer.writerows(rows)
    return path


def row(account, amount, unit='100', period='13', fund='F1'):
    return [unit, 'ACTUALS', '2024', period, fund, account, amount]


@pytest.mark.parametrize('different_unit', [False, True])
def test_all_classes_and_zero_net_do_not_certify_complete_books(tmp_path, different_unit):
    result = ledger.check(write_rows(tmp_path, [row('1000', '10'), row('2000', '-5'),
                                              row('3000', '-5', unit='200' if different_unit else '100')]))
    assert result['trial_balance_testable'] is False
    assert result['balance_assessment']['status'] == 'insufficient_evidence'
    assert result['source_actuals']['total_net'] == '0'
    assert result['fiscal_periods'] == ['2024-13']  # fiscal period 13 is not a calendar month


def test_rejected_subcents_stay_in_source_net_and_duplicate_statistics(tmp_path):
    result = ledger.check(write_rows(tmp_path, [row('1000', '1.00'), row('4000', '-1.00'),
                                              row('5000', '.005'), row('5000', '.005'),
                                              row('5000', '.003', unit='200', period='14')]))
    assert result['rows'] == 5 and result['money_accepted'] == 2 and result['money_rejected'] == 3
    assert result['actuals_total_net'] == '0.00'  # explicitly accepted subset only
    source = result['source_actuals']
    assert source['total_net'] == '0.013'
    assert (source['groups'], source['zero_net_groups'], source['nonzero_net_groups']) == (2, 0, 2)
    assert source['groups_with_money_rejections'] == 2
    assert result['exact_duplicate_rows'] == 1 and result['ledgers'] == {'ACTUALS': 5}
    assert result['fiscal_periods'] == ['2024-13', '2024-14']
    assert source['affected_groups'][1]['source_net'] == '0.003'


@pytest.mark.parametrize('raw', ['NaN', 'Infinity', 'missing', '', '1e999999', '1e-999999'])
def test_unavailable_source_amount_cannot_make_partial_total_look_complete(tmp_path, raw):
    result = ledger.check(write_rows(tmp_path, [row('1000', '0.00'), row('2000', raw)]))
    assert result['money_rejected'] == 1 and result['source_amounts_unavailable'] == 1
    assert result['source_actuals']['arithmetic_complete'] is False
    assert result['source_actuals']['total_net'] is None
    assert result['source_actuals']['zero_net_groups'] == 0
    assert result['source_actuals']['unavailable_net_groups'] == 1
    assert result['source_actuals']['affected_groups'][0]['source_net'] is None


def test_source_arithmetic_does_not_round_large_values_or_follow_ambient_precision(tmp_path):
    path = write_rows(tmp_path, [row('1000', '1000000000000000000000000000000.001'),
                                row('2000', '-1000000000000000000000000000000'), row('3000', '.002')])
    result = ledger.check(path)
    assert result['source_actuals']['total_net'] == '0.003'
    with localcontext() as context:
        context.prec = 3
        assert ledger.exact_add(Decimal('123456789.001'), Decimal('0.002')) == Decimal('123456789.003')


def test_cell_separator_cannot_alias_distinct_rows(tmp_path):
    # Old join-based fingerprint produces the same bytes for these distinct adjacent cells.
    result = ledger.check(write_rows(tmp_path, [row('A\x1fB', '0', fund='F'),
                                              row('B', '0', fund='F\x1fA')]))
    assert result['exact_duplicate_rows'] == 0


@pytest.mark.parametrize('content', [
    'ACCOUNT,ACCOUNT\n1,2\n',
    ','.join([*ledger.KEYS, 'ACCOUNT', ledger.AMOUNT]) + '\n100,ACTUALS\n',
    ','.join([*ledger.KEYS, 'ACCOUNT', ledger.AMOUNT]) + '\n100,ACTUALS,2024,10,F,1000,0,extra\n',
    ','.join([*ledger.KEYS, 'ACCOUNT', ledger.AMOUNT]) + '\n,ACTUALS,2024,10,F,1000,0\n',
])
def test_malformed_or_unscoped_csv_fails_instead_of_success(tmp_path, content):
    path = tmp_path / 'bad.csv'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError):
        ledger.check(path)


@pytest.mark.parametrize('data,expected', [
    (b'abc\xc3', 3),
    (b'a' * ((1 << 20) - 1) + b'\xc3!', (1 << 20) - 1),
], ids=['truncated-final-codepoint', 'cross-block-invalid-continuation'])
def test_utf8_truncation_and_cross_block_error_offsets(tmp_path, data, expected):
    path = tmp_path / 'encoding.txt'
    path.write_bytes(data)
    assert ledger.encoding_of(path) == ('cp1252', expected)


def test_valid_utf8_codepoint_crosses_block_boundary(tmp_path):
    path = tmp_path / 'encoding.txt'
    path.write_bytes(b'a' * ((1 << 20) - 1) + 'é'.encode())
    assert ledger.encoding_of(path) == ('utf-8-sig', None)


@pytest.mark.parametrize('tamper', ['none', 'omit_affected_group', 'wrong_net'])
def test_rational_crosscheck_detects_missing_detail_and_changed_total(tmp_path, tamper):
    source = write_rows(tmp_path, [row('1000', '1.00'), row('5000', '.005')])
    result = {'schema_version': ledger.VERSION, **ledger.check(source)}
    if tamper == 'omit_affected_group':
        result['source_actuals']['affected_groups'] = []
    if tamper == 'wrong_net':
        result['source_actuals']['total_net'] = '1.00'
    report = tmp_path / 'report.json'
    report.write_text(json.dumps(result), encoding='utf-8')
    validation = validate(source, report)
    assert validation['status'] == ('passed' if tamper == 'none' else 'failed')
