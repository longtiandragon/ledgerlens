"""Local synthetic PO -> partial receipt -> invoice -> partial payment differential.

Handwritten expectations are separate from runtime adapters. No inferred invoices
are imported from GL. Only the named synthetic company receives fixture writes.
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

COMPANY = 'LedgerLens Procurement V2 Synthetic'
ITEM = 'LEDGERLENS-PROCUREMENT-SYNTHETIC-001'
SUPPLIER = 'LedgerLens Procurement Synthetic Supplier'
# 20 ordered at CNY12.34; 12 received/invoiced, CNY100 paid. No taxes or rounding.
EXPECTED = {'1405': (Decimal('148.08'), Decimal('0.00')),
            '2202-UNBILLED': (Decimal('148.08'), Decimal('148.08')),
            '2202': (Decimal('100.00'), Decimal('148.08')),
            '1002': (Decimal('0.00'), Decimal('100.00'))}


def ensure_document(client, doctype, marker_field, marker, document):
    found = listed(client, doctype, [['company', '=', COMPANY], [marker_field, '=', marker]],
                   ['name', 'docstatus'])
    if found:
        if len(found) != 1 or found[0]['docstatus'] != 1:
            raise ValueError('Ambiguous or unsubmitted procurement fixture; no automatic replacement')
        return get(client, doctype, found[0]['name'])
    return create(client, doctype, {'company': COMPANY, 'docstatus': 1, marker_field: marker, **document})


def run(output):
    report = {'schema_version': 'erpnext-procurement-v1', 'status': 'running', 'synthetic': True,
              'started_at': datetime.now(timezone.utc).isoformat(), 'model_requests': 0,
              'company': COMPANY, 'checks': {}, 'source_sha256': {
                  name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                  ('scripts/validate_erpnext_procurement.py', 'scripts/validate_erpnext_semantics.py',
                   'backend/app/connectors.py')}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)
    start = perf_counter()

    def save():
        output.write_text(json.dumps(json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def check(name, condition):
        report['checks'][name] = bool(condition)
        save()

    try:
        key, secret = api_credentials()
        url = 'http://127.0.0.1:8090'
        with httpx.Client(base_url=url, headers={'Authorization': f'token {key}:{secret}'},
                          timeout=90, trust_env=False) as client:
            company = ensure_company(client, COMPANY, 'LPT', 'CNY')
            accounts = {r['account_name']: r['name'] for r in listed(
                client, 'Account', [['company', '=', COMPANY]], ['name', 'account_name'])}
            warehouses = listed(client, 'Warehouse', [['company', '=', COMPANY], ['is_group', '=', 0]],
                                ['name', 'warehouse_name'])
            warehouse = next(r['name'] for r in warehouses if r['warehouse_name'] == 'Stores')
            if not listed(client, 'Supplier', [['name', '=', SUPPLIER]], ['name']):
                create(client, 'Supplier', {'supplier_name': SUPPLIER, 'supplier_group': 'All Supplier Groups',
                                            'supplier_type': 'Company'})
            if not listed(client, 'Item', [['name', '=', ITEM]], ['name']):
                create(client, 'Item', {'item_code': ITEM, 'item_name': 'Synthetic procurement item',
                                       'item_group': 'All Item Groups', 'stock_uom': 'Nos', 'is_stock_item': 1})
            common = {'supplier': SUPPLIER, 'currency': 'CNY', 'conversion_rate': 1,
                      'disable_rounded_total': 1, 'taxes': [], 'ignore_pricing_rule': 1}
            item = {'item_code': ITEM, 'uom': 'Nos', 'conversion_factor': 1,
                    'rate': '12.34', 'warehouse': warehouse, 'cost_center': company['cost_center']}
            po = ensure_document(client, 'Purchase Order', 'order_confirmation_no', 'LedgerLens synthetic PO', {
                **common, 'transaction_date': '2026-09-15', 'schedule_date': '2026-09-16',
                'items': [{**item, 'qty': 20}]})
            pr = ensure_document(client, 'Purchase Receipt', 'remarks', 'LedgerLens synthetic partial receipt', {
                **common, 'posting_date': '2026-09-16', 'posting_time': '10:00:00', 'set_posting_time': 1,
                'items': [{**item, 'qty': 12, 'received_qty': 12,
                           'purchase_order': po['name'], 'purchase_order_item': po['items'][0]['name']}]})
            pi = ensure_document(client, 'Purchase Invoice', 'bill_no', 'LedgerLens synthetic invoice', {
                **common, 'posting_date': '2026-09-17', 'posting_time': '10:00:00', 'set_posting_time': 1,
                'due_date': '2026-10-17',
                'bill_date': '2026-09-17', 'update_stock': 0, 'credit_to': accounts['Creditors'],
                'items': [{**item, 'qty': 12, 'purchase_order': po['name'], 'po_detail': po['items'][0]['name'],
                           'purchase_receipt': pr['name'], 'pr_detail': pr['items'][0]['name'],
                           'expense_account': accounts['Stock Received But Not Billed']}]})
            pe = ensure_document(client, 'Payment Entry', 'remarks', 'LedgerLens synthetic partial payment', {
                'payment_type': 'Pay', 'party_type': 'Supplier', 'party': SUPPLIER,
                'posting_date': '2026-09-18', 'paid_from': accounts['Cash'], 'paid_to': accounts['Creditors'],
                'paid_from_account_currency': 'CNY', 'paid_to_account_currency': 'CNY',
                'paid_amount': Decimal('100.00'), 'received_amount': Decimal('100.00'), 'source_exchange_rate': 1,
                'target_exchange_rate': 1, 'custom_remarks': 1,
                'references': [{'reference_doctype': 'Purchase Invoice', 'reference_name': pi['name'],
                                # ERPNext sums this external child field without coercion.
                                # Decimal writes an exact JSON number; our export API stays strings.
                                'allocated_amount': Decimal('100.00')}]})
            # Re-read after the payment and invoice update their upstream records.
            docs = {kind: get(client, kind, doc['name']) for kind, doc in
                    [('Purchase Order', po), ('Purchase Receipt', pr), ('Purchase Invoice', pi), ('Payment Entry', pe)]}
            po, pr, pi, pe = [docs[k] for k in ('Purchase Order', 'Purchase Receipt', 'Purchase Invoice', 'Payment Entry')]
            report['documents'] = docs
            check('all_posting_dates_preserved', po['transaction_date'] == '2026-09-15'
                  and pr['posting_date'] == '2026-09-16' and pi['posting_date'] == '2026-09-17'
                  and pe['posting_date'] == '2026-09-18')
            check('four_submitted_same_company_and_supplier', all(d['company'] == COMPANY and d['docstatus'] == 1
                  and (d.get('supplier') or d.get('party')) == SUPPLIER for d in docs.values()))
            check('po_twenty_units_246_80', Decimal(po['items'][0]['qty']) == 20
                  and Decimal(po['base_grand_total']) == Decimal('246.80'))
            check('po_received_twelve_units_sixty_percent', Decimal(po['items'][0]['received_qty']) == 12
                  and Decimal(po['per_received']) == 60)
            check('po_billed_sixty_percent', Decimal(po['per_billed']) == 60)
            check('receipt_links_exact_order_and_line', pr['items'][0]['purchase_order'] == po['name']
                  and pr['items'][0]['purchase_order_item'] == po['items'][0]['name'])
            check('receipt_twelve_units_148_08', Decimal(pr['items'][0]['qty']) == 12
                  and Decimal(pr['base_grand_total']) == Decimal('148.08'))
            check('invoice_links_order_receipt_and_child_lines', pi['items'][0]['purchase_order'] == po['name']
                  and pi['items'][0]['po_detail'] == po['items'][0]['name']
                  and pi['items'][0]['purchase_receipt'] == pr['name']
                  and pi['items'][0]['pr_detail'] == pr['items'][0]['name'])
            check('invoice_148_08_outstanding_48_08', Decimal(pi['base_grand_total']) == Decimal('148.08')
                  and Decimal(pi['outstanding_amount']) == Decimal('48.08'))
            check('payment_allocates_100_to_exact_invoice', len(pe['references']) == 1
                  and pe['references'][0]['reference_doctype'] == 'Purchase Invoice'
                  and pe['references'][0]['reference_name'] == pi['name']
                  and Decimal(pe['references'][0]['allocated_amount']) == Decimal('100.00'))
            check('payment_100_no_unallocated_or_difference', Decimal(pe['paid_amount']) == Decimal('100.00')
                  and Decimal(pe['unallocated_amount']) == 0 and Decimal(pe['difference_amount']) == 0)
            mapping = {accounts['Stock In Hand']: '1405', accounts['Stock Received But Not Billed']: '2202-UNBILLED',
                       accounts['Creditors']: '2202', accounts['Cash']: '1002'}
            export = asyncio.run(fetch_erpnext_gl(url, key, secret, COMPANY, '2026-09-01', '2026-09-30', mapping))
            report['export'] = export
            aggregate = totals(export['records'])
            report['export_totals'] = aggregate
            tb = trial_balance(client, COMPANY, mapping)
            report['trial_balance'] = tb
            check('gl_totals_match_handwritten_expectations', aggregate == EXPECTED)
            check('gl_totals_match_erp_trial_balance', aggregate == tb)
            check('only_receipt_invoice_payment_have_gl', {r['voucher_id'] for r in export['records']}
                  == {pr['name'], pi['name'], pe['name']})
            check('order_does_not_post_gl', po['name'] not in {r['voucher_id'] for r in export['records']})
            check('six_gl_lines_three_balanced_vouchers', len(export['records']) == 6 and all(
                sum(Decimal(r['debit']) - Decimal(r['credit']) for r in export['records'] if r['voucher_id'] == v) == 0
                for v in (pr['name'], pi['name'], pe['name'])))
            parsed, errors, *_ = parse_file(json.dumps(export).encode(), 'procurement-gl.json')
            check('normal_preview_preserves_all_six_gl_amounts', not errors and len(parsed) == 6
                  and totals([r['record'] for r in parsed]) == EXPECTED)
            check('gl_export_never_invents_purchase_records', all(r['kind'] == 'journal' and 'invoice_id' not in r
                  and 'order_id' not in r for r in export['records']))
            stock = listed(client, 'Stock Ledger Entry', [['company', '=', COMPANY], ['item_code', '=', ITEM],
                                                         ['is_cancelled', '=', 0]],
                           ['name', 'voucher_no', 'voucher_detail_no', 'actual_qty', 'qty_after_transaction',
                            'stock_value', 'stock_value_difference', 'warehouse', 'company'])
            report['stock'] = stock
            check('only_receipt_moves_stock', len(stock) == 1 and stock[0]['voucher_no'] == pr['name']
                  and stock[0]['voucher_detail_no'] == pr['items'][0]['name'])
            check('stock_twelve_units_value_148_08', len(stock) == 1 and Decimal(stock[0]['actual_qty']) == 12
                  and Decimal(stock[0]['qty_after_transaction']) == 12
                  and Decimal(stock[0]['stock_value']) == Decimal('148.08'))
            check('stock_gl_value_agrees', sum(Decimal(r['stock_value_difference']) for r in stock)
                  == aggregate['1405'][0] - aggregate['1405'][1])
        report['status'] = 'completed' if all(report['checks'].values()) else 'completed_with_failures'
    except Exception as exc:
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:400],
                           'where': f'{Path(frame.filename).name}:{frame.lineno}'}
    report['duration_seconds'] = perf_counter() - start
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    result = run(parser.parse_args().output)
    print(json.dumps({k: result[k] for k in ('status', 'checks', 'error') if k in result}, ensure_ascii=True))
    raise SystemExit(0 if result['status'] == 'completed' else 1)
