"""Synthetic local purchase tax and partial-return differential; not tax advice.

Writes only a new named synthetic company. Runtime GL adapter stays read-only.
The 10 percent charge is a test parameter, not a statutory rate or tax policy.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT))
from app.connectors import fetch_erpnext_gl  # noqa: E402
from app.imports import parse_file  # noqa: E402
from scripts.validate_erpnext import api_credentials, totals  # noqa: E402
from scripts.validate_erpnext_semantics import (  # noqa: E402
    create, ensure_company, get, json_safe, listed, trial_balance,
)

COMPANY = 'LedgerLens Tax Return Synthetic'
SUPPLIER = 'LedgerLens Tax Return Synthetic Supplier'
ITEM = 'LEDGERLENS-TAX-RETURN-SYNTHETIC-001'
# Independent, handwritten amounts: 10 x 12.34 plus test charge 12.34;
# return 3 x 12.34 plus charge reversal 3.70, leaving stock 86.38 / payable 95.02.
EXPECTED = {'1405': (Decimal('123.40'), Decimal('37.02')),
            'TEST-TAX': (Decimal('12.34'), Decimal('3.70')),
            '2202': (Decimal('40.72'), Decimal('135.74'))}
EXPECTED_VOUCHERS = {
    'purchase': {('1405', '123.40', '0.00'), ('TEST-TAX', '12.34', '0.00'), ('2202', '0.00', '135.74')},
    'return': {('1405', '0.00', '37.02'), ('TEST-TAX', '0.00', '3.70'), ('2202', '40.72', '0.00')},
}


def ensure_invoice(client, marker, document):
    found = listed(client, 'Purchase Invoice', [['company', '=', COMPANY], ['bill_no', '=', marker]], ['name', 'docstatus'])
    if found:
        if len(found) != 1 or found[0]['docstatus'] != 1:
            raise ValueError('Synthetic return fixture is ambiguous, draft or cancelled; no replacement')
        return get(client, 'Purchase Invoice', found[0]['name'])
    return create(client, 'Purchase Invoice', {**document, 'company': COMPANY, 'bill_no': marker, 'docstatus': 1})


def inspect(report):
    """Recompute every check from saved documents; never trust reported flags."""
    docs, export, stock = report['documents'], report['export'], report['stock']
    purchase, returned = docs['purchase'], docs['return']
    records = export['records']
    checks = {}
    checks['scope_company_currency_and_journal_only'] = (
        report['company'] == export['company'] == COMPANY and export['currency'] == 'CNY'
        and all(r['kind'] == 'journal' and r['currency'] == 'CNY' for r in records))
    checks['source_ids_unique'] = len({r['source_id'] for r in records}) == len(records)
    checks['explicit_string_money'] = all(isinstance(r[k], str) for r in records for k in ('debit', 'credit'))
    checks['submitted_same_company_supplier'] = all(d['docstatus'] == 1 and d['company'] == COMPANY
        and d['supplier'] == SUPPLIER for d in docs.values())
    checks['original_and_return_stock_flags'] = (purchase['is_return'] == 0 and returned['is_return'] == 1
        and purchase['update_stock'] == returned['update_stock'] == 1)
    checks['frozen_posting_dates'] = purchase['posting_date'] == '2026-09-21' and returned['posting_date'] == '2026-09-22'
    checks['exact_return_document_and_child_link'] = (returned['return_against'] == purchase['name']
        and len(purchase['items']) == len(returned['items']) == 1
        and returned['items'][0]['purchase_invoice_item'] == purchase['items'][0]['name'])
    for label, doc, qty, net, tax, grand in (
        ('purchase', purchase, '10', '123.40', '12.34', '135.74'),
        ('return', returned, '-3', '-37.02', '-3.70', '-40.72'),
    ):
        item, taxes = doc['items'][0], doc['taxes']
        checks[label + '_quantity_rate_and_net'] = (item['item_code'] == ITEM and Decimal(item['qty']) == Decimal(qty)
            and Decimal(item['rate']) == Decimal('12.34') and Decimal(doc['base_net_total']) == Decimal(net))
        checks[label + '_explicit_test_charge_and_total'] = (len(taxes) == 1 and taxes[0]['category'] == 'Total'
            and taxes[0]['charge_type'] == 'On Net Total' and taxes[0]['add_deduct_tax'] == 'Add'
            and Decimal(taxes[0]['rate']) == Decimal('10') and Decimal(taxes[0]['base_tax_amount']) == Decimal(tax)
            and Decimal(doc['base_total_taxes_and_charges']) == Decimal(tax)
            and Decimal(doc['base_grand_total']) == Decimal(grand))
        voucher_rows = [r for r in records if r['voucher_id'] == doc['name']]
        checks[label + '_three_exact_gl_lines'] = len(voucher_rows) == 3 and {
            (r['account'], r['debit'], r['credit']) for r in voucher_rows} == EXPECTED_VOUCHERS[label]
        checks[label + '_gl_date_preserved'] = all(r['business_date'] == doc['posting_date'] for r in voucher_rows)
        entries = [r for r in stock if r['voucher_no'] == doc['name']]
        checks[label + '_one_exact_stock_movement'] = (len(entries) == 1
            and entries[0]['voucher_detail_no'] == item['name'] and entries[0]['company'] == COMPANY
            and entries[0]['warehouse'] == item['warehouse'] and entries[0]['item_code'] == ITEM
            and Decimal(entries[0]['actual_qty']) == Decimal(qty)
            and Decimal(entries[0]['stock_value_difference']) == Decimal(net))
    checks['only_two_posting_vouchers_and_movements'] = (len(records) == 6 and len(stock) == 2
        and {r['voucher_id'] for r in records} == {purchase['name'], returned['name']})
    aggregate = totals(records)
    checks['gl_totals_match_handwritten_expected'] = aggregate == EXPECTED
    checks['trial_balance_independently_matches'] = {
        k: tuple(Decimal(v) for v in pair) for k, pair in report['trial_balance'].items()} == EXPECTED
    tail = next((r for r in stock if r['voucher_no'] == returned['name']), {})
    checks['remaining_stock_seven_units_86_38'] = (Decimal(tail.get('qty_after_transaction', '-1')) == 7
        and Decimal(tail.get('stock_value', '-1')) == Decimal('86.38'))
    checks['net_payable_95_02'] = (aggregate.get('2202') == EXPECTED['2202']
        and sum(Decimal(d['outstanding_amount']) for d in docs.values()) == Decimal('95.02'))
    parsed, errors, *_ = parse_file(json.dumps(json_safe(export)).encode(), 'synthetic-tax-return.json')
    checks['normal_import_preview_preserves_six_gl_rows'] = not errors and len(parsed) == 6 and totals([r['record'] for r in parsed]) == EXPECTED
    checks['no_inferred_business_documents'] = all('invoice_id' not in r and 'order_id' not in r for r in records)
    return checks


def run(output):
    report = {'schema_version': 'erpnext-tax-return-v1', 'company': COMPANY, 'synthetic': True,
              'status': 'running', 'model_requests': 0, 'started_at': datetime.now(timezone.utc).isoformat(),
              'notice': '10 percent is a synthetic test charge, not a statutory tax rate.', 'checks': {},
              'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
                  'scripts/validate_erpnext_returns.py', 'scripts/validate_erpnext_semantics.py', 'backend/app/connectors.py')}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)
    start = perf_counter()
    try:
        key, secret = api_credentials()
        url = 'http://127.0.0.1:8090'
        with httpx.Client(base_url=url, headers={'Authorization': f'token {key}:{secret}'}, timeout=90, trust_env=False) as client:
            company = ensure_company(client, COMPANY, 'LTR', 'CNY')
            accounts = {r['account_name']: r['name'] for r in listed(client, 'Account', [['company', '=', COMPANY]], ['name', 'account_name'])}
            if 'Synthetic Input Tax' not in accounts:
                tax = create(client, 'Account', {'company': COMPANY, 'account_name': 'Synthetic Input Tax',
                    'parent_account': accounts['Current Assets'], 'account_type': 'Tax', 'account_currency': 'CNY'})
                accounts['Synthetic Input Tax'] = tax['name']
            warehouse = next(r['name'] for r in listed(client, 'Warehouse', [['company', '=', COMPANY], ['is_group', '=', 0]],
                ['name', 'warehouse_name']) if r['warehouse_name'] == 'Stores')
            if not listed(client, 'Supplier', [['name', '=', SUPPLIER]], ['name']):
                create(client, 'Supplier', {'supplier_name': SUPPLIER, 'supplier_group': 'All Supplier Groups', 'supplier_type': 'Company'})
            if not listed(client, 'Item', [['name', '=', ITEM]], ['name']):
                create(client, 'Item', {'item_code': ITEM, 'item_name': 'Synthetic tax return item', 'item_group': 'All Item Groups',
                    'stock_uom': 'Nos', 'is_stock_item': 1})
            common = {'supplier': SUPPLIER, 'currency': 'CNY', 'conversion_rate': 1, 'disable_rounded_total': 1,
                'ignore_pricing_rule': 1, 'update_stock': 1, 'set_posting_time': 1, 'posting_time': '10:00:00',
                'credit_to': accounts['Creditors'], 'due_date': '2026-10-21',
                'taxes': [{'charge_type': 'On Net Total', 'category': 'Total', 'add_deduct_tax': 'Add',
                    'account_head': accounts['Synthetic Input Tax'], 'rate': Decimal('10'),
                    'description': 'Synthetic test charge; not a statutory rate', 'cost_center': company['cost_center']}]}
            item = {'item_code': ITEM, 'uom': 'Nos', 'conversion_factor': 1, 'rate': Decimal('12.34'),
                'warehouse': warehouse, 'cost_center': company['cost_center'], 'expense_account': accounts['Cost of Goods Sold']}
            purchase = ensure_invoice(client, 'LedgerLens synthetic taxed purchase', {**common, 'posting_date': '2026-09-21',
                'bill_date': '2026-09-21', 'is_return': 0, 'items': [{**item, 'qty': 10}]})
            returned = ensure_invoice(client, 'LedgerLens synthetic taxed partial return', {**common, 'posting_date': '2026-09-22',
                'bill_date': '2026-09-22', 'is_return': 1, 'return_against': purchase['name'],
                'items': [{**item, 'qty': -3, 'purchase_invoice_item': purchase['items'][0]['name']}]})
            report['documents'] = {label: get(client, 'Purchase Invoice', doc['name']) for label, doc in [('purchase', purchase), ('return', returned)]}
            mapping = {accounts['Stock In Hand']: '1405', accounts['Synthetic Input Tax']: 'TEST-TAX', accounts['Creditors']: '2202'}
            report['export'] = asyncio.run(fetch_erpnext_gl(url, key, secret, COMPANY, '2026-09-01', '2026-09-30', mapping))
            report['trial_balance'] = trial_balance(client, COMPANY, mapping)
            report['stock'] = listed(client, 'Stock Ledger Entry', [['company', '=', COMPANY], ['item_code', '=', ITEM], ['is_cancelled', '=', 0]],
                ['name', 'voucher_no', 'voucher_detail_no', 'actual_qty', 'qty_after_transaction', 'stock_value',
                 'stock_value_difference', 'warehouse', 'company', 'item_code'])
            report['checks'] = inspect(report)
            report['status'] = 'completed' if all(report['checks'].values()) else 'completed_with_failures'
    except Exception as exc:
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:400], 'where': f'{Path(frame.filename).name}:{frame.lineno}'}
    report['duration_seconds'] = perf_counter() - start
    output.write_text(json.dumps(json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    result = run(parser.parse_args().output)
    print(json.dumps({k: result[k] for k in ('status', 'checks', 'error') if k in result}))
    raise SystemExit(0 if result['status'] == 'completed' else 1)
