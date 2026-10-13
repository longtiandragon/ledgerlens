"""v3 sealed results after the active and multi runs were interrupted (documented deviation).

The pre-registered combined analysis (root_cause_v3_final) needs three completed runs and
refuses interrupted reports, as designed; it is left unchanged. This script applies the same
scorer, strongest-baseline rule, family bootstrap and targets to the one complete run (single
agent x3 with the three deterministic baselines). The interrupted active and multi runs are only
described: every completed sample is scored, plus a same-case view on the cases that all methods
completed in their first repeat. Each report must still match its frozen plan except for the
completion status. Truth is read only after every run has ended; failures stay in the denominator.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT))

from benchmarks.costs import estimate_batch, load_prices  # noqa: E402
from benchmarks.root_cause_v2 import score  # noqa: E402
from benchmarks.root_cause_v3_final import BASELINES, MODELS, PLANS, bootstrap, check_report, sha  # noqa: E402

VERSION = 'root-cause-v3-partial-v1'
# Same formula as scripts/guard_paid_run.py: off-peak list minimum, USD at 7.3, unknown attempts at $0.003.
GUARD_RATE, GUARD_UNKNOWN_USD = Decimal('7.3'), Decimal('0.003')
INTERRUPTED = 'run did not complete (running)'


def guard_estimate(report: dict, prices: dict) -> str:
    groups = estimate_batch(report, prices)['groups'].values()
    usd = sum(Decimal(g['known_usage_minimum_usd']) for g in groups)
    return str(round((usd + sum(g['unknown_attempts'] for g in groups) * GUARD_UNKNOWN_USD) * GUARD_RATE, 3))


def metrics(rows: list[dict], categories: dict) -> dict:
    result = score(rows, categories)
    return {k: v for k, v in result.items() if k != 'family_bootstrap_conclusion'} | {
        'conclusion_bootstrap': result['family_bootstrap_conclusion'],
        'paid_requests_per_sample': round(sum(r.get('paid_requests') or 0 for r in rows) / len(rows), 2),
        'tokens_per_sample': round(sum(r.get('tokens') or 0 for r in rows) / len(rows))}


def targets(model: dict, base: dict, needing: int) -> dict:
    return {'macro_f1_at_least_0_85': (model['category_macro_f1'] or 0) >= 0.85,
            'gain_over_strongest_at_least_0_05':
                (model['category_macro_f1'] or 0) - (base['category_macro_f1'] or 0) >= 0.05,
            'decisive_evidence_rate': (round(int(model['decisive_evidence_obtained'].split('/')[0]) / needing, 4)
                                       if needing else None)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / '.local/datasets-v3')
    parser.add_argument('--single', type=Path, required=True)
    parser.add_argument('--active', type=Path, required=True)
    parser.add_argument('--multi', type=Path, required=True)
    parser.add_argument('--interruption', required=True, help='what ended the incomplete runs, recorded verbatim')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    if options.output.exists():
        parser.error('Refusing to overwrite an existing analysis')
    manifest_path = options.dataset / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest_sha = sha(manifest_path)
    entries = {case['case_id']: case for case in manifest['cases']}
    categories = manifest['categories']
    prices = load_prices(ROOT / 'benchmarks/deepseek-prices-2026-10-08.json')
    reports, problems = {}, []
    for name in MODELS:
        path = getattr(options, name)
        report = json.loads(path.read_text(encoding='utf-8'))
        plan = json.loads((ROOT / PLANS[name]).read_text(encoding='utf-8'))
        found = [p for p in check_report(name, report, plan, manifest_sha) if not (
            name != 'single' and p == f'{name}: {INTERRUPTED}')]
        problems += found
        reports[name] = (path, report, plan)
    if problems:
        raise SystemExit('Reports do not match their frozen plans: ' + '; '.join(problems))
    truth = {}
    for case_id in reports['single'][1]['selected_case_ids']:
        raw = (options.dataset / 'truth' / f'{case_id}.json').read_bytes()
        expected = entries[case_id].get('file_sha256', {}).get('truth')
        if expected is None or hashlib.sha256(raw).hexdigest() != expected:
            raise SystemExit('Frozen truth hash mismatch: ' + case_id)
        truth[case_id] = json.loads(raw)
    by_method: dict[str, list[dict]] = defaultdict(list)
    for name, (_, report, _) in reports.items():
        for row in report['cases']:   # completed samples only; an in-flight sample has no prediction
            if name != 'single' and row['method'] in BASELINES:
                continue
            by_method[row['method']].append({**row, 'truth': truth[row['case_id']]})

    complete = {m: by_method[m] for m in (*BASELINES, 'single')}
    full = {m: metrics(rows, categories) for m, rows in complete.items()}
    strongest = max(BASELINES, key=lambda m: (full[m]['category_macro_f1'] or 0, full[m]['conclusion_accuracy'] or 0))
    needing = sum(bool(r['truth']['decisive_kinds']) for r in complete['single'])
    primary = {'methods': full, 'strongest_same_budget_baseline': strongest,
               'bootstrap': bootstrap(complete, categories, [('single', strongest)]),
               'preregistered_targets': {'single': targets(full['single'], full[strongest], needing)}}

    first = {m: {r['case_id']: r for r in rows if r['repeat'] == 1} for m, rows in by_method.items()}
    common = sorted(set.intersection(*(set(first[m]) for m in first)))
    same_cases = {m: [first[m][c] for c in common] for m in first}
    described = {'all_completed_samples': {m: metrics(by_method[m], categories) for m in ('active', 'multi')},
                 'same_cases_first_repeat': {
                     'case_ids': common, 'families': len({first['single'][c]['family_id'] for c in common}),
                     'methods': {m: metrics(rows, categories) for m, rows in same_cases.items()},
                     'bootstrap': bootstrap(same_cases, categories,
                                            [('active', strongest), ('multi', strongest),
                                             ('active', 'single'), ('multi', 'single')])}}
    runs = {}
    for name, (path, report, plan) in reports.items():
        planned = len(plan['selected_case_ids']) * plan['repeat'] * len(plan['modes'])
        runs[name] = {'path': str(path).replace('\\', '/'), 'sha256': sha(path), 'status': report['status'],
                      'repeat': report['repeat'], 'paid_requests': report['paid_requests'],
                      'planned_samples': planned, 'completed_samples': len(report['cases']),
                      'lost_in_flight_sample': bool(report.get('current_sample')) and report['status'] == 'running',
                      'guard_formula_offpeak_cny': guard_estimate(report, prices),
                      'cost_budget': report.get('cost_budget')}
    result = {'schema_version': VERSION, 'dataset_version': manifest['version'], 'dataset_manifest_sha256': manifest_sha,
              'deviation': ('Active and multi sealed runs ended before completion: ' + options.interruption +
                            '. The pre-registered combined analysis was not run; only the complete single-agent '
                            'run is analysed as planned, the interrupted runs are descriptive.'),
              'reports': runs, 'primary_single_agent': primary, 'interrupted_runs_descriptive': described,
              'truth_read_after_all_runs_ended': True, 'truth_files_verified': len(truth)}
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    table = {m: {k: v[k] for k in ('samples', 'failures', 'category_macro_f1', 'conclusion_accuracy',
                                   'decisive_evidence_obtained')} for m, v in full.items()}
    print(json.dumps({'primary': table, 'strongest': strongest,
                      'single_minus_strongest': primary['bootstrap']['macro_f1_difference'],
                      'same_cases': {m: {k: v[k] for k in ('samples', 'failures', 'category_macro_f1', 'conclusion_accuracy')}
                                     for m, v in described['same_cases_first_repeat']['methods'].items()},
                      'common_cases': len(common)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
