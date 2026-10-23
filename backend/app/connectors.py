"""Read-only adapters. Import results still pass through the normal preview flow."""
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlparse
import httpx


def decimal_text(value):
    """Preserve an explicit amount exactly; unsupported precision is not rounded."""
    if value is None or isinstance(value, (float, bool)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('ERPNext amount must be an explicit decimal, never null or binary float')
    try:
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) > Decimal('999999999999.99'):
            raise ValueError('ERPNext amount is non-finite or outside the supported range')
        cents = amount.quantize(Decimal('.01'))
    except InvalidOperation as exc:
        raise ValueError('ERPNext amount is not a valid decimal') from exc
    if amount != cents:
        raise ValueError('ERPNext amount has precision beyond cents; explicit policy is required')
    return format(cents, '.2f')


def _json_data(response):
    # Read numeric JSON tokens directly as Decimal, before any float conversion.
    def reject_constant(value):
        raise ValueError(f'ERPNext returned an invalid JSON number: {value}')

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'ERPNext returned a duplicate JSON field: {key}')
            result[key] = value
        return result

    payload = json.loads(response.content, parse_float=Decimal,
                         parse_constant=reject_constant, object_pairs_hook=unique_fields)
    if not isinstance(payload, dict) or 'data' not in payload:
        raise ValueError('ERPNext did not return a data payload')
    return payload['data']


async def fetch_erpnext_gl(base_url, api_key, api_secret, company, date_from, date_to, account_mapping, client=None):
    parsed = urlparse(base_url)
    if parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1'}:
        raise ValueError('ERPNext requires HTTPS except an explicit local demo')
    first_date, last_date = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if first_date > last_date:
        raise ValueError('ERPNext date range is reversed')
    if not isinstance(account_mapping, dict) or any(
            not isinstance(value, str) or not value.strip() for value in account_mapping.values()):
        raise ValueError('ERPNext requires an explicit non-empty account mapping')
    rows, seen, offset = [], set(), 0
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
    try:
        headers = {'Authorization': f'token {api_key}:{api_secret}'}
        response = await client.get(base_url.rstrip('/') + '/api/resource/Company/' + quote(company, safe=''),
                                    headers=headers)
        response.raise_for_status()
        company_data = _json_data(response)
        if not isinstance(company_data, dict) or company_data.get('name') != company:
            raise ValueError('ERPNext returned an invalid or cross-company currency record')
        # GL Entry.debit/credit are in Company.default_currency, not account_currency.
        currency = company_data.get('default_currency')
        if currency != 'CNY':
            raise ValueError('ERPNext company default_currency must explicitly be CNY; no conversion is inferred')
        while True:
            response = await client.get(base_url.rstrip('/') + '/api/resource/GL Entry', headers=headers, params={
                'fields': json.dumps(['name', 'posting_date', 'voucher_no', 'account', 'debit', 'credit',
                                      'party', 'against_voucher', 'company', 'is_cancelled']),
                'filters': json.dumps([['company', '=', company], ['posting_date', '>=', date_from],
                                       ['posting_date', '<=', date_to], ['is_cancelled', '=', 0]]),
                'order_by': 'name asc', 'limit_start': offset, 'limit_page_length': 200})
            response.raise_for_status()
            page = _json_data(response)
            if not isinstance(page, list):
                raise ValueError('ERPNext did not return records')
            for item in page:
                if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name']:
                    raise ValueError('ERPNext GL Entry must contain an explicit source name')
                if item['name'] in seen or item.get('company') != company:
                    raise ValueError('ERPNext pagination duplicate or cross-company response')
                seen.add(item['name'])
                if item['account'] not in account_mapping:
                    raise ValueError(f'Missing explicit account mapping: {item["account"]}')
                if 'debit' not in item or 'credit' not in item:
                    raise ValueError('ERPNext GL Entry must explicitly include debit and credit, including zero')
                if not first_date <= date.fromisoformat(item['posting_date']) <= last_date:
                    raise ValueError('ERPNext returned a posting date outside the requested range')
                # Filtering is a request, not evidence that the server honored it.
                # Immutable-ledger originals and their cancellation reversals must
                # never silently become active GL entries in this export scope.
                if type(item.get('is_cancelled')) is not int or item['is_cancelled'] != 0:
                    raise ValueError('ERPNext GL Entry must explicitly be active (is_cancelled integer 0)')
                rows.append({'source_id': item['name'], 'kind': 'journal', 'business_date': item['posting_date'],
                             'period': item['posting_date'][:7], 'voucher_id': item['voucher_no'],
                             'account': account_mapping[item['account']], 'debit': decimal_text(item['debit']),
                             'credit': decimal_text(item['credit']), 'counterparty': item.get('party') or '',
                             'reference': item.get('against_voucher') or '', 'currency': currency})
            offset += len(page)
            if len(page) < 200:
                break
            if offset >= 50000:
                raise ValueError('Pagination limit reached; incomplete results are not returned as success')
        return {'records': rows, 'source': 'ERPNext GL Entry', 'scope': 'general_ledger_only',
                'company': company, 'currency': currency, 'date_from': date_from, 'date_to': date_to,
                'notice': 'Company currency was read as CNY. Stock/order/invoice evidence is not inferred from GL. '
                          'Paginated reads are not an atomic ERP snapshot; export while source posting is paused.'}
    finally:
        if own:
            await client.aclose()


def read_bpi_xes(path: Path, case_limit=1000):
    """Stream complete traces; preserve source semantics, never invent journals."""
    from xml.etree.ElementTree import iterparse
    import gzip
    count = 0
    source = gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')
    with source:
        for _, node in iterparse(source, events=('end',)):
            if node.tag.rsplit('}', 1)[-1] != 'trace':
                continue
            attributes, events = {}, []
            for child in node:
                if child.tag.rsplit('}', 1)[-1] == 'event':
                    events.append({v.attrib.get('key'): v.attrib.get('value') for v in child})
                elif 'key' in child.attrib:
                    attributes[child.attrib['key']] = child.attrib.get('value')
            yield {'case_id': attributes.get('concept:name'), 'attributes': attributes, 'events': events,
                   'source': 'BPI2019', 'accounting_framework': None, 'complete_trace': True}
            node.clear()
            count += 1
            if count >= case_limit:
                break
