"""Hand-authored geometry and fixed expected values, independent of the extractor."""
from copy import deepcopy

import pytest

from app.ocr_observations import normalize
from app.ocr_tables import candidates
from test_ocr_tables import observed_membership


def source(*, extra_text='第二行说明', extra_x=440, extra_y=96, extra_width=100, complete=True):
    entries = [(40, 20, '日期'), (240, 20, '编号'), (440, 20, '摘要'), (640, 20, '金额'),
               (40, 70, '2026-09-03'), (240, 70, 'A101'), (440, 70, '设备维护'),
               (640, 70, '1250.50' if complete else '1O.00'),
               (extra_x, extra_y, extra_text),
               (40, 150, '2026-09-04'), (240, 150, 'A102'), (440, 150, '耗材采购'), (640, 150, '40.00')]
    polygons = [[[x, y], [x + (extra_width if i == 8 else 100), y],
                 [x + (extra_width if i == 8 else 100), y + 20], [x, y + 20]]
                for i, (x, y, _) in enumerate(entries)]
    return normalize({'res': {'rec_texts': [t for _, _, t in entries], 'rec_scores': [.95] * len(entries),
                              'rec_polys': polygons}}, source_hash='independent-wrapped-fixture',
                     width=800, height=240, engine={'fixture': 'manual-not-ocr'})


def test_close_aligned_description_preserves_both_real_evidence_locations():
    result = source()
    original = deepcopy(result)
    output = candidates(result, 1)
    rows = output['tables'][0]['rows']
    assert [row['record'] for row in rows] == [
        {'business_date': '2026-09-03', 'source_id': 'A101', 'description': '设备维护\n第二行说明', 'amount': '1250.50'},
        {'business_date': '2026-09-04', 'source_id': 'A102', 'description': '耗材采购', 'amount': '40.00'}]
    assert rows[0]['issues'] == ['wrapped_description_requires_manual_review']
    assert rows[0]['confirmation'] == 'unconfirmed' and rows[0]['copyable']
    assert rows[0]['field_evidence']['description'] == [result['observations'][6]['observation_id'],
                                                     result['observations'][8]['observation_id']]
    assert sorted(observed_membership(output)) == sorted(r['observation_id'] for r in result['observations'])
    assert result == original
    assert output['ledger_writes'] == 0


@pytest.mark.parametrize('overrides', [
    {'extra_y': 125}, {'extra_x': 480}, {'extra_width': 220}, {'extra_x': 640, 'extra_text': '50.00'},
    {'extra_x': 40, 'extra_text': '2026-09-05'}, {'complete': False},
    {'extra_y': 84},
])
def test_sparse_or_ambiguous_lines_never_inherit_amount_or_date(overrides):
    result = source(**overrides)
    output = candidates(result, 1)
    first = output['tables'][0]['rows'][0]
    assert first['record']['description'] == '设备维护'
    assert result['observations'][8]['observation_id'] not in first['observation_ids']
    assert sorted(observed_membership(output)) == sorted(r['observation_id'] for r in result['observations'])


def test_small_tilted_box_overlap_keeps_distinct_description_baselines():
    result = source(extra_y=88)
    output = candidates(result, 1)
    first = output['tables'][0]['rows'][0]
    assert first['record']['description'] == '设备维护\n第二行说明'
    assert first['field_evidence']['description'] == [result['observations'][6]['observation_id'],
                                                     result['observations'][8]['observation_id']]
    assert first['issues'] == ['wrapped_description_requires_manual_review']
    assert sorted(observed_membership(output)) == sorted(r['observation_id'] for r in result['observations'])


def test_footer_stops_the_table_instead_of_becoming_wrapped_description():
    output = candidates(source(extra_text='备注：请核实'), 1)
    assert len(output['tables'][0]['rows']) == 1
    assert output['tables'][0]['rows'][0]['record']['description'] == '设备维护'
    assert output['unassigned'][0]['reason'] == 'footer_requires_manual_review'


def test_continuation_changes_candidate_identity_without_changing_original():
    with_wrap = source()
    without_wrap = deepcopy(with_wrap)
    without_wrap['observations'].pop(8)
    assert candidates(with_wrap, 1)['tables'][0]['rows'][0]['candidate_id'] != candidates(
        without_wrap, 1)['tables'][0]['rows'][0]['candidate_id']


def test_each_page_requires_its_own_complete_header_and_never_inherits_previous_page():
    page1 = {**source(), 'page': 1}
    page2 = deepcopy(page1)
    page2['page'] = 2
    page2['observations'] = page2['observations'][4:]
    for line in page2['observations']:
        line['observation_id'] += '-page2'
        line['locator']['page'] = 2
    bundle = {'pages': [page1, page2]}
    assert len(candidates(bundle, 1)['tables'][0]['rows']) == 2
    assert candidates(bundle, 2)['status'] == 'no_table'
    page2['observations'] = deepcopy(page1['observations'])
    for line in page2['observations']:
        line['observation_id'] += '-repeated-header-page2'
        line['locator']['page'] = 2
    second = candidates(bundle, 2)
    assert len(second['tables'][0]['rows']) == 2
    assert all(identity.endswith('-repeated-header-page2') for identity in observed_membership(second))


def test_repeated_header_on_same_page_keeps_continuations_in_their_own_table():
    result = source()
    repeated = deepcopy(result['observations'])
    for line in repeated:
        line['observation_id'] += '-table2'
        for point in line['locator']['polygon_px']:
            point[1] += 250
    result['observations'].extend(repeated)
    result['image']['height'] = 500
    output = candidates(result, 1)
    assert [len(t['rows']) for t in output['tables']] == [2, 2]
    assert len({t['table_id'] for t in output['tables']}) == 2
    assert sorted(observed_membership(output)) == sorted(r['observation_id'] for r in result['observations'])
