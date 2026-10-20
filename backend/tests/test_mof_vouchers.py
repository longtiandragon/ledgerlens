"""Independent small XML cases; expected amounts and errors are handwritten."""
from copy import deepcopy
import json

import pytest

from datasets.mof_vouchers import project, verify_package


def example():
    # Hand-authored fixture, NOT copied from a supposed real taxpayer invoice.
    return '''<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance"
        xmlns:e="http://xbrl.mof.gov.cn/taxonomy/2023-12-31/einv"
        xmlns:i="http://www.xbrl.org/2003/iso4217">
      <x:context id="c"><x:entity><x:identifier>TEST-ENTITY</x:identifier></x:entity></x:context>
      <x:unit id="u"><x:measure>i:CNY</x:measure></x:unit>
      <e:UnifiedSocialCreditCodeOfAccountingEntity>TEST-ENTITY</e:UnifiedSocialCreditCodeOfAccountingEntity>
      <e:NameOfAccountingEntity>独立合成测试</e:NameOfAccountingEntity>
      <e:InvoiceNumber>123</e:InvoiceNumber><e:SellerName>合成销售方</e:SellerName>
      <e:WhetherEinvoiceIsRedEinvoice>false</e:WhetherEinvoiceIsRedEinvoice>
      <e:WhetherEinvoiceHasBeenPaid>true</e:WhetherEinvoiceHasBeenPaid>
      <e:WhetherEinvoiceHasBeenChecked>true</e:WhetherEinvoiceHasBeenChecked>
      <e:MatchingStateBetweenBusinessDocumentsAndVatEinvoice>已匹配</e:MatchingStateBetweenBusinessDocumentsAndVatEinvoice>
      <e:InformationOfAccountingDocumentsTuple>
       <e:NumberOfAccountingDocuments>V1</e:NumberOfAccountingDocuments>
       <e:PostingDate>2023-12-31</e:PostingDate><e:AccountingPeriod>2023-12</e:AccountingPeriod>
       <e:InformationOfDebitAndCreditEntryTuple>
        <e:DebitOrCredit>借方</e:DebitOrCredit><e:NameOfGeneralLedgerSubject>原材料</e:NameOfGeneralLedgerSubject>
        <e:RecordedAmount contextRef="c" unitRef="u">0.10</e:RecordedAmount>
       </e:InformationOfDebitAndCreditEntryTuple>
       <e:InformationOfDebitAndCreditEntryTuple>
        <e:DebitOrCredit>贷方</e:DebitOrCredit><e:NameOfGeneralLedgerSubject>银行存款</e:NameOfGeneralLedgerSubject>
        <e:RecordedAmount contextRef="c" unitRef="u">0.10</e:RecordedAmount>
       </e:InformationOfDebitAndCreditEntryTuple>
      </e:InformationOfAccountingDocumentsTuple>
    </x:xbrl>'''.encode()


def test_exact_amount_source_and_no_fabricated_bank_or_invoice():
    raw = example()
    result = project(raw, 'test_123.xml')
    assert [r['kind'] for r in result['records']] == ['journal', 'journal']
    assert [(r['debit'], r['credit']) for r in result['records']] == [('0.10', '0.00'), ('0.00', '0.10')]
    assert result['source']['original_xml_utf8'].encode() == raw
    assert not result['source']['matches_pinned_official_example']
    assert all('actual occurrence date is unknown' in r['date_basis'] for r in result['field_provenance'])
    assert result['unverified_source_flags']['WhetherEinvoiceHasBeenPaid'] == 'true'
    assert len({r['source_id'] for r in result['records']}) == 2
    assert project(raw, 'test_123.xml') == result


@pytest.mark.parametrize('old,new', [
    ('0.10', '0.001'), ('0.10', '-1.00'), ('0.10', 'NaN'), ('0.10', '1e2'),
    ('0.10', '1000000000000.00'), ('i:CNY', 'i:USD'),
    ('http://www.xbrl.org/2003/iso4217', 'https://fake.invalid/currency'),
    ('unitRef="u"', 'unitRef="missing"'), ('contextRef="c"', 'contextRef="other"'),
    ('<e:DebitOrCredit>借方', '<e:DebitOrCredit>借贷'),
    ('<e:WhetherEinvoiceIsRedEinvoice>false', '<e:WhetherEinvoiceIsRedEinvoice>true'),
    ('<e:AccountingPeriod>2023-12', '<e:AccountingPeriod>2023-13'),
    ('<e:PostingDate>2023-12-31', '<e:PostingDate>2023-02-30'),
    ('<x:identifier>TEST-ENTITY', '<x:identifier>OTHER-ENTITY'),
    ('<e:InvoiceNumber>123</e:InvoiceNumber>', '<e:InvoiceNumber>123</e:InvoiceNumber><e:InvoiceNumber>456</e:InvoiceNumber>'),
])
def test_ambiguous_or_unsupported_source_rejected(old, new):
    with pytest.raises(ValueError):
        project(example().decode().replace(old, new).encode(), 'test.xml')


def test_xml_entities_size_and_bad_structure_rejected():
    for raw in [b'<!DOCTYPE x [<!ENTITY x "boom">]>' + example(), b'<broken', b'<other/>', b'x' * (1024 * 1024 + 1)]:
        with pytest.raises(ValueError):
            project(raw, 'test.xml')


def test_training_fixture_cannot_claim_official_source():
    package = project(example(), 'test_123.xml')
    with pytest.raises(ValueError, match='pinned official'):
        verify_package(package)


def test_offline_collector_and_tamper_gate(tmp_path):
    # Optional real-source check never downloads during tests.
    from pathlib import Path
    from datasets.mof_vouchers import collect
    archive = Path('.local/mof-vouchers-2025/digital-invoice.zip')
    if not archive.exists():
        pytest.skip('Optional independently downloaded MOF archive is absent')
    manifest = collect(tmp_path / 'bundle', archive)
    assert manifest['status'] == 'complete'
    assert [r['records'] for r in manifest['samples']] == [3, 2]
    for sample in manifest['samples']:
        package = json.loads((tmp_path / 'bundle' / sample['projection']).read_text(encoding='utf-8'))
        verify_package(package)
        edited = deepcopy(package)
        edited['records'][0]['debit'] = '0.01'
        with pytest.raises(ValueError, match='differs'):
            verify_package(edited)
    with pytest.raises(FileExistsError):
        collect(tmp_path / 'bundle', archive)


def test_bad_archive_leaves_failure_report(tmp_path):
    from datasets.mof_vouchers import collect
    archive = tmp_path / 'bad.zip'
    archive.write_bytes(b'not the official archive')
    with pytest.raises(ValueError, match='changed'):
        collect(tmp_path / 'failed', archive)
    assert json.loads((tmp_path / 'failed/manifest.json').read_text())['status'] == 'failed'
