from copy import deepcopy

import pytest

from app.ocr_observations import normalize
from app.ocr_tables import candidates, field_value, MAX_ROWS
from app import ocr_workflow
from app.db import ImportBatch, OcrReview, Record
from sqlalchemy import select
from test_ocr_workflow import context as context, start


def fixture(rows=None, header=None):
    rows = rows if rows is not None else [['2026-09-03', 'T101', '办公采购', '1,250.50'],
                                         ['2026/09/10', 'T102', '预付订金', '0.00']]
    cells = [header or ['日期', '编号', '摘要', '金额'], *rows]
    texts, polygons = [], []
    for y, values in enumerate(cells):
        for x, value in enumerate(values):
            if value is None:
                continue
            texts.append(value)
            left, top = 40 + x * 200, 20 + y * 50
            polygons.append([[left, top], [left + 100, top], [left + 100, top + 20], [left, top + 20]])
    return normalize({'res': {'rec_texts': texts, 'rec_scores': [0.9] * len(texts), 'rec_polys': polygons}},
                     source_hash='fixture-source', width=800, height=50 * len(cells), engine={'fixture': 'manual'})


def test_headers_geometry_and_printed_values_produce_unconfirmed_candidates():
    source = fixture()
    original = deepcopy(source)
    result = candidates(source, 1)
    assert source == original
    assert result['confirmation'] == 'unconfirmed' and result['ledger_writes'] == 0
    rows = result['tables'][0]['rows']
    assert [r['record'] for r in rows] == [
        {'business_date': '2026-09-03', 'source_id': 'T101', 'description': '办公采购', 'amount': '1250.50'},
        {'business_date': '2026-09-10', 'source_id': 'T102', 'description': '预付订金', 'amount': '0.00'}]
    assert all(r['copyable'] and not r['issues'] for r in rows)
    assert 'kind' not in rows[0]['record'] and 'period' not in rows[0]['record']
    assert rows[1]['field_evidence']['amount'] == [source['observations'][11]['observation_id']]
    assert candidates(source, 1) == result


@pytest.mark.parametrize('text', ['1O0.00', '12,34.50', '1.005', 'NaN', '¥100.00', '1e3', '１２.００'])
def test_amount_ambiguity_is_not_repaired_or_rounded(text):
    result = candidates(fixture(rows=[['2026-09-03', 'X', '测试', text]]), 1)
    row = result['tables'][0]['rows'][0]
    assert 'amount' not in row['record']
    assert row['cells']['amount']['text'] == text
    assert 'invalid_amount:amount' in row['issues']
    assert 'amount' not in row['field_evidence']


@pytest.mark.parametrize('text', ['2026-02-30', '09/03/2026', '2026-9-3', '2026-09-03 extra'])
def test_dates_require_unambiguous_valid_iso_order(text):
    assert field_value('business_date', text) == (None, 'invalid_date')


def test_missing_zero_and_out_of_order_input_are_not_conflated():
    source = fixture(rows=[['2026-09-03', 'X', '测试', None], ['2026-09-10', 'Y', '零金额', '0']])
    source['observations'].reverse()
    rows = candidates(source, 1)['tables'][0]['rows']
    assert 'amount' not in rows[0]['record']
    assert rows[1]['record']['amount'] == '0.00'
    assert 'missing_valid_field:amount' in rows[0]['issues']


@pytest.mark.parametrize('fault', ['cross_column', 'duplicate_cell'])
def test_ambiguous_geometry_blocks_copy_and_preserves_observations(fault):
    source = fixture()
    if fault == 'cross_column':
        source['observations'][6]['locator']['polygon_px'][1][0] = 750
        source['observations'][6]['locator']['polygon_px'][2][0] = 750
    else:
        extra = deepcopy(source['observations'][6])
        extra['observation_id'] = 'a-second-observation'
        source['observations'].append(extra)
    row = candidates(source, 1)['tables'][0]['rows'][0]
    assert row['copyable'] is False
    assert source['observations'][6]['observation_id'] in row['observation_ids']
    assert row['issues']


def test_duplicate_header_is_explicitly_unsupported():
    result = candidates(fixture(header=['日期', '金额', '摘要', '金额']), 1)
    assert result['status'] == 'no_table' and result['tables'] == []
    assert 'ambiguous_header' in result['issues']


def test_unknown_income_expense_layout_does_not_invent_a_single_amount():
    result = candidates(fixture(header=['日期', '编号', '收入', '支出']), 1)
    assert result['status'] == 'no_table'
    assert sum(len(g['observation_ids']) for g in result['unassigned']) == 12


def observed_membership(result):
    return [identity for table in result['tables'] for identity in (
        *[h['observation_id'] for h in table['headers']],
        *[identity for row in table['rows'] for identity in row['observation_ids']])
    ] + [identity for group in result['unassigned'] for identity in group['observation_ids']]


