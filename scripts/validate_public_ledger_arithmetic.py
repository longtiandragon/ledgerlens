"""Cross-check a v2 extract report with exact rational arithmetic, without importing its checker.

This verifies arithmetic and coverage counts only, never accounting correctness or anomaly labels.
"""
import argparse
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path


def validate(source: Path, report: Path) -> dict:
    observed = json.loads(report.read_text(encoding='utf-8'))
    with source.open('rb') as stream:
        source_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    amounts = defaultdict(Fraction)
    accepted = defaultdict(Fraction)
    rejected_groups = Counter()
    rows = rejected = 0
    # Independent source decoding choice for the pinned Oklahoma file; no production parser.
    with source.open(encoding='cp1252', newline='') as stream:
        for row in csv.DictReader(stream, strict=True):
            rows += 1
            key = tuple(row[k].strip() for k in ('BUSINESS_UNIT', 'LEDGER', 'FISCAL_YEAR',
                                                'ACCOUNTING_PERIOD', 'FUND_CODE'))
            amount = Fraction(row['SUM(L.POSTED_TOTAL_AMT)'].strip())
            amounts[key] += amount
            if (amount * 100).denominator != 1 or abs(amount) > Fraction('999999999999.99'):
                rejected += 1
                rejected_groups[key] += 1
            else:
                accepted[key] += amount
    actual = {k: v for k, v in amounts.items() if k[1] == 'ACTUALS'}
    accepted_actual = {k: v for k, v in accepted.items() if k[1] == 'ACTUALS'}
    source_report = observed['source_actuals']
    detail_rows = source_report['affected_groups']
    detail_keys = [tuple(entry[k] for k in ('BUSINESS_UNIT', 'LEDGER', 'FISCAL_YEAR',
                                          'ACCOUNTING_PERIOD', 'FUND_CODE')) for entry in detail_rows]
    expected_keys = {k for k in actual if rejected_groups[k]}
    details_match = len(detail_keys) == len(expected_keys) and set(detail_keys) == expected_keys
    for key, entry in zip(detail_keys, detail_rows, strict=True):
        details_match = details_match and (entry['money_rejected'] == rejected_groups[key]
                                          and Fraction(entry['source_net']) == actual[key]
                                          and Fraction(entry['accepted_subset_net']) == accepted[key])
    checks = {
        'schema': observed['schema_version'] == 'public-ledger-checks-v2',
        'source_sha256': observed['file_sha256'] == source_sha,
        'source_bytes': observed['file_bytes'] == source.stat().st_size,
        'all_rows': observed['rows'] == rows,
        'rejected_rows': observed['money_rejected'] == rejected,
        'accepted_rows': observed['money_accepted'] == rows - rejected,
        'source_groups': source_report['groups'] == len(actual),
        'source_arithmetic_complete': source_report['arithmetic_complete'] is True,
        'source_net': Fraction(source_report['total_net']) == sum(actual.values()),
        'accepted_net': Fraction(observed['actuals_total_net']) == sum(accepted_actual.values()),
        'zero_net_groups': source_report['zero_net_groups'] == sum(v == 0 for v in actual.values()),
        'nonzero_net_groups': source_report['nonzero_net_groups'] == sum(v != 0 for v in actual.values()),
        'affected_groups': source_report['groups_with_money_rejections'] == sum(k in rejected_groups for k in actual),
        'affected_group_details': details_match,
        'no_trial_balance_claim': observed['trial_balance_testable'] is False,
    }
    return {'schema_version': 'public-ledger-arithmetic-crosscheck-v1',
            'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
            'source_sha256': source_sha, 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
            'rows': rows, 'money_rejected': rejected,
            'rejected_actuals_net_fraction': str(sum(actual.values()) - sum(accepted_actual.values())),
            'limitations': 'Fraction cross-check of the pinned cp1252 extract only; no accounting or anomaly truth.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite an existing report')
    result = validate(args.input, args.report)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
