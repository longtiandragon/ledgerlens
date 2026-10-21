import io
import pytest
from datetime import datetime

from openpyxl import Workbook
from app.imports import parse_file


def test_excel_decimal_and_locator_are_preserved():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '资金明细'
    sheet.append(['记录编号', '类型', '业务日期', '期间', '金额'])
    sheet.append(['A', 'bank', datetime(2026, 9, 1), '2026-09', 1234.56])
    buffer = io.BytesIO()
    workbook.save(buffer)
    rows, errors, _, _, _ = parse_file(buffer.getvalue(), 'test.xlsx')
    assert errors == []
    assert rows[0]['record']['amount'] == '1234.56'
    assert rows[0]['locator'] == {'sheet': '资金明细', 'row': 2}


def test_excel_formula_is_not_executed_or_silently_zeroed():
    workbook = Workbook()
    workbook.active.append(['source_id', 'kind', 'business_date', 'period', 'amount'])
    workbook.active.append(['A', 'bank', '2026-09-01', '2026-09', '=1+2'])
    buffer = io.BytesIO()
    workbook.save(buffer)
    rows, errors, _, _, _ = parse_file(buffer.getvalue(), 'test.xlsx')
    assert not rows and errors[0]['field'] == 'amount'


def test_custom_mapping_preserves_explicit_zero_and_missing():
    payload = 'ID,TYPE,DATE,MONTH,VALUE\na,bank,2026-09-01,2026-09,0.00\nb,bank,2026-09-01,2026-09,\n'
    rows, errors, _, _, _ = parse_file(payload.encode(), 'data.csv',
        {'source_id': 'ID', 'kind': 'TYPE', 'business_date': 'DATE', 'period': 'MONTH', 'amount': 'VALUE'})
    assert len(rows) == 1 and rows[0]['record']['amount'] == '0.00'
    assert errors[0]['row'] == 3


@pytest.mark.parametrize('content,filename', [
    (b'source_id,amount,amount\na,100.00,200.00\n', 'conflict.csv'),
    (b'source_id,amount\na,100.00,unexpected\n', 'extra.csv'),
    (b'[{"amount":"100.00","amount":"200.00"}]', 'conflict.json'),
])
def test_ambiguous_file_columns_cannot_silently_select_an_amount(content, filename):
    with pytest.raises(ValueError):
        parse_file(content, filename)


def test_invalid_mapping_is_reported_as_input_error():
    with pytest.raises(ValueError):
        parse_file(b'source_id,amount\na,1.00', 'data.csv', ['amount'])


VOUCHER_EXPORT = ('﻿日期,凭证号,摘要,科目编码,科目名称,借方金额,贷方金额,发票号\n'
                  '2026/9/3,记-001,采购办公用品,6602,管理费用,1200.00,,FP1\n'
                  '2026/9/3,记-001,采购办公用品,2202,应付账款,,1200.00,FP1\n')


def test_common_voucher_export_imports_with_every_derived_field_marked():
    rows, errors, _, mapping, _ = parse_file(VOUCHER_EXPORT.encode(), '九月凭证.csv')
    assert errors == []
    assert mapping['debit'] == '借方金额' and mapping['account'] == '科目编码' and mapping['description'] == '摘要'
    first, second = rows
    assert first['record'] == {
        'source_id': '九月凭证.csv#row-2', 'kind': 'journal', 'business_date': '2026-09-03', 'period': '2026-09',
        'currency': 'CNY', 'counterparty': '', 'reference': '', 'order_id': '', 'invoice_id': 'FP1',
        'voucher_id': '记-001', 'account': '6602', 'debit': '1200.00', 'credit': '0.00',
        'description': '采购办公用品', 'direction': ''}
    assert first['locator'] == {'row': 2, 'derived': ['business_date', 'kind', 'period', 'source_id', 'credit']}
    assert second['record']['debit'] == '0.00' and second['locator']['derived'][-1] == 'debit'


def test_voucher_line_with_both_sides_blank_is_still_rejected_in_chinese():
    payload = '日期,凭证号,科目编码,借方金额,贷方金额\n2026-09-03,记-001,6602,,\n'
    rows, errors, _, _, _ = parse_file(payload.encode(), 'v.csv')
    assert rows == []
    assert any(e['field'] == 'kind' and '记录类型' in e['message'] for e in errors)


def test_explicit_columns_are_never_overridden_by_derivation():
    payload = ('来源编号,类型,业务日期,期间,凭证号,科目,借方,贷方\n'
               'S1,journal,2026-09-03,2026-08,记-001,6602,1200.00,0.00\n')
    rows, errors, _, _, _ = parse_file(payload.encode(), 'v.csv')
    assert errors == [] and rows[0]['record']['period'] == '2026-08' and rows[0]['record']['source_id'] == 'S1'
    assert 'derived' not in rows[0]['locator']


def test_business_rows_without_an_account_are_not_guessed_as_journal_lines():
    payload = '日期,金额,摘要\n2026-09-03,500.00,银行收款\n'
    rows, errors, _, _, _ = parse_file(payload.encode(), 'bank.csv')
    assert rows == [] and [e['field'] for e in errors] == ['kind']
    assert errors[0]['message'] == '缺少必填字段“记录类型”；请在字段映射中选择对应列'


def test_unparseable_dates_are_reported_not_guessed():
    payload = '日期,凭证号,科目编码,借方金额,贷方金额\n九月三日,记-001,6602,1.00,\n'
    rows, errors, _, _, _ = parse_file(payload.encode(), 'v.csv')
    assert rows == [] and any(e['field'] == 'business_date' and '有效日期' in e['message'] for e in errors)
