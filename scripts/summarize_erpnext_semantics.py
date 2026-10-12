"""Recompute ERP semantic acceptance from saved exports and source observations."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def amounts(records):
    result = {}
    for row in records:
        require(row['currency'] == 'CNY' and row['kind'] == 'journal', 'Unexpected export scope')
        require(isinstance(row['debit'], str) and isinstance(row['credit'], str), 'Money must be strings')
        debit, credit = Decimal(row['debit']), Decimal(row['credit'])
        require(debit.is_finite() and credit.is_finite(), 'Non-finite amounts')
        pair = result.setdefault(row['account'], [Decimal(0), Decimal(0)])
        pair[0] += debit
        pair[1] += credit
    return {k: [format(v, '.2f') for v in pair] for k, pair in result.items()}


def verify_header(report, sources, schema):
    require(report['schema_version'] == schema and report['status'] == 'completed', 'Incomplete experiment')
    require(report.get('synthetic') is True and report.get('model_requests') == 0, 'Unexpected experiment scope')
    require(len(report['checks']) == 21 and all(v is True for v in report['checks'].values()), 'Unpassed checks')
    require(set(report['source_sha256']) == set(sources), 'Missing source hashes')
    for name, expected in report['source_sha256'].items():
        require(digest(ROOT / name) == expected, 'Changed experiment code; preserve historical report')


def verify_currency_stock(path):
    report = read(path)
    verify_header(report, ['scripts/validate_erpnext_semantics.py', 'backend/app/connectors.py'], 'erpnext-semantics-v1')
    expected = {'a': {'6602': ['888.84', '0.00'], '1002': ['0.00', '888.84'],
                      '1405': ['123.40', '37.02'], '6401': ['37.02', '123.40']},
                'b': {'6602': ['321.09', '0.00'], '1002': ['0.00', '321.09']}}
    exports = {}
    for label in ('a', 'b'):
        export_path = path.with_name(path.stem + '-' + label + '-export.json')
        require(digest(export_path) == report[label + '_export_sha256'], 'Changed export bytes')
        export = read(export_path)
        require(export['company'] == f'LedgerLens Semantic {label.upper()} Synthetic', 'Wrong company')
        records = export['records']
        require(len({r['source_id'] for r in records}) == len(records), 'Duplicate source rows')
        require(amounts(records) == expected[label] == report[label + '_totals'], 'Export/manual total mismatch')
        require({k: [format(Decimal(v), '.2f') for v in pair] for k, pair in report['trial_balances'][label].items()}
                == expected[label], 'Independent ERP trial balance mismatch')
        exports[label] = records
    docs = report['documents']
    require({r['voucher_id'] for r in exports['a']} == {docs['journal_a'], docs['stock_receipt'], docs['stock_issue']}
            and {r['voucher_id'] for r in exports['b']} == {docs['journal_b']}, 'Wrong voucher scope')
    line = report['foreign_currency_source_line']
    require(line['account_currency'] == 'USD' and Decimal(line['credit_in_account_currency']) == Decimal('123.45')
            and Decimal(line['credit']) == Decimal('888.84'), 'Foreign/base currency evidence mismatch')
    matching = [r for r in exports['a'] if r['source_id'] == line['name']]
    require(len(matching) == 1 and matching[0]['credit'] == '888.84', 'Foreign amount export mismatch')
    stock = report['stock_observations']
    require(len(stock) == 2, 'Unexpected stock rows')
    for key, qty, value, remaining in [('stock_receipt', '10', '123.40', '10'), ('stock_issue', '-3', '-37.02', '7')]:
        rows = [r for r in stock if r['voucher_no'] == docs[key]]
        require(len(rows) == 1, 'Missing stock voucher')
        row = rows[0]
        require(Decimal(row['actual_qty']) == Decimal(qty) and Decimal(row['stock_value_difference']) == Decimal(value)
                and Decimal(row['qty_after_transaction']) == Decimal(remaining), 'Stock movement mismatch')
        doc = report['stock_documents'][docs[key]]
        require(doc['company'] == 'LedgerLens Semantic A Synthetic' and doc['docstatus'] == 1
                and row['voucher_detail_no'] in {r['name'] for r in doc['items']}, 'Stock source link mismatch')
    require(Decimal(next(r for r in stock if r['voucher_no'] == docs['stock_issue'])['stock_value']) == Decimal('86.38'),
            'Wrong remaining stock value')
    return report


def verify_procurement(report):
    """Independent replay of key accounting invariants; never trusts boolean checks alone."""
    docs = report['documents']
    po, pr, pi, pe = [docs[k] for k in ('Purchase Order', 'Purchase Receipt', 'Purchase Invoice', 'Payment Entry')]
    require(all(d['docstatus'] == 1 and d['company'] == report['company'] for d in docs.values()), 'Document scope mismatch')
    require([po['transaction_date'], pr['posting_date'], pi['posting_date'], pe['posting_date']] ==
            ['2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18'], 'Posting dates not preserved')
    require(Decimal(po['items'][0]['qty']) == 20 and Decimal(po['items'][0]['received_qty']) == 12
            and Decimal(po['per_received']) == 60 and Decimal(po['per_billed']) == 60, 'Order remaining quantity mismatch')
    require(Decimal(po['base_grand_total']) == Decimal('246.80') and Decimal(pr['base_grand_total']) == Decimal('148.08')
            and Decimal(pi['base_grand_total']) == Decimal('148.08'), 'Document amounts mismatch')
    receipt_line, invoice_line = pr['items'][0], pi['items'][0]
    require(Decimal(receipt_line['qty']) == 12 and Decimal(invoice_line['qty']) == 12, 'Received/invoiced quantity mismatch')
    require(receipt_line['purchase_order'] == po['name'] and receipt_line['purchase_order_item'] == po['items'][0]['name'],
            'Receipt/order detail link mismatch')
    require(invoice_line['purchase_order'] == po['name'] and invoice_line['po_detail'] == po['items'][0]['name']
            and invoice_line['purchase_receipt'] == pr['name'] and invoice_line['pr_detail'] == receipt_line['name'],
            'Invoice/order/receipt detail link mismatch')
    require(len(pe['references']) == 1 and pe['references'][0]['reference_doctype'] == 'Purchase Invoice'
            and pe['references'][0]['reference_name'] == pi['name'], 'Payment/invoice link mismatch')
    require(Decimal(pe['paid_amount']) == Decimal('100.00') and Decimal(pe['references'][0]['allocated_amount']) == 100
            and Decimal(pe['unallocated_amount']) == 0 and Decimal(pe['difference_amount']) == 0
            and Decimal(pi['outstanding_amount']) == Decimal('48.08'), 'Payment/outstanding mismatch')
    expected = {'1405': ['148.08', '0.00'], '2202-UNBILLED': ['148.08', '148.08'],
                '2202': ['100.00', '148.08'], '1002': ['0.00', '100.00']}
    records = report['export']['records']
    require(report['export']['company'] == report['company'], 'Export company mismatch')
    require(len(records) == len({r['source_id'] for r in records}) == 6 and amounts(records) == expected, 'GL export mismatch')
    require({r['voucher_id'] for r in records} == {pr['name'], pi['name'], pe['name']}, 'GL voucher scope mismatch')
    require({k: [format(Decimal(v), '.2f') for v in p] for k, p in report['trial_balance'].items()} == expected,
            'Independent trial balance mismatch')
    stock = report['stock']
    require(len(stock) == 1 and stock[0]['voucher_no'] == pr['name'] and stock[0]['voucher_detail_no'] == receipt_line['name'],
            'Stock/receipt detail link mismatch')
    require(Decimal(stock[0]['actual_qty']) == Decimal(stock[0]['qty_after_transaction']) == 12
            and Decimal(stock[0]['stock_value']) == Decimal(stock[0]['stock_value_difference']) == Decimal('148.08'),
            'Stock amount or quantity mismatch')


def summarize(semantics, procurement, replay):
    basic = verify_currency_stock(semantics)
    chain, repeated = read(procurement), read(replay)
    for report in (chain, repeated):
        verify_header(report, ['scripts/validate_erpnext_procurement.py', 'scripts/validate_erpnext_semantics.py',
                               'backend/app/connectors.py'], 'erpnext-procurement-v1')
        verify_procurement(report)
    names = {k: v['name'] for k, v in chain['documents'].items()}
    require(names == {k: v['name'] for k, v in repeated['documents'].items()}, 'Replay created different vouchers')
    require(chain['export'] == repeated['export'] and chain['stock'] == repeated['stock'], 'Replay changed GL/stock')
    return {'schema_version': 'erpnext-semantic-summary-v1', 'status': 'completed', 'synthetic': True,
            'model_requests': 0, 'erpnext_image': 'frappe/erpnext:v16.50.0',
            'report_sha256': {str(p.name): digest(p) for p in (semantics, procurement, replay)},
            'source_sha256': chain['source_sha256'], 'currency_stock_checks': basic['checks'],
            'procurement_checks': chain['checks'], 'procurement_replay_identical_gl_stock_and_document_ids': True,
            'foreign_currency': {'account_currency': 'USD', 'account_credit': '123.45', 'fixed_exchange_rate': '7.20',
                                 'export_currency': 'CNY', 'export_credit': '888.84'},
            'stock_movement': {'received_quantity': '10', 'issued_quantity': '3', 'remaining_quantity': '7',
                               'remaining_value_cny': '86.38'},
            'procurement': {'ordered_quantity': '20', 'received_quantity': '12', 'not_received_quantity': '8',
                            'invoice_cny': '148.08', 'paid_cny': '100.00', 'outstanding_cny': '48.08'},
            'durations_seconds': [basic['duration_seconds'], chain['duration_seconds'], repeated['duration_seconds']],
            'limits': ['Local hand-authored synthetic fixtures, not real-company or expert acceptance.',
                       'CNY company GL only; USD company remains explicitly unsupported.',
                       'USD account is exported in existing ERP company currency; no inferred conversion.',
                       'Procurement source documents are compared here, not imported as runtime facts from GL.',
                       'No tax, return, cancellation chain, landed cost, exchange gain/loss or concurrent posting acceptance.',
                       'Historical failed fixtures and reports are retained; their submitted documents were not rewritten.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('semantics', 'procurement', 'replay', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(args.semantics, args.procurement, args.replay)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print('Recomputed currency/stock and procurement invariants; identical procurement replay verified.')
