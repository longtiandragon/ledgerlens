"""Run the BPI2019 process rule package on stored snapshots; no model calls.

Counts are observations about the local subset, not accuracy or anomaly rates.
Independent cross-checks recount a few quantities straight from source.jsonl
with plain JSON logic that does not import the rule package.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.process_rules import summarize_snapshot  # noqa: E402
from app.process_store import list_process_snapshots, load_process_snapshot  # noqa: E402

THREE_WAY = {'3-way match, invoice before GR', '3-way match, invoice after GR'}


def independent_counts(source: Path) -> dict:
    traces = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
    counts = {'three_way_without_clear_invoice': 0, 'gr_based_missing_goods_or_invoice_receipt': 0,
              'consignment_with_item_invoice_or_clearing': 0, 'traces_with_cancellation': 0}
    for trace in traces:
        category = trace['attributes'].get('Item Category')
        names = [event.get('concept:name') for event in trace['events']]
        if category in THREE_WAY and 'Clear Invoice' not in names:
            counts['three_way_without_clear_invoice'] += 1
        if category == '3-way match, invoice after GR' and (
                'Record Goods Receipt' not in names or 'Record Invoice Receipt' not in names):
            counts['gr_based_missing_goods_or_invoice_receipt'] += 1
        if category == 'Consignment' and any(name in names for name in (
                'Record Invoice Receipt', 'Record Subsequent Invoice', 'Clear Invoice')):
            counts['consignment_with_item_invoice_or_clearing'] += 1
        if any(isinstance(name, str) and name.startswith('Cancel ') for name in names):
            counts['traces_with_cancellation'] += 1
    return counts


def run(root: Path) -> dict:
    reports = []
    for manifest in list_process_snapshots(root):
        snapshot = load_process_snapshot(root, manifest['snapshot_id'])
        summary = summarize_snapshot(snapshot, dataset_scope_id=manifest['dataset_scope_id'],
                                     snapshot_id=manifest['snapshot_id'])
        expected = independent_counts(root / manifest['snapshot_id'] / 'source.jsonl')
        rules = summary['rule_status_counts']
        observed = {
            'three_way_without_clear_invoice': rules['BPI2019_PROCESS.CLEARING_AFTER_GOODS_RECEIPT']['insufficient'],
            'gr_based_missing_goods_or_invoice_receipt': rules['BPI2019_PROCESS.GR_BASED_INVOICE_ORDER']['insufficient'],
            'consignment_with_item_invoice_or_clearing': rules['BPI2019_PROCESS.ITEM_INVOICE_EXPECTATION']['needs_review'],
            'traces_with_cancellation': rules['BPI2019_PROCESS.CANCELLATION_EVENTS']['insufficient'],
        }
        # Only valid when no timestamps tie or are unreadable for these stages;
        # a mismatch is reported, never silently reconciled.
        checks = [{'name': name, 'independent': expected[name], 'rule_package': observed[name],
                   'match': expected[name] == observed[name]} for name in expected]
        reports.append({'manifest': {key: manifest[key] for key in (
            'dataset_scope_id', 'snapshot_id', 'source_raw_sha256', 'source_subset_sha256', 'trace_count',
            'event_count')}, 'summary': summary, 'independent_cross_checks': checks})
    return {'schema_version': 'bpi2019-process-rules-summary-v1', 'generated_at': datetime.now(UTC).isoformat(),
            'model_requests': 0, 'snapshots': reports,
            'all_cross_checks_match': all(check['match'] for report in reports
                                          for check in report['independent_cross_checks']),
            'interpretation': 'Rule outcome counts on a local public subset; not accuracy, anomaly rate, '
                              'accounting-standard support or business completion.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT / '.local/bpi2019/process-events')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/bpi2019-process-rules-summary.json')
    args = parser.parse_args()
    report = run(args.root)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'all_cross_checks_match': report['all_cross_checks_match'],
                      'snapshots': len(report['snapshots'])}, ensure_ascii=False))
    if not report['all_cross_checks_match']:
        sys.exit(1)


if __name__ == '__main__':
    main()
