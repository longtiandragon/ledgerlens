"""Offline, handwritten ERP GL contract checks; never an ERP execution claim.

The independent reference totals below are test oracles, not application rules.
Rates and accounts are synthetic accounting examples, not tax-policy guidance.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.connectors import fetch_erpnext_gl  # noqa: E402
from app.imports import parse_file  # noqa: E402

COMPANY = 'LedgerLens Extended Contract Synthetic'
MAPPING = {'Inventory': '1405', 'Charge A': '6602-A', 'Charge B': '6602-B',
           'Withholding': '2241', 'Payable': '2202', 'Receivable': '1122',
           'Revenue': '6001', 'Bank': '1002', 'Exchange': '6603'}

# Each posting is independently written down, in company currency CNY.
# No production amount calculation is used to create these expected totals.
SCENARIOS = [
    {'id': 'compound_charges_and_partial_return',
     'derivation': '100 net + 10 charge + 5.50 compound charge - 2 withheld = 113.50; '
                   '40 percent return reverses 40 + 4 + 2.20 - 0.80 = 45.40.',
     'postings': [
         ['purchase', 'Inventory', '100.00', '0.00'], ['purchase', 'Charge A', '10.00', '0.00'],
         ['purchase', 'Charge B', '5.50', '0.00'], ['purchase', 'Withholding', '0.00', '2.00'],
         ['purchase', 'Payable', '0.00', '113.50'],
         ['return', 'Inventory', '0.00', '40.00'], ['return', 'Charge A', '0.00', '4.00'],
         ['return', 'Charge B', '0.00', '2.20'], ['return', 'Withholding', '0.80', '0.00'],
         ['return', 'Payable', '45.40', '0.00']],
     'expected_totals': {'1405': ['100.00', '40.00'], '6602-A': ['10.00', '4.00'],
                         '6602-B': ['5.50', '2.20'], '2241': ['0.80', '2.00'], '2202': ['45.40', '113.50']},
     'expected_sum': ['161.70', '161.70']},
    {'id': 'foreign_currency_receivable_realized_loss',
     'derivation': 'USD 100 invoice at 7.20 = CNY 720; receipt at 7.10 = CNY 710; realized loss CNY 10.',
     'postings': [['invoice', 'Receivable', '720.00', '0.00'], ['invoice', 'Revenue', '0.00', '720.00'],
                  ['payment', 'Bank', '710.00', '0.00'], ['payment', 'Exchange', '10.00', '0.00'],
                  ['payment', 'Receivable', '0.00', '720.00']],
     'expected_totals': {'1122': ['720.00', '720.00'], '6001': ['0.00', '720.00'],
                         '1002': ['710.00', '0.00'], '6603': ['10.00', '0.00']},
     'expected_sum': ['1440.00', '1440.00']},
    {'id': 'foreign_currency_payable_realized_gain',
     'derivation': 'USD 100 payable at 7.20 = CNY 720; payment at 7.10 = CNY 710; realized gain CNY 10.',
     'postings': [['invoice', 'Inventory', '720.00', '0.00'], ['invoice', 'Payable', '0.00', '720.00'],
                  ['payment', 'Payable', '720.00', '0.00'], ['payment', 'Bank', '0.00', '710.00'],
                  ['payment', 'Exchange', '0.00', '10.00']],
     'expected_totals': {'1405': ['720.00', '0.00'], '2202': ['720.00', '720.00'],
                         '1002': ['0.00', '710.00'], '6603': ['0.00', '10.00']},
     'expected_sum': ['1440.00', '1440.00']},
    {'id': 'unrealized_revaluation_separate_voucher',
     'derivation': 'USD 100 open receivable at 7.00 = CNY 700; closing 7.20 adds CNY 20; no receipt invented.',
     'postings': [['invoice', 'Receivable', '700.00', '0.00'], ['invoice', 'Revenue', '0.00', '700.00'],
                  ['revaluation', 'Receivable', '20.00', '0.00'], ['revaluation', 'Exchange', '0.00', '20.00']],
     'expected_totals': {'1122': ['720.00', '0.00'], '6001': ['0.00', '700.00'], '6603': ['0.00', '20.00']},
     'expected_sum': ['720.00', '720.00']},
    {'id': 'cancelled_original_and_reversal_excluded',
     'derivation': 'Cancelled original and its reversal are retained by ERP but both excluded from active GL.',
     'postings': [['cancelled-original', 'Inventory', '50.00', '0.00', 1],
                  ['cancelled-original', 'Payable', '0.00', '50.00', 1],
                  ['cancelled-reversal', 'Inventory', '0.00', '50.00', 1],
                  ['cancelled-reversal', 'Payable', '50.00', '0.00', 1],
                  ['active', 'Inventory', '25.00', '0.00'], ['active', 'Payable', '0.00', '25.00']],
     'expected_totals': {'1405': ['25.00', '0.00'], '2202': ['0.00', '25.00']},
     'expected_sum': ['25.00', '25.00']},
]


def source_rows(scenario):
    return [{'name': f"{scenario['id']}-{i}", 'voucher_no': row[0], 'account': row[1],
             'debit': row[2], 'credit': row[3], 'is_cancelled': row[4] if len(row) > 4 else 0,
             'company': COMPANY, 'posting_date': '2026-09-30',
             # Deliberately different: the adapter must preserve base debit/credit.
             'account_currency': 'USD', 'debit_in_account_currency': '999.99'}
            for i, row in enumerate(scenario['postings'])]


async def export_mock(rows, *, currency='CNY', honor_filter=True):
    requests = []

    def handler(request):
        if request.method != 'GET':
            raise AssertionError('ERP adapter attempted a mutation')
        requests.append({'method': request.method, 'path': request.url.path})
        if '/Company/' in request.url.path:
            return httpx.Response(200, json={'data': {'name': COMPANY, 'default_currency': currency}})
        filters = json.loads(request.url.params['filters'])
        fields = json.loads(request.url.params['fields'])
        assert ['company', '=', COMPANY] in filters and ['is_cancelled', '=', 0] in filters
        assert 'is_cancelled' in fields
        selected = [r for r in rows if r.get('is_cancelled') == 0] if honor_filter else rows
        return httpx.Response(200, json={'data': selected})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_erpnext_gl('https://erp.example.invalid', 'synthetic', 'synthetic', COMPANY,
                                       '2026-09-01', '2026-09-30', MAPPING, client)
    return result, requests


def inspect(export, scenario):
    rows = export['records']
    totals = {}
    vouchers = {}
    for row in rows:
        pair = totals.setdefault(row['account'], [Decimal(0), Decimal(0)])
        pair[0] += Decimal(row['debit'])
        pair[1] += Decimal(row['credit'])
        vouchers[row['voucher_id']] = vouchers.get(row['voucher_id'], Decimal(0)) + Decimal(row['debit']) - Decimal(row['credit'])
    expected = {key: [Decimal(x) for x in value] for key, value in scenario['expected_totals'].items()}
    parsed, errors, *_ = parse_file(json.dumps(export).encode(), 'extended-gl.json')
    return {
        'handwritten_account_totals': totals == expected,
        'handwritten_grand_totals': [sum(pair[i] for pair in totals.values()) for i in (0, 1)]
                                   == [Decimal(x) for x in scenario['expected_sum']],
        'every_voucher_balanced': all(value == 0 for value in vouchers.values()),
        'source_identifiers_preserved': {r['source_id'] for r in rows}
                                      == {r['name'] for r in source_rows(scenario) if r['is_cancelled'] == 0},
        'explicit_company_currency_strings': export['currency'] == 'CNY'
            and all(r['currency'] == 'CNY' and isinstance(r['debit'], str) and isinstance(r['credit'], str) for r in rows),
        'normal_preview_preserves_rows': not errors and len(parsed) == len(rows),
        'no_inferred_business_evidence': all('invoice_id' not in r and 'order_id' not in r for r in rows),
    }


async def validate():
    report = {'schema_version': 'erpnext-extended-contract-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
              'status': 'running', 'mode': 'offline_mock_contract', 'erp_live_executed': False,
              'model_requests': 0, 'company': COMPANY, 'scenarios': [], 'negative_cases': [],
              'sources': ['https://docs.frappe.io/erpnext/immutable-ledger-in-erpnext',
                          'https://docs.frappe.io/erpnext/manage-foreign-exchange-difference',
                          'https://docs.frappe.io/erpnext/exchange-rate-revaluation'],
              'limits': ['Does not run ERPNext document calculations or posting engine.',
                         'Synthetic rates are not tax policy; GL rows do not establish invoice or stock evidence.',
                         'USD-base companies remain explicitly unsupported; no currency conversion is inferred.'],
              'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                ('scripts/validate_erpnext_extended.py', 'backend/app/connectors.py')}}
    for scenario in SCENARIOS:
        export, requests = await export_mock(source_rows(scenario))
        checks = inspect(export, scenario)
        report['scenarios'].append({'id': scenario['id'], 'derivation': scenario['derivation'],
                                    'checks': checks, 'export': export, 'requests': requests})
    for value in [1, -1, None, '0', False, 'missing']:
        rows = source_rows(SCENARIOS[0])[:1]
        if value == 'missing':
            rows[0].pop('is_cancelled')
        else:
            rows[0]['is_cancelled'] = value
        try:
            await export_mock(rows, honor_filter=False)
        except ValueError as exc:
            report['negative_cases'].append({'case': repr(value), 'rejected': True, 'error': str(exc)})
        else:
            report['negative_cases'].append({'case': repr(value), 'rejected': False})
    try:
        await export_mock([], currency='USD')
    except ValueError as exc:
        report['negative_cases'].append({'case': 'USD_base', 'rejected': True, 'error': str(exc)})
    else:
        report['negative_cases'].append({'case': 'USD_base', 'rejected': False})
    report['status'] = 'passed' if (all(all(s['checks'].values()) for s in report['scenarios'])
                                   and all(s['rejected'] for s in report['negative_cases'])) else 'failed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        report = asyncio.run(validate())
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': report['status'], 'scenarios': len(report['scenarios']),
                      'negative_cases': len(report['negative_cases']), 'erp_live_executed': False}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
