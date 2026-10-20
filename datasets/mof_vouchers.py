"""Offline, source-preserving projections of MOF's public EINV teaching examples.

This is NOT a general XBRL importer, invoice authenticator, or tax/accounting
opinion. Only already-present journal tuples are copied. PostingDate is an
explicit educational date convention, never independent evidence of occurrence.
Original XML stays inside the local import package, with per-row XML pointers.
No expectation, fault injection, or production accounting rule is used here.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

VERSION = 'mof-einv-example-projection-v1'
PAGE = 'https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/t20250519_3964020.htm'
URL = PAGE.rsplit('/', 1)[0] + '/P020250617561583232009.zip'
ZIP_SHA256 = 'eb75e173c1315742302df5edf74cf9e5aed51c2733b820a3236d9cfbb7f0bd8b'
SAMPLES = {
    'einv_spcl_20231231_22442000000921291350.xml': 'dd06323e0b006a98e93ef178a65778607e358b178c47e1c490f7b39ee4adfad5',
    'einv_ord_20220326_22442000000005170034.xml': '53722fc825a00fb9200ce6f79f0ce7c6487b2a8a02588f89b7f0b86fda243912',
}
NS = {'xbrli': 'http://www.xbrl.org/2003/instance',
      'einv': 'http://xbrl.mof.gov.cn/taxonomy/2023-12-31/einv'}
NOTICE = ('财政部公开标准示例的教学投影，不是真实企业账套或已验真发票；'
          '业务日期字段仅沿用原文过账日期，不证明实际业务发生日期。'
          '只复制原有分录，不从已付款、已查验或匹配标志生成银行/收货证据，不确认抵税资格。')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def one(parent, tag):
    nodes = parent.findall(tag, NS)
    if len(nodes) != 1 or len(nodes[0]) or not (nodes[0].text or '').strip():
        raise ValueError(f'Missing, duplicate, empty or non-scalar field: {tag}')
    return nodes[0]


def text(parent, tag):
    return one(parent, tag).text.strip()


def project(data: bytes, filename: str) -> dict:
    if len(data) > 1024 * 1024:
        raise ValueError('Example XML exceeds 1 MiB')
    xml = data.decode('utf-8-sig')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', xml, re.I):
        raise ValueError('DTD and entity declarations are forbidden')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError('Invalid XML') from exc
    if root.tag != '{' + NS['xbrli'] + '}xbrl':
        raise ValueError('Only the declared EINV XBRL profile is supported')
    contexts = root.findall('xbrli:context', NS)
    if len(contexts) != 1 or not contexts[0].get('id'):
        raise ValueError('Exactly one explicit entity/context is required')
    context = contexts[0].get('id')
    entity_id = text(contexts[0], 'xbrli:entity/xbrli:identifier')
    if entity_id != text(root, 'einv:UnifiedSocialCreditCodeOfAccountingEntity'):
        raise ValueError('Accounting entity and XBRL context disagree')
    # Reject conflicting contexts and nil facts rather than mixing scopes.
    for element in root.iter():
        if element.get('contextRef') not in (None, context):
            raise ValueError('A fact references another or missing context')
        if element.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true', '1'):
            raise ValueError('Nil facts are not supported in this profile')
    if text(root, 'einv:WhetherEinvoiceIsRedEinvoice') != 'false':
        raise ValueError('Red or unspecified invoices require a separate profile')
    units = {}
    for unit in root.findall('xbrli:unit', NS):
        unit_id = unit.get('id')
        if not unit_id or unit_id in units:
            raise ValueError('Duplicate or missing currency unit')
        units[unit_id] = text(unit, 'xbrli:measure')
    # QName meaning is validated, not just the spelling of the prefix.
    # iterparse items are (event, (prefix, URI)). Reject nested namespace rebinding.
    bindings = {}
    for _, (prefix, uri) in ET.iterparse(io.StringIO(xml), events=('start-ns',)):
        if prefix in bindings and bindings[prefix] != uri:
            raise ValueError('Namespace rebinding is unsupported')
        bindings[prefix] = uri
    rows, pointers = [], []
    documents = root.findall('einv:InformationOfAccountingDocumentsTuple', NS)
    if not 1 <= len(documents) <= 100:
        raise ValueError('Expected 1 to 100 accounting document tuples')
    invoice = text(root, 'einv:InvoiceNumber')
    seller = text(root, 'einv:SellerName')
    original_sha = sha(data)
    for doc_index, doc in enumerate(documents, 1):
        voucher = text(doc, 'einv:NumberOfAccountingDocuments')
        posted = text(doc, 'einv:PostingDate')
        date.fromisoformat(posted)
        period = text(doc, 'einv:AccountingPeriod')
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
            raise ValueError('Invalid explicit accounting period')
        entries = doc.findall('einv:InformationOfDebitAndCreditEntryTuple', NS)
        if not 1 <= len(entries) <= 1000:
            raise ValueError('Expected 1 to 1000 journal entries per document')
        for entry_index, entry in enumerate(entries, 1):
            direction = text(entry, 'einv:DebitOrCredit')
            if direction not in ('借方', '贷方'):
                raise ValueError('Unknown debit/credit direction')
            amount = one(entry, 'einv:RecordedAmount')
            measure = units.get(amount.get('unitRef'), '')
            prefix, separator, currency = measure.partition(':')
            if not separator or bindings.get(prefix) != 'http://www.xbrl.org/2003/iso4217' or currency != 'CNY':
                raise ValueError('Only explicitly declared CNY is supported')
            try:
                raw_amount = (amount.text or '').strip()
                if not re.fullmatch(r'\d+(?:\.\d+)?', raw_amount):
                    raise ValueError('Amount must be a nonnegative decimal lexical value')
                value = Decimal(raw_amount)
                if value > Decimal('999999999999.99') or value != value.quantize(Decimal('.01')):
                    raise ValueError('Amount is outside the exact-cent range')
            except InvalidOperation as exc:
                raise ValueError('Invalid amount') from exc
            account = text(entry, 'einv:NameOfGeneralLedgerSubject')
            sub = entry.findall('einv:NameOfSubsidiaryLedgerSubject', NS)
            if sub:
                account += '/' + text(entry, 'einv:NameOfSubsidiaryLedgerSubject')
            pointer = (f'./einv:InformationOfAccountingDocumentsTuple[{doc_index}]/'
                       f'einv:InformationOfDebitAndCreditEntryTuple[{entry_index}]')
            source_id = f'mof-example:{original_sha[:16]}:{doc_index}:{entry_index}'
            rows.append({'source_id': source_id, 'kind': 'journal', 'business_date': posted,
                         'period': period, 'currency': 'CNY', 'account': account,
                         'voucher_id': voucher, 'invoice_id': invoice, 'counterparty': seller,
                         'debit': format(value, '.2f') if direction == '借方' else '0.00',
                         'credit': format(value, '.2f') if direction == '贷方' else '0.00',
                         'description': f'{NOTICE} 原件SHA256={original_sha}；XML位置={pointer}'})
            pointers.append({'source_id': source_id, 'xml_path': pointer,
                             'date_basis': 'PostingDate; actual occurrence date is unknown',
                             'account_basis': 'verbatim source account name; no code inferred'})
    warnings = []
    if not filename.endswith('_' + invoice + '.xml'):
        warnings.append('Filename invoice number differs from XML; the XML value is retained, not repaired.')
    flags = {tag: text(root, 'einv:' + tag) for tag in (
        'WhetherEinvoiceHasBeenPaid', 'WhetherEinvoiceHasBeenChecked', 'MatchingStateBetweenBusinessDocumentsAndVatEinvoice')}
    transfer = root.findall('einv:WhetherInputVatHasBeenTransferredOut', NS)
    transfer_amount = root.findall('einv:AmountOfTransferredOutInputVat', NS)
    if transfer and transfer_amount and transfer[0].text == 'false' and Decimal(transfer_amount[0].text) != 0:
        warnings.append('Source says input VAT not transferred out but gives a nonzero transfer amount; no tax conclusion.')
    return {'schema_version': VERSION, 'data_nature': 'public_standard_example_projection',
            'notice': NOTICE, 'source': {'publisher': '财政部会计司', 'page_url': PAGE,
                'archive_url': URL, 'archive_sha256': ZIP_SHA256,
                'filename': filename, 'sha256': original_sha,
                'matches_pinned_official_example': SAMPLES.get(filename) == original_sha,
                'redistribution': 'local original only; no open-content license inferred',
                'original_xml_utf8': data.decode('utf-8'), 'namespaces': NS},
            'accounting_entity': {'id': entity_id, 'name': text(root, 'einv:NameOfAccountingEntity')},
            'unverified_source_flags': flags, 'warnings': warnings,
            'field_provenance': pointers, 'records': rows}


def verify_package(package: dict) -> None:
    """Reparse embedded original; reject edited rows, scope, provenance or metadata."""
    try:
        source = package['source']
        raw = source['original_xml_utf8'].encode('utf-8')
        if SAMPLES.get(source['filename']) != sha(raw):
            raise ValueError('Package does not contain a pinned official example')
        if json.dumps(project(raw, source['filename']), sort_keys=True, ensure_ascii=False) != json.dumps(
                package, sort_keys=True, ensure_ascii=False):
            raise ValueError('Projection differs from its immutable source')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Invalid self-contained example package') from exc


def collect(destination: Path, archive: Path | None = None) -> dict:
    """Create a new local bundle; fail on changed official bytes, never overwrite."""
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': VERSION, 'status': 'running', 'page_url': PAGE, 'archive_url': URL,
                'retrieved_at': datetime.now(timezone.utc).isoformat(), 'samples': []}
    try:
        if archive:
            data = archive.read_bytes()
        else:
            import httpx
            with httpx.stream('GET', URL, timeout=60, follow_redirects=True) as response:
                response.raise_for_status()
                data = b''
                for block in response.iter_bytes():
                    data += block
                    if len(data) > 5 * 1024 * 1024:
                        raise ValueError('Archive download exceeds 5 MiB')
        (destination / 'source.zip').write_bytes(data)
        manifest['archive_sha256'] = sha(data)
        if sha(data) != ZIP_SHA256:
            raise ValueError('Official archive has changed; independent review required')
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            for filename, digest in SAMPLES.items():
                matches = [item for item in bundle.infolist() if item.filename.split('/')[-1] == filename]
                if len(matches) != 1 or matches[0].file_size > 1024 * 1024:
                    raise ValueError('Missing, duplicate or oversized example')
                raw = bundle.read(matches[0])
                if sha(raw) != digest:
                    raise ValueError('Example hash differs from independently inspected source')
                (destination / filename).write_bytes(raw)
                output = project(raw, filename)
                output_name = filename + '.json'
                (destination / output_name).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                manifest['samples'].append({'file': filename, 'sha256': digest, 'projection': output_name,
                                            'records': len(output['records']), 'warnings': output['warnings']})
        manifest['status'] = 'complete'
    except Exception as exc:
        manifest['status'], manifest['error'] = 'failed', str(exc)
        raise
    finally:
        (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(collect(args.output, args.archive), ensure_ascii=False, indent=2))
