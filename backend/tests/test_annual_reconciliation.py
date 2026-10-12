"""Hand-written observations for the annual-report consistency checks."""
from benchmarks.annual_reconciliation import balance, continuity, reconcile


def metric(current, previous, unit='元'):
    return {'amount_reported': current, 'previous_amount_reported': previous, 'unit': unit, 'pdf_page': 9}


def report(identifier, company, year, assets, liabilities, equity, unit='元', status='observed'):
    return {'id': identifier, 'company_code': company, 'report_year': year, 'status': status,
            'metrics': {'total_assets': metric(*assets, unit), 'total_liabilities': metric(*liabilities, unit),
                        'total_equity': metric(*equity, unit)}}


def test_balance_identity_is_exact_in_the_reported_unit_for_both_columns():
    ok = report('a', 'X', 2025, ('100.10', '90'), ('60.05', '50'), ('40.05', '40'))
    assert [row['status'] for row in balance(ok)] == ['consistent', 'consistent']
    off = report('b', 'X', 2025, ('100.00', '90'), ('60.00', '50'), ('39.99', '41'))
    rows = balance(off)
    assert [r['status'] for r in rows] == ['needs_review', 'needs_review']
    assert rows[0]['difference_reported_unit'] == '0.01' and rows[1]['difference_reported_unit'] == '-1'


def test_missing_amounts_or_mixed_units_are_not_checkable_never_assumed_zero():
    partial = report('c', 'X', 2025, ('100', None), ('60', '50'), ('40', '40'))
    assert [r['status'] for r in balance(partial)] == ['consistent', 'not_checkable']
    mixed = report('d', 'X', 2025, ('100', '90'), ('60', '50'), ('40', '40'))
    mixed['metrics']['total_equity']['unit'] = '千元'
    assert {r['status'] for r in balance(mixed)} == {'not_checkable'}
    assert balance({'id': 'e', 'metrics': {}})[0]['reason'] == 'missing_located_total'


def test_cross_year_continuity_normalises_units_and_flags_restatement_for_review():
    earlier = report('y1', 'X', 2023, ('1000', '900'), ('600', '500'), ('400', '400'), unit='千元')
    later_same = report('y2', 'X', 2024, ('1100000', '1000000'), ('700000', '600000'), ('400000', '400000'))
    rows = continuity([earlier, later_same])
    assert {r['status'] for r in rows} == {'consistent'} and len(rows) == 3
    restated = report('y2', 'X', 2024, ('1100000', '1050000'), ('700000', '600000'), ('400000', '450000'))
    rows = {r['item']: r for r in continuity([earlier, restated])}
    assert rows['total_assets']['difference_cny'] == '50000'
    assert rows['total_assets']['interpretation'] == 'restatement_or_extraction_error_to_review'
    assert rows['total_liabilities']['status'] == 'consistent'


def test_rejected_reports_and_gaps_in_years_are_skipped_not_invented():
    a = report('a', 'X', 2023, ('1', '1'), ('1', '1'), ('0', '0'))
    c = report('c', 'X', 2025, ('1', '1'), ('1', '1'), ('0', '0'))
    rejected = report('b', 'X', 2024, ('1', '1'), ('1', '1'), ('0', '0'), status='rejected')
    assert continuity([a, rejected, c]) == []
    summary = reconcile({'reports': [a, rejected, c]})
    assert summary['reports_observed'] == 2 and summary['balance_counts'] == {'consistent': 4}
    assert summary['model_requests'] == 0
