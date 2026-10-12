import json
from decimal import Decimal

import httpx
import pytest

from app.connectors import decimal_text, fetch_erpnext_gl, read_bpi_xes
from app.knowledge import build_corpus, search


@pytest.mark.asyncio
async def test_erpnext_only_reads_explicit_company_and_mapping():
    async def handler(request):
        assert request.method == 'GET'
        if '/api/resource/Company/' in str(request.url):
            return httpx.Response(200, json={'data': {'name': 'Demo', 'default_currency': 'CNY'}})
        assert '/api/resource/GL%20Entry' in str(request.url)
        assert json.loads(request.url.params['filters'])[0] == ['company', '=', 'Demo']
        return httpx.Response(200, content=b'{"data":[{"name":"g1","posting_date":"2026-09-30",'
            b'"voucher_no":"v1","company":"Demo","account":"Cash","debit":5.20,"credit":0}]}')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_erpnext_gl('https://erp.example', 'test', 'test', 'Demo', '2026-09-01',
                                       '2026-09-30', {'Cash': '1002'}, client)
    assert result['records'][0]['debit'] == '5.20'
    assert result['currency'] == 'CNY'
    assert result['scope'] == 'general_ledger_only'


@pytest.mark.asyncio
async def test_erpnext_wrong_company_rejected():
    async def handler(request):
        if '/api/resource/Company/' in str(request.url):
            return httpx.Response(200, json={'data': {'name': 'Demo', 'default_currency': 'CNY'}})
        return httpx.Response(200, json={'data': [{'name': 'x', 'company': 'Other'}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match='cross-company'):
            await fetch_erpnext_gl('https://erp.example', '', '', 'Demo', '2026-09-01', '2026-09-30', {}, client)


@pytest.mark.parametrize('value', [None, '', ' ', 0.1, True, 'NaN', 'Infinity', '0.001', '1e30'])
def test_erpnext_amount_never_infers_zero_or_rounds(value):
    with pytest.raises(ValueError):
        decimal_text(value)


def test_erpnext_decimal_amounts_keep_explicit_zero_and_exact_cents():
    assert decimal_text(0) == '0.00'
    assert decimal_text(Decimal('999999999999.99')) == '999999999999.99'


@pytest.mark.asyncio
@pytest.mark.parametrize('raw_amount', ['null', '""', 'NaN', '0.100000000000000005'])
async def test_erpnext_raw_json_invalid_precision_and_missing_amount_are_rejected(raw_amount):
    async def handler(request):
        if '/api/resource/Company/' in str(request.url):
            return httpx.Response(200, json={'data': {'name': 'Demo', 'default_currency': 'CNY'}})
        return httpx.Response(200, content=(
            '{"data":[{"name":"g1","posting_date":"2026-09-30","voucher_no":"v1",'
            '"company":"Demo","account":"Cash","credit":0,"debit":' + raw_amount + '}]}').encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await fetch_erpnext_gl('https://erp.example', '', '', 'Demo', '2026-09-01',
                                  '2026-09-30', {'Cash': '1002'}, client)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_field', ['debit', 'credit'])
async def test_erpnext_missing_debit_or_credit_does_not_become_zero(missing_field):
    row = {'name': 'g1', 'posting_date': '2026-09-30', 'voucher_no': 'v1',
           'company': 'Demo', 'account': 'Cash', 'debit': '0', 'credit': '5.20'}
    del row[missing_field]

    async def handler(request):
        if '/api/resource/Company/' in str(request.url):
            return httpx.Response(200, json={'data': {'name': 'Demo', 'default_currency': 'CNY'}})
        return httpx.Response(200, json={'data': [row]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match='explicitly include debit and credit'):
            await fetch_erpnext_gl('https://erp.example', '', '', 'Demo', '2026-09-01',
                                  '2026-09-30', {'Cash': '1002'}, client)


@pytest.mark.asyncio
@pytest.mark.parametrize('currency', [None, '', 'USD'])
async def test_erpnext_currency_is_verified_before_reading_gl(currency):
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        assert request.url.path == '/api/resource/Company/Demo'
        return httpx.Response(200, json={'data': {'name': 'Demo', 'default_currency': currency}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match='default_currency'):
            await fetch_erpnext_gl('https://erp.example', '', '', 'Demo', '2026-09-01',
                                  '2026-09-30', {'Cash': '1002'}, client)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(('overrides', 'error'), [
    ({'account': 'Unmapped'}, 'Missing explicit account mapping'),
    ({'posting_date': '2026-08-31'}, 'outside the requested range'),
])
async def test_erpnext_rejects_unmapped_accounts_and_out_of_scope_dates(overrides, error):
    row = {'name': 'g1', 'posting_date': '2026-09-30', 'voucher_no': 'v1',
           'company': 'Demo', 'account': 'Cash', 'debit': '5.20', 'credit': '0', **overrides}

    async def handler(request):
        if '/api/resource/Company/' in str(request.url):
            return httpx.Response(200, json={'data': {'name': 'Demo', 'default_currency': 'CNY'}})
        return httpx.Response(200, json={'data': [row]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match=error):
            await fetch_erpnext_gl('https://erp.example', '', '', 'Demo', '2026-09-01',
                                  '2026-09-30', {'Cash': '1002'}, client)


def test_bpi_preserves_complete_trace_not_accounting_entries(tmp_path):
    path = tmp_path / 'events.xes'
    path.write_text('<log><trace><string key="concept:name" value="p1"/><event><string key="concept:name" value="Receive"/></event><event><string key="concept:name" value="Invoice"/></event></trace><trace><string key="concept:name" value="p2"/></trace></log>')
    cases = list(read_bpi_xes(path, case_limit=1))
    assert len(cases) == 1 and len(cases[0]['events']) == 2
    assert cases[0]['accounting_framework'] is None


def test_reference_corpus_excludes_answers_and_filters_framework_time(tmp_path):
    from hashlib import sha256
    source = {'id': 'standard', 'framework': 'CAS', 'kind': 'policy', 'title': '独立规则测试',
              'url': 'https://www.mof.gov.cn/example', 'effective_from': '2025-01-01'}
    registry = tmp_path / 'trusted-registry.json'
    registry.write_text(json.dumps({'sources': [source]}), encoding='utf-8')
    source_dir = tmp_path / 'standard'
    source_dir.mkdir()
    raw_bytes = '<html><body>会计凭证原始记录差错更正规则</body></html>'.encode()
    raw_hash = sha256(raw_bytes).hexdigest()
    raw = source_dir / f'{raw_hash}.html'
    raw.write_bytes(raw_bytes)
    parsed = source_dir / 'parsed.json'
    parsed.write_text(json.dumps([{'page': 1, 'text': '会计凭证原始记录差错更正规则'}]), encoding='utf-8')
    rows = [{**source, 'status': 'fetched', 'parsed_path': str(parsed), 'raw_path': str(raw), 'sha256': raw_hash},
            {'id': 'answer', 'status': 'fetched', 'framework': 'CAS', 'kind': 'official_answer'}]
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(rows), encoding='utf-8')
    corpus = build_corpus(manifest, tmp_path / 'corpus-v2.json', registry=registry, source_root=tmp_path)
    assert len(corpus) == 1
    assert search(corpus, '差错更正', 'CAS', '2026-09')
    assert not search(corpus, '差错更正', 'SCAS', '2026-09')
    assert not search(corpus, '差错更正', 'CAS', '2024-09')
