"""Publish metadata-only coverage from a current, immutable local probe."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.statement_store import digest, project, read_bounded, read_json  # noqa: E402

# These IDs have independently transcribed cells and original-page regression
# cases in test_annual_statement_probe.py, not complete industry validation.
PINNED_SAMPLES = {'annual-002024-2025', 'annual-600588-2025', 'annual-688111-2025', 'annual-601933-2023'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--coverage', type=Path, default=ROOT / 'docs/annual-report-coverage.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/annual-statement-component-coverage.json')
    args = parser.parse_args()
    content, coverage = read_bounded(args.probe), read_bounded(args.coverage)
    report = read_json(content)
    project(content, coverage, observed_at=datetime.now(timezone.utc))
    if report['parser_source_sha256'] != digest((ROOT / 'benchmarks/annual_statement_probe.py').read_bytes()):
        raise ValueError('Parser changed; historical run must not be labeled current')
    rows = report['reports']
    pinned = PINNED_SAMPLES & {r['id'] for r in rows if r['status'] == 'observed'}
    summary = {'schema_version': 'annual-statement-component-coverage-v1', 'created_at': report['created_at'],
               'source_report_sha256': digest(content), 'parser_source_sha256': report['parser_source_sha256'],
               'coverage_manifest_sha256': report['coverage_manifest_sha256'],
               'reports_attempted': report['reports_attempted'], 'reports_observed': report['reports_observed'],
               'reports_rejected': report['reports_rejected'], 'unprobed_reports': report['unprobed_reports'],
               'independently_pinned_report_samples': len(pinned), 'model_calls': 0,
               'runtime_agent_used': False, 'financial_accuracy_benchmark': False,
               'error_counts': dict(Counter(r['error_code'] for r in rows if r['status'] == 'rejected')),
               'reports': [{'id': r['id'], 'company_code': r['company_code'], 'report_year': r['report_year'],
                           'url': r['url'], 'raw_sha256': r['raw_sha256'], 'status': r['status'],
                           'error_code': r.get('error_code'), 'observed_metric_count': len(r['metrics']),
                           'has_independently_pinned_sample': r['id'] in pinned} for r in rows],
               'limitations': ['Observed means located extraction, not verified financial truth.',
                               'Only four pinned samples have independent cell regressions.',
                               'The read-only workspace is separate from books, executable rules and Agent tools.']}
    if args.output.exists():
        previous = read_json(read_bounded(args.output))
        summary['previous_runs'] = previous.get('previous_runs', [])
        if previous['source_report_sha256'] != summary['source_report_sha256']:
            summary['previous_runs'].append({key: previous[key] for key in (
                'created_at', 'source_report_sha256', 'parser_source_sha256', 'reports_attempted',
                'reports_observed', 'reports_rejected', 'error_counts')})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ('reports_attempted', 'reports_observed', 'reports_rejected',
                                                   'independently_pinned_report_samples')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
