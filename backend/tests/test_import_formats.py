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
