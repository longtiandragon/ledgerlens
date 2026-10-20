"""Public-ledger extract: arithmetic observations and data-quality checks.

Source: State of Oklahoma OMES "General Ledger" on data.gov (CC-BY), quarterly CSV of posted
totals summed by chartfield combination. This is real accounting data, not a company's books:
US fund accounting in USD, so CAS/SCAS rules do not apply and the product's CNY import refuses
it by design. Check exact-cent parsing with money() and independently sum the source amounts.
Neither zero nor nonzero group net establishes accounting correctness: the catalog does not
attest complete double-entry coverage for these group keys. Class presence cannot prove it.
Streaming only; the file is never loaded into memory at once.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.domain import money  # noqa: E402

VERSION = 'public-ledger-checks-v2'
AMOUNT = 'SUM(L.POSTED_TOTAL_AMT)'
KEYS = ('BUSINESS_UNIT', 'LEDGER', 'FISCAL_YEAR', 'ACCOUNTING_PERIOD', 'FUND_CODE')


def independent(text: str) -> Decimal | None:
    # Explicit resource bound for this offline checker; unsupported values remain counted.
    if len(text) > 1024:
        return None
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return None
    return value if (value.is_finite() and abs(value.as_tuple().exponent) <= 256
                     and len(value.as_tuple().digits) <= 256) else None


def exact_add(left: Decimal, right: Decimal) -> Decimal:
    """Size precision from both operands, including carry; never round source sub-cents."""
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted()) - min(
            left.as_tuple().exponent, right.as_tuple().exponent) + 2
        return left + right


def encoding_of(path: Path) -> tuple[str, int | None]:
    """UTF-8 if the whole file decodes strictly; otherwise strict cp1252, with the first failing offset."""
    decoder, offset = __import__('codecs').getincrementaldecoder('utf-8')('strict'), 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            buffered = len(decoder.getstate()[0])
            try:
                decoder.decode(block)
            except UnicodeDecodeError as exc:
                return 'cp1252', offset - buffered + exc.start
            offset += len(block)
        buffered = len(decoder.getstate()[0])
        try:
            decoder.decode(b'', final=True)
        except UnicodeDecodeError as exc:
            return 'cp1252', offset - buffered + exc.start
    return 'utf-8-sig', None


def check(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    encoding, utf8_failure = encoding_of(path)
    groups: dict[tuple, Decimal] = defaultdict(lambda: Decimal('0'))
    source_groups: dict[tuple, Decimal] = defaultdict(lambda: Decimal('0'))
    rejected_groups: dict[tuple, int] = defaultdict(int)
    unavailable_groups: dict[tuple, int] = defaultdict(int)
    rows = rejected = disagreements = 0
    ledgers, periods, seen, duplicates = defaultdict(int), set(), set(), 0
    classes: dict[str, list] = defaultdict(lambda: [0, Decimal('0')])   # first account digit -> rows, net
    period_net: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    examples = []
    with path.open(encoding=encoding, errors='strict', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames
        if not fields or len(set(fields)) != len(fields) or not set((*KEYS, 'ACCOUNT', AMOUNT)) <= set(fields):
            raise ValueError('Missing or duplicate CSV columns')
        for row in reader:
            rows += 1
            if None in row or None in row.values() or any(not row[k].strip() for k in (*KEYS, 'ACCOUNT')):
                raise ValueError(f'Malformed CSV or missing scope key at data row {rows}')
            key = tuple(row[k].strip() for k in KEYS)
            ledgers[key[1]] += 1
            periods.add((key[2], key[3]))
            # JSON array framing prevents delimiters inside cells from aliasing distinct rows.
            fingerprint = hashlib.sha256(json.dumps([row[k] for k in fields], ensure_ascii=False,
                                                    separators=(',', ':')).encode('utf-8')).digest()
            duplicates += fingerprint in seen
            seen.add(fingerprint)
            raw = row[AMOUNT]
            reference = independent(raw)
            source_groups.setdefault(key, Decimal('0'))
            if reference is None:
                unavailable_groups[key] += 1
            else:
                source_groups[key] = exact_add(source_groups[key], reference)
            try:
                parsed = money(raw.strip())
            except ValueError:
                rejected += 1
                rejected_groups[key] += 1
                if len(examples) < 5:
                    examples.append({'row': rows, 'reason': 'money() rejected', 'independent_parse': str(reference)})
                continue
            if reference is None or reference != parsed:
                disagreements += 1
            groups[key] = exact_add(groups[key], parsed)
            classes[row['ACCOUNT'].strip()[:1]][0] += 1
            classes[row['ACCOUNT'].strip()[:1]][1] = exact_add(classes[row['ACCOUNT'].strip()[:1]][1], parsed)
            period = f'{key[2]}-{key[3]}'
            period_net[period] = exact_add(period_net[period], parsed)
    actual = {k: v for k, v in groups.items() if k[1] == 'ACTUALS'}
    unbalanced = sorted(((k, v) for k, v in actual.items() if v != 0), key=lambda item: -abs(item[1]))
    source_actual = {k: v for k, v in source_groups.items() if k[1] == 'ACTUALS'}
    source_complete = not any(unavailable_groups[k] for k in source_actual)
    source_total = accepted_total = Decimal('0')
    for value in source_actual.values():
        source_total = exact_add(source_total, value)
    for value in actual.values():
        accepted_total = exact_add(accepted_total, value)
    return {'file_sha256': digest.hexdigest(), 'file_bytes': path.stat().st_size,
            'encoding': encoding, 'utf8_decode_failed_at_byte': utf8_failure, 'rows': rows,
            'money_rejected': rejected, 'money_accepted': rows - rejected, 'rejected_examples': examples,
            'independent_parse_disagreements': disagreements, 'exact_duplicate_rows': duplicates,
            'ledgers': dict(ledgers), 'fiscal_periods': sorted(f'{y}-{p}' for y, p in periods),
            'actuals_groups': len(actual), 'actuals_balanced_groups': len(actual) - len(unbalanced),
            'actuals_unbalanced_groups': len(unbalanced),
            'largest_imbalances': [{'business_unit': k[0], 'fiscal_year': k[2], 'period': k[3], 'fund': k[4],
                                    'net': str(v)} for k, v in unbalanced[:10]],
            'actuals_total_net': str(accepted_total),
            'account_classes': {k: {'rows': v[0], 'net': str(v[1])} for k, v in sorted(classes.items())},
            'net_by_period': {k: str(v) for k, v in sorted(period_net.items())},
            'trial_balance_testable': False,
            'balance_assessment': {'status': 'insufficient_evidence',
                                   'reason': 'No independent attestation of complete double-entry coverage for these groups; '
                                             'account classes and zero nets do not prove completeness or correctness.'},
            'field_scopes': {'ledgers_fiscal_periods_duplicates': 'all source rows, including money rejections',
                             'account_classes_net_by_period_legacy_actuals': 'money()-accepted subset only',
                             'legacy_balance_names': 'balanced/unbalanced and largest_imbalances are deprecated aliases '
                                                     'for zero/nonzero arithmetic nets, not accounting verdicts',
                             'periods': 'source fiscal year and accounting period, not calendar dates',
                             'source_amounts': 'finite Decimal, <=256 digits, exponent magnitude <=256; no rounding'},
            'source_amounts_unavailable': sum(unavailable_groups.values()),
            'source_actuals': {
                'groups': len(source_actual), 'arithmetic_complete': source_complete,
                'total_net': str(source_total) if source_complete else None,
                'zero_net_groups': sum(v == 0 for k, v in source_actual.items() if not unavailable_groups[k]),
                'nonzero_net_groups': sum(v != 0 for k, v in source_actual.items() if not unavailable_groups[k]),
                'unavailable_net_groups': sum(bool(unavailable_groups[k]) for k in source_actual),
                'groups_with_money_rejections': sum(bool(rejected_groups[k]) for k in source_actual),
                'affected_groups': [dict(zip(KEYS, k, strict=True), money_rejected=rejected_groups[k],
                                         source_amounts_unavailable=unavailable_groups[k],
                                         source_net=None if unavailable_groups[k] else str(source_actual[k]),
                                         accepted_subset_net=str(groups[k]))
                                    for k in sorted(source_actual) if rejected_groups[k] or unavailable_groups[k]]}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    if options.output.exists():
        parser.error('Refusing to overwrite an existing report')
    result = {'schema_version': VERSION, 'source': {'publisher': 'State of Oklahoma, Office of Management and '
                                                                 'Enterprise Services', 'catalog': 'https://catalog.data.gov/dataset/general-ledger',
                                                    'license': 'CC-BY (opendefinition.org/licenses/cc-by)', 'url': options.source_url},
              'scope': 'Real public-sector posted totals (USD fund accounting); not an enterprise ledger, not CAS/SCAS.',
              **check(options.input)}
    with options.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('largest_imbalances', 'rejected_examples')},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
