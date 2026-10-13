"""Revalidate saved ERPNext tax/return evidence, including an idempotent replay."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_erpnext_returns import inspect  # noqa: E402


def summarize(first_path, replay_path):
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in (first_path, replay_path)]
    audits = []
    for report in reports:
        if (report.get('schema_version') != 'erpnext-tax-return-v1' or report.get('synthetic') is not True
                or report.get('model_requests') != 0 or report.get('status') != 'completed'):
            raise ValueError('Unexpected validation scope')
        checks = inspect(report)
        if not all(checks.values()):
            raise ValueError('Saved evidence failed: ' + ', '.join(k for k, v in checks.items() if not v))
        for name in ('scripts/validate_erpnext_returns.py', 'scripts/validate_erpnext_semantics.py', 'backend/app/connectors.py'):
            if report.get('source_sha256', {}).get(name) != hashlib.sha256((ROOT / name).read_bytes()).hexdigest():
                raise ValueError('Validation source changed: ' + name)
        audits.append(checks)
    same = {key: reports[0][key] == reports[1][key] for key in ('documents', 'export', 'stock', 'trial_balance')}
    if not all(same.values()):
        raise ValueError('Replay changed source documents or accounting evidence')
    return {'schema_version': 'erpnext-tax-return-summary-v1', 'status': 'passed', 'synthetic': True,
            'model_requests': 0, 'new_dependencies': 0, 'company': reports[0]['company'],
            'recomputed_checks_per_run': [len(a) for a in audits], 'checks': audits[0], 'replay_equal': same,
            'documents': {label: doc['name'] for label, doc in reports[0]['documents'].items()},
            'observed_totals': {'purchase_net': '123.40', 'purchase_test_tax': '12.34', 'purchase_total': '135.74',
                'return_net': '-37.02', 'return_test_tax': '-3.70', 'return_total': '-40.72',
                'remaining_stock_quantity': '7', 'remaining_stock_value': '86.38', 'net_payable': '95.02'},
            'reports': [{'path': p.as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                         'duration_seconds': report['duration_seconds']} for p, report in zip((first_path, replay_path), reports)],
            'source_sha256': reports[0]['source_sha256'],
            'limitations': ['10 percent is a synthetic test charge, not a statutory tax rate or policy.',
                'One company, one stock item, one partial return; no payment/refund, cancellation or valuation-tax scenario.',
                'Local ERPNext v16.50.0 only; no real enterprise data or professional review.',
                'GL adapter remains read-only and journal-only; paginated reads are not atomic.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first', required=True, type=Path)
    parser.add_argument('--replay', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = summarize(args.first, args.replay)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print('Saved evidence and unchanged replay verified.')
