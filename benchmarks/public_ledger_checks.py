"""Real public-ledger pilot: balance and data-quality checks on an open government general ledger.

Source: State of Oklahoma OMES "General Ledger" on data.gov (CC-BY), quarterly CSV of posted
totals summed by chartfield combination. This is real accounting data, not a company's books:
US fund accounting in USD, so CAS/SCAS rules do not apply and the product's CNY import refuses
it by design. What it can test on real data is exact-cent parsing with the product's own
money() and double-entry balance: for the ACTUALS ledger, posted totals of every business unit,
fund and accounting period should net to zero. An independent Decimal parse checks money().
Streaming only; the file is never loaded into memory at once.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.domain import money  # noqa: E402

VERSION = 'public-ledger-checks-v1'
AMOUNT = 'SUM(L.POSTED_TOTAL_AMT)'
KEYS = ('BUSINESS_UNIT', 'LEDGER', 'FISCAL_YEAR', 'ACCOUNTING_PERIOD', 'FUND_CODE')


def independent(text: str) -> Decimal | None:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def encoding_of(path: Path) -> tuple[str, int | None]:
    """UTF-8 if the whole file decodes strictly; otherwise strict cp1252, with the first failing offset."""
    decoder, offset = __import__('codecs').getincrementaldecoder('utf-8')('strict'), 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            try:
                decoder.decode(block)
            except UnicodeDecodeError as exc:
                return 'cp1252', offset + exc.start
            offset += len(block)
    return 'utf-8-sig', None


def check(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    encoding, utf8_failure = encoding_of(path)
    groups: dict[tuple, Decimal] = defaultdict(lambda: Decimal('0'))
    rows = rejected = disagreements = 0
    ledgers, periods, seen, duplicates = defaultdict(int), set(), set(), 0
    classes: dict[str, list] = defaultdict(lambda: [0, Decimal('0')])   # first account digit -> rows, net
    period_net: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    examples = []
    with path.open(encoding=encoding, errors='strict', newline='') as stream:
        for row in csv.DictReader(stream):
            rows += 1
            raw = row[AMOUNT]
            reference = independent(raw)
            try:
                parsed = money(raw.strip())
            except ValueError:
                rejected += 1
                if len(examples) < 5:
                    examples.append({'row': rows, 'reason': 'money() rejected', 'independent_parse': str(reference)})
                continue
            if reference is None or reference != parsed:
                disagreements += 1
            key = tuple(row[k].strip() for k in KEYS)
            groups[key] += parsed
            classes[row['ACCOUNT'].strip()[:1]][0] += 1
            classes[row['ACCOUNT'].strip()[:1]][1] += parsed
            period_net[f'{key[2]}-{key[3]}'] += parsed
            ledgers[key[1]] += 1
            periods.add((key[2], key[3]))
            fingerprint = hashlib.sha256('\x1f'.join(row[k] for k in row).encode('utf-8')).digest()
            duplicates += fingerprint in seen
            seen.add(fingerprint)
    actual = {k: v for k, v in groups.items() if k[1] == 'ACTUALS'}
    unbalanced = sorted(((k, v) for k, v in actual.items() if v != 0), key=lambda item: -abs(item[1]))
    return {'file_sha256': digest.hexdigest(), 'file_bytes': path.stat().st_size,
            'encoding': encoding, 'utf8_decode_failed_at_byte': utf8_failure, 'rows': rows,
            'money_rejected': rejected, 'rejected_examples': examples,
            'independent_parse_disagreements': disagreements, 'exact_duplicate_rows': duplicates,
            'ledgers': dict(ledgers), 'fiscal_periods': sorted(f'{y}-{p}' for y, p in periods),
            'actuals_groups': len(actual), 'actuals_balanced_groups': len(actual) - len(unbalanced),
            'actuals_unbalanced_groups': len(unbalanced),
            'largest_imbalances': [{'business_unit': k[0], 'fiscal_year': k[2], 'period': k[3], 'fund': k[4],
                                    'net': str(v)} for k, v in unbalanced[:10]],
            'actuals_total_net': str(sum(actual.values(), Decimal('0'))),
            'account_classes': {k: {'rows': v[0], 'net': str(v[1])} for k, v in sorted(classes.items())},
            'net_by_period': {k: str(v) for k, v in sorted(period_net.items())},
            # Balance can only be judged if liability and fund-balance classes (2, 3) are present.
            'trial_balance_testable': {'2', '3'} <= set(classes)}


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
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('largest_imbalances', 'rejected_examples')},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
