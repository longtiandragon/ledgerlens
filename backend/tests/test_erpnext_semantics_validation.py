from decimal import Decimal
import json

import httpx
import pytest

from scripts.validate_erpnext_semantics import decimal_json, ensure_company, ensure_voucher


def test_fixture_money_remains_string_and_exchange_rate_is_exact_json_decimal():
    wire = decimal_json({'credit_in_account_currency': '123.45', 'exchange_rate': Decimal('7.20')})
    assert '"exchange_rate":7.20' in wire
    decoded = json.loads(wire, parse_float=Decimal)
    assert decoded['credit_in_account_currency'] == '123.45'
    assert decoded['exchange_rate'] == Decimal('7.20')
    assert Decimal(decoded['credit_in_account_currency']) * decoded['exchange_rate'] == Decimal('888.8400')


@pytest.mark.parametrize('value', [7.2, Decimal('NaN'), Decimal('Infinity')])
def test_fixture_transport_rejects_binary_float_and_nonfinite(value):
    with pytest.raises(ValueError):
        decimal_json({'exchange_rate': value})


def test_replay_reuses_submitted_fixture_without_new_post():
    calls = []

    def handler(request):
        calls.append(request.method)
        assert json.loads(request.url.params['filters']) == [
            ['company', '=', 'Synthetic A'], ['user_remark', '=', 'fixture']]
        return httpx.Response(200, json={'data': [{'name': 'journal-1', 'docstatus': 1}]})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url='http://localhost') as client:
        assert ensure_voucher(client, 'Journal Entry', 'Synthetic A', 'fixture', {}) == 'journal-1'
    assert calls == ['GET']


@pytest.mark.parametrize('rows', [[{'name': 'j', 'docstatus': 0}], [{'name': 'j', 'docstatus': 2}],
                                 [{'name': 'a', 'docstatus': 1}, {'name': 'b', 'docstatus': 1}]])
def test_replay_never_resubmits_cancelled_drafts_or_ambiguous_matches(rows):
    def handler(request):
        assert request.method == 'GET'
        return httpx.Response(200, json={'data': rows})
    with httpx.Client(transport=httpx.MockTransport(handler), base_url='http://localhost') as client:
        with pytest.raises(ValueError, match='exactly one submitted'):
            ensure_voucher(client, 'Journal Entry', 'Synthetic A', 'fixture', {})


def test_existing_company_currency_cannot_be_silently_changed():
    def handler(request):
        assert request.method == 'GET'
        return httpx.Response(200, json={'data': [{'name': 'Synthetic A', 'default_currency': 'USD'}]})
    with httpx.Client(transport=httpx.MockTransport(handler), base_url='http://localhost') as client:
        with pytest.raises(ValueError, match='unexpected currency'):
            ensure_company(client, 'Synthetic A', 'SYN', 'CNY')


@pytest.fixture
def procurement_report():
    common = {'company': 'Synthetic', 'docstatus': 1}
    po = {**common, 'name': 'po', 'transaction_date': '2026-09-15', 'base_grand_total': '246.80',
          'per_received': '60', 'per_billed': '60',
          'items': [{'name': 'po-line', 'qty': '20', 'received_qty': '12'}]}
    pr = {**common, 'name': 'pr', 'posting_date': '2026-09-16', 'base_grand_total': '148.08',
          'items': [{'name': 'pr-line', 'qty': '12', 'purchase_order': 'po', 'purchase_order_item': 'po-line'}]}
    pi = {**common, 'name': 'pi', 'posting_date': '2026-09-17', 'base_grand_total': '148.08',
          'outstanding_amount': '48.08', 'items': [{'qty': '12', 'purchase_order': 'po', 'po_detail': 'po-line',
                                                   'purchase_receipt': 'pr', 'pr_detail': 'pr-line'}]}
    pe = {**common, 'name': 'pe', 'posting_date': '2026-09-18', 'paid_amount': '100',
          'unallocated_amount': '0', 'difference_amount': '0',
          'references': [{'reference_doctype': 'Purchase Invoice', 'reference_name': 'pi', 'allocated_amount': '100'}]}
    records = []
    for index, (voucher, account, debit, credit) in enumerate([
        ('pr', '1405', '148.08', '0.00'), ('pr', '2202-UNBILLED', '0.00', '148.08'),
        ('pi', '2202-UNBILLED', '148.08', '0.00'), ('pi', '2202', '0.00', '148.08'),
        ('pe', '2202', '100.00', '0.00'), ('pe', '1002', '0.00', '100.00')]):
        records.append({'source_id': str(index), 'voucher_id': voucher, 'account': account, 'debit': debit,
                        'credit': credit, 'kind': 'journal', 'currency': 'CNY'})
    return {'company': 'Synthetic', 'documents': {'Purchase Order': po, 'Purchase Receipt': pr,
                                                 'Purchase Invoice': pi, 'Payment Entry': pe},
            'export': {'company': 'Synthetic', 'records': records},
            'trial_balance': {'1405': ['148.08', '0.00'], '2202-UNBILLED': ['148.08', '148.08'],
                              '2202': ['100.00', '148.08'], '1002': ['0.00', '100.00']},
            'stock': [{'voucher_no': 'pr', 'voucher_detail_no': 'pr-line', 'actual_qty': '12',
                       'qty_after_transaction': '12', 'stock_value': '148.08', 'stock_value_difference': '148.08'}]}


@pytest.mark.parametrize('tamper', ['none', 'date', 'child_link', 'company', 'outstanding', 'allocation',
                                   'source_duplicate', 'float_money', 'stock', 'trial_balance'])
def test_procurement_report_recomputes_invariants_not_boolean_flags(procurement_report, tamper):
    from scripts.summarize_erpnext_semantics import verify_procurement
    report = procurement_report
    report['checks'] = {'all_passed': True}  # Does not grant authority to contradict the source observations.
    if tamper == 'date':
        report['documents']['Purchase Invoice']['posting_date'] = '2026-10-13'
    elif tamper == 'child_link':
        report['documents']['Purchase Invoice']['items'][0]['pr_detail'] = 'unrelated-line'
    elif tamper == 'company':
        report['export']['company'] = 'Other'
    elif tamper == 'outstanding':
        report['documents']['Purchase Invoice']['outstanding_amount'] = '0.00'
    elif tamper == 'allocation':
        report['documents']['Payment Entry']['references'][0]['reference_name'] = 'other-invoice'
    elif tamper == 'source_duplicate':
        report['export']['records'][1]['source_id'] = '0'
    elif tamper == 'float_money':
        report['export']['records'][0]['debit'] = 148.08
    elif tamper == 'stock':
        report['stock'][0]['stock_value'] = '148.09'
    elif tamper == 'trial_balance':
        report['trial_balance']['1002'][1] = '99.99'
    if tamper == 'none':
        verify_procurement(report)
    else:
        with pytest.raises(ValueError):
            verify_procurement(report)