@pytest.mark.parametrize('unsupported', [
    ['日期', '编号', '收入', '支出'], ['日期', '编号', '摘要', '金颚'],
    ['业务日期', '流水号', '摘要', '余额'], ['来源编号', '摘要', '单价', '数量'],
    ['项目', '收入', '支出', '余额'], ['科目', '借方金额', '贷方金额', '余额'],
])
def test_unsupported_later_header_cannot_inherit_previous_amount_column(unsupported):
    source = fixture(rows=[['2026-09-03', 'A', '明确金额', '10.00'], unsupported,
                           ['2026-09-10', 'B', '900.00', '800.00']])
    original = deepcopy(source)
    result = candidates(source, 1)
    assert source == original
    assert len(result['tables']) == 1
    assert [r['record']['source_id'] for r in result['tables'][0]['rows']] == ['A']
    assert [g['reason'] for g in result['unassigned']] == ['unsupported_header_boundary', 'outside_recognized_table']
    assert sorted(observed_membership(result)) == sorted(r['observation_id'] for r in source['observations'])


def test_supported_table_after_unsupported_header_starts_its_own_binding():
    source = fixture(rows=[['2026-09-03', 'A', '首张表', '10.00'], ['日期', '编号', '收入', '支出'],
                           ['2026-09-10', 'B', '900.00', '800.00'], ['日期', '编号', '摘要', '金额'],
                           ['2026-09-21', 'C', '新表', '30.00']])
    result = candidates(source, 1)
    assert [[r['record']['source_id'] for r in t['rows']] for t in result['tables']] == [['A'], ['C']]
    assert len({t['table_id'] for t in result['tables']}) == 2
    assert sorted(observed_membership(result)) == sorted(r['observation_id'] for r in source['observations'])


def test_one_header_word_inside_data_description_is_still_a_data_row():
    source = fixture(rows=[['2026-09-03', 'A', '金额', '10.00']])
    row = candidates(source, 1)['tables'][0]['rows'][0]
    assert row['copyable'] and not row['issues']
    assert row['record']['description'] == '金额'


def test_footer_and_notes_remain_explicitly_unassigned():
    result = candidates(fixture(rows=[['2026-09-03', 'X', '测试', '1'], ['合计', None, None, '1'],
                                    ['审核人', None, None, None]]), 1)
    assert len(result['tables'][0]['rows']) == 1
    assert [g['reason'] for g in result['unassigned']] == ['footer_requires_manual_review', 'outside_recognized_table']


def test_budget_exhaustion_does_not_claim_complete_table():
    source = fixture(rows=[['2026-09-03', f'X{i}', '测试', '1'] for i in range(MAX_ROWS + 1)])
    result = candidates(source, 1)
    assert len(result['tables'][0]['rows']) == MAX_ROWS
    assert result['status'] == 'limit_exceeded' and 'row_budget_exhausted' in result['issues']
    assert len(result['unassigned'][-1]['observation_ids']) == 4


def test_page_failure_and_absence_are_not_successful_empty_tables():
    result = {'pages': [{'page': 1, 'status': 'failed'}]}
    assert candidates(result, 1)['status'] == 'page_failed'
    with pytest.raises(ValueError, match='does not exist'):
        candidates(result, 2)
    assert candidates(fixture(rows=[], header=['说明', '其他', '材料', '来源']), 1)['status'] == 'no_table'


def test_candidate_identity_is_bound_to_result_and_page():
    first = fixture()
    second = deepcopy(first)
    second['source_sha256'] = 'another-file'
    assert candidates(first, 1)['tables'][0]['rows'][0]['candidate_id'] != candidates(second, 1)['tables'][0]['rows'][0]['candidate_id']


def test_api_is_scoped_read_only_and_rejects_modified_original(context, monkeypatch):
    client, factory, book = context
    def infer(doc):
        source = fixture()
        # Re-normalize all hashes for this uploaded document.
        return normalize({'res': {'rec_texts': [r['text'] for r in source['observations']],
                                 'rec_scores': [r['confidence'] for r in source['observations']],
                                 'rec_polys': [r['locator']['polygon_px'] for r in source['observations']]}},
                         source_hash=doc.sha256, width=800, height=150, engine={'fixture': 'manual'})
    monkeypatch.setattr(ocr_workflow, 'infer', infer)
    run = start(context)
    url = f'/api/v1/ocr/runs/{run["id"]}/table-candidates'
    response = client.get(url, params={'book_id': book, 'page': 1})
    assert response.status_code == 200, response.text
    assert len(response.json()['tables'][0]['rows']) == 2
    assert response.json()['result_digest'] == run['result_digest']
    assert client.get(url, params={'book_id': 'another-book'}).status_code == 404
    assert client.get(url, params={'book_id': book, 'page': 2}).status_code == 422
    with factory() as s:
        for cls in (Record, ImportBatch, OcrReview):
            assert s.scalars(select(cls)).all() == []
        from app.db import Document
        from pathlib import Path
        Path(s.get(Document, run['document_id']).storage_path).write_bytes(b'altered')
    assert client.get(url, params={'book_id': book}).status_code == 409
