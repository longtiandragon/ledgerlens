"""Real local ERPNext multi-company, account-currency and stock-ledger differential.

Only clearly named synthetic ERP companies are written, with idempotent fixtures.
The application GL adapter remains read-only. Independent expectations below are
hand-written; ERPNext's own trial balance and stock ledger provide a second path.
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
from app.connectors import _json_data, fetch_erpnext_gl  # noqa: E402
from app.imports import parse_file  # noqa: E402
from scripts.validate_erpnext import api_credentials, totals  # noqa: E402

COMPANIES = [('LedgerLens Semantic A Synthetic', 'LSA', 'CNY'),
             ('LedgerLens Semantic B Synthetic', 'LSB', 'CNY'),
             ('LedgerLens Semantic USD Synthetic', 'LSU', 'USD')]
EXPECTED_A = {'6602': (Decimal('888.84'), Decimal('0.00')),
              '1002': (Decimal('0.00'), Decimal('888.84')),
              '1405': (Decimal('123.40'), Decimal('37.02')),
              '6401': (Decimal('37.02'), Decimal('123.40'))}
EXPECTED_B = {'6602': (Decimal('321.09'), Decimal('0.00')),
              '1002': (Decimal('0.00'), Decimal('321.09'))}


def get(client, doctype, name):
    from urllib.parse import quote
    response = client.get('/api/resource/' + quote(doctype, safe='') + '/' + quote(name, safe=''))
    response.raise_for_status()
    return _json_data(response)


def listed(client, doctype, filters, fields, limit=1000):
    response = client.get('/api/resource/' + doctype, params={
        'filters': json.dumps(filters), 'fields': json.dumps(fields),
        'limit_page_length': limit, 'order_by': 'name asc'})
    response.raise_for_status()
    result = _json_data(response)
    if len(result) >= limit:
        raise ValueError('Incomplete synthetic fixture listing')
    return result


def decimal_json(value):
    """ERPNext expects JSON numeric tokens; render Decimal without binary float."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('Non-finite fixture decimal')
        return format(value, 'f')
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + ':' + decimal_json(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(decimal_json(v) for v in value) + ']'
    if isinstance(value, float):
        raise ValueError('Binary float is not allowed in synthetic postings')
    return json.dumps(value)


def create(client, doctype, document):
    response = client.post('/api/resource/' + doctype, content=decimal_json(document).encode(),
                           headers={'Content-Type': 'application/json'})
    if response.is_error:
        payload = response.json()
        raise ValueError(f'ERPNext {doctype}: {payload.get("exc_type")}: {str(payload.get("exception"))[:300]}')
    response.raise_for_status()
    return _json_data(response)


def ensure_company(client, name, abbr, currency):
    existing = listed(client, 'Company', [['name', '=', name]], ['name', 'default_currency'])
    if existing:
        if existing[0]['default_currency'] != currency:
            raise ValueError('Existing synthetic company has unexpected currency')
        return get(client, 'Company', name)
    return create(client, 'Company', {'company_name': name, 'abbr': abbr, 'country': 'China',
                                     'default_currency': currency,
                                     'create_chart_of_accounts_based_on': 'Standard Template',
                                     'chart_of_accounts': 'Standard'})


def ensure_voucher(client, doctype, company, marker, document):
    field = 'user_remark' if doctype == 'Journal Entry' else 'remarks'
    found = listed(client, doctype, [['company', '=', company], [field, '=', marker]], ['name', 'docstatus'])
    if found:
        if len(found) != 1 or found[0]['docstatus'] != 1:
            raise ValueError('Existing fixture must be exactly one submitted document')
        return found[0]['name']
    row = create(client, doctype, {'company': company, 'docstatus': 1, field: marker,
                                  'posting_date': '2026-09-12', **document})
    return row['name']


def trial_balance(client, company, mapping):
    response = client.post('/api/method/frappe.desk.query_report.run', json={
        'report_name': 'Trial Balance', 'filters': {'company': company, 'fiscal_year': '2026',
                                                  'from_date': '2026-09-01', 'to_date': '2026-09-30',
                                                  'show_zero_values': 0}})
    response.raise_for_status()
    # Parse the report's numeric JSON directly as Decimal, independently of the GL adapter.
    payload = json.loads(response.content, parse_float=Decimal)
    return {mapping[row['account']]: (Decimal(row['debit']), Decimal(row['credit']))
            for row in payload['message']['result'] if isinstance(row, dict) and row.get('account') in mapping}


def json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def run(output):
    report = {'schema_version': 'erpnext-semantics-v1', 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(), 'synthetic': True,
              'model_requests': 0, 'checks': {}, 'source_sha256': {
                  name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                  for name in ('scripts/validate_erpnext_semantics.py', 'backend/app/connectors.py')}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)
    started = perf_counter()

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def check(name, result):
        report['checks'][name] = bool(result)
        save()

    try:
        key, secret = api_credentials()
        url = 'http://127.0.0.1:8090'
        with httpx.Client(base_url=url, headers={'Authorization': f'token {key}:{secret}'},
                          timeout=90, trust_env=False) as client:
            companies = [ensure_company(client, *row) for row in COMPANIES]
            a, b, usd = [row['name'] for row in companies]
            roles = {}
            for company in (a, b):
                accounts = listed(client, 'Account', [['company', '=', company]],
                                  ['name', 'account_name', 'is_group', 'account_type', 'account_currency'])
                roles[company] = {row['account_name']: row['name'] for row in accounts}
            banks = listed(client, 'Account', [['company', '=', a], ['account_name', '=', 'Synthetic USD Bank']],
                           ['name', 'account_currency'])
            if not banks:
                bank = create(client, 'Account', {'company': a, 'account_name': 'Synthetic USD Bank',
                              'parent_account': roles[a]['Bank Accounts'], 'account_type': 'Bank',
                              'account_currency': 'USD'})
            else:
                bank = banks[0]
            check('foreign_account_explicit_usd', bank['account_currency'] == 'USD')
            journal_a = ensure_voucher(client, 'Journal Entry', a, 'LedgerLens semantic fixture: USD payment', {
                'voucher_type': 'Journal Entry', 'multi_currency': 1, 'accounts': [
                    {'account': roles[a]['Administrative Expenses'], 'debit_in_account_currency': '888.84',
                     'exchange_rate': 1, 'cost_center': companies[0]['cost_center']},
                    {'account': bank['name'], 'credit_in_account_currency': '123.45',
                     'exchange_rate': Decimal('7.20')}]})
            journal_b = ensure_voucher(client, 'Journal Entry', b, 'LedgerLens semantic fixture: company isolation', {
                'voucher_type': 'Journal Entry', 'accounts': [
                    {'account': roles[b]['Administrative Expenses'], 'debit_in_account_currency': '321.09',
                     'cost_center': companies[1]['cost_center']},
                    {'account': roles[b]['Cash'], 'credit_in_account_currency': '321.09'}]})
            item_code = 'LEDGERLENS-SEMANTIC-SYNTHETIC-001'
            if not listed(client, 'Item', [['name', '=', item_code]], ['name']):
                create(client, 'Item', {'item_code': item_code, 'item_name': 'Synthetic stock fixture',
                                       'item_group': 'All Item Groups', 'stock_uom': 'Nos', 'is_stock_item': 1})
            warehouses = listed(client, 'Warehouse', [['company', '=', a], ['is_group', '=', 0]],
                                ['name', 'warehouse_name'])
            warehouse = next(r['name'] for r in warehouses if r['warehouse_name'] == 'Stores')
            received = ensure_voucher(client, 'Stock Entry', a, 'LedgerLens semantic fixture: stock receipt', {
                'stock_entry_type': 'Material Receipt', 'set_posting_time': 1, 'posting_time': '10:00:00',
                'items': [{'item_code': item_code, 'qty': 10, 't_warehouse': warehouse,
                           'basic_rate': '12.34', 'expense_account': roles[a]['Stock Adjustment'],
                           'cost_center': companies[0]['cost_center']}]})
            issued = ensure_voucher(client, 'Stock Entry', a, 'LedgerLens semantic fixture: stock issue', {
                'stock_entry_type': 'Material Issue', 'set_posting_time': 1, 'posting_time': '11:00:00',
                'items': [{'item_code': item_code, 'qty': 3, 's_warehouse': warehouse,
                           'expense_account': roles[a]['Stock Adjustment'],
                           'cost_center': companies[0]['cost_center']}]})
            report['documents'] = {'journal_a': journal_a, 'journal_b': journal_b,
                                   'stock_receipt': received, 'stock_issue': issued}
            save()
            mapping_a = {roles[a]['Administrative Expenses']: '6602', bank['name']: '1002',
                         roles[a]['Stock In Hand']: '1405', roles[a]['Stock Adjustment']: '6401'}
            mapping_b = {roles[b]['Administrative Expenses']: '6602', roles[b]['Cash']: '1002'}
            exports = {}
            report['trial_balances'] = {}
            for company, mapping, expected, label in [(a, mapping_a, EXPECTED_A, 'a'),
                                                     (b, mapping_b, EXPECTED_B, 'b')]:
                exported = asyncio.run(fetch_erpnext_gl(url, key, secret, company, '2026-09-01', '2026-09-30', mapping))
                exports[company] = exported
                aggregate = totals(exported['records'])
                check(label + '_matches_manual_totals', aggregate == expected)
                independent = trial_balance(client, company, mapping)
                report['trial_balances'][label] = json_safe(independent)
                check(label + '_matches_erp_trial_balance', aggregate == independent)
                data = json.dumps(exported).encode()
                parsed, errors, *_ = parse_file(data, 'synthetic-erp.json')
                check(label + '_normal_import_preview', not errors and len(parsed) == len(exported['records']))
                check(label + '_import_amounts_unchanged', totals([r['record'] for r in parsed]) == expected)
                report[label + '_totals'] = {k: [str(x) for x in v] for k, v in aggregate.items()}
                artifact = output.with_name(output.stem + '-' + label + '-export.json')
                with artifact.open('xb') as handle:
                    handle.write(data)
                report[label + '_export_sha256'] = hashlib.sha256(data).hexdigest()
            a_vouchers = {r['voucher_id'] for r in exports[a]['records']}
            b_vouchers = {r['voucher_id'] for r in exports[b]['records']}
            check('companies_have_disjoint_vouchers', not a_vouchers & b_vouchers)
            check('a_has_only_three_expected_vouchers', a_vouchers == {journal_a, received, issued})
            check('b_has_only_its_one_voucher', b_vouchers == {journal_b})
            raw_gl = listed(client, 'GL Entry', [['company', '=', a], ['voucher_no', '=', journal_a],
                                               ['is_cancelled', '=', 0]],
                            ['name', 'account', 'debit', 'credit', 'account_currency',
                             'debit_in_account_currency', 'credit_in_account_currency'])
            usd_line = next(r for r in raw_gl if r['account'] == bank['name'])
            report['foreign_currency_source_line'] = json_safe(usd_line)
            check('raw_usd_amount_123_45_base_cny_888_84', usd_line['account_currency'] == 'USD'
                  and Decimal(usd_line['credit_in_account_currency']) == Decimal('123.45')
                  and Decimal(usd_line['credit']) == Decimal('888.84'))
            imported_usd = next(r for r in exports[a]['records'] if r['source_id'] == usd_line['name'])
            check('adapter_exports_base_currency_not_account_amount', imported_usd['currency'] == 'CNY'
                  and imported_usd['credit'] == '888.84')
            sl = listed(client, 'Stock Ledger Entry', [['company', '=', a], ['item_code', '=', item_code],
                                                      ['warehouse', '=', warehouse], ['is_cancelled', '=', 0]],
                        ['name', 'voucher_no', 'actual_qty', 'qty_after_transaction', 'stock_value_difference',
                         'stock_value', 'voucher_detail_no'])
            receipt = [r for r in sl if r['voucher_no'] == received]
            issue = [r for r in sl if r['voucher_no'] == issued]
            check('two_stock_ledger_lines_link_to_documents', len(sl) == 2 and len(receipt) == len(issue) == 1)
            check('receipt_10_units_123_40', Decimal(receipt[0]['actual_qty']) == Decimal('10')
                  and Decimal(receipt[0]['stock_value_difference']) == Decimal('123.40'))
            check('issue_3_units_37_02_remaining_7_and_86_38', Decimal(issue[0]['actual_qty']) == Decimal('-3')
                  and Decimal(issue[0]['stock_value_difference']) == Decimal('-37.02')
                  and Decimal(issue[0]['qty_after_transaction']) == Decimal('7')
                  and Decimal(issue[0]['stock_value']) == Decimal('86.38'))
            report['stock_documents'] = {}
            for name, rows in ((received, receipt), (issued, issue)):
                doc = get(client, 'Stock Entry', name)
                report['stock_documents'][name] = json_safe(doc)
                check('stock_detail_link_' + ('receipt' if name == received else 'issue'),
                      rows[0]['voucher_detail_no'] == doc['items'][0]['name'] and doc['company'] == a)
            check('gl_does_not_invent_procurement_evidence', all(r['kind'] == 'journal'
                  and 'invoice_id' not in r and 'order_id' not in r for r in exports[a]['records']))
            try:
                asyncio.run(fetch_erpnext_gl(url, key, secret, usd, '2026-09-01', '2026-09-30', {}))
            except ValueError as exc:
                check('usd_company_rejected_without_inferred_conversion', 'default_currency' in str(exc))
            else:
                check('usd_company_rejected_without_inferred_conversion', False)
            report['stock_observations'] = [{k: str(v) if isinstance(v, Decimal) else v for k, v in r.items()}
                                             for r in sl]
        report['status'] = 'completed' if all(report['checks'].values()) else 'completed_with_failures'
    except Exception as exc:
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:400],
                           'where': f'{Path(frame.filename).name}:{frame.lineno}'}
    report['duration_seconds'] = perf_counter() - started
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = run(parser.parse_args().output)
    print(json.dumps({k: result[k] for k in ('status', 'checks', 'error') if k in result}, ensure_ascii=True))
    raise SystemExit(0 if result['status'] == 'completed' else 1)
