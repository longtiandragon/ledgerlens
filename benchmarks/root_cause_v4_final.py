"""Combine the frozen v4 sealed runs into the pre-registered hybrid comparison (evaluator only).

Two sealed runs, each checked against its frozen plan and the dataset manifest: the hybrid run
(which also carries the three deterministic baselines) and, unless the plan dropped it for budget,
a single-agent run. Truth files are read and hash-checked only after every prediction exists.
Failures stay in the denominator. Family bootstrap as in v3, with a new seed.

Pre-registered targets (docs/ROOT_CAUSE_V4.md):
  T1 hybrid macro-F1 minus the strongest same-budget deterministic baseline >= 0.05
  T2 the family-bootstrap 95% interval of that difference lies above 0
  T3 hybrid macro-F1 >= single-agent macro-F1
  T4 at most 1 structural failure among the hybrid samples sent to the model
  T5 hybrid paid requests per case <= 60% of the single agent's
Also reported: macro-F1 >= 0.85 and the decisive-evidence rate (GOALS targets).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT))

from benchmarks.root_cause_v2 import score  # noqa: E402
from benchmarks.root_cause_v3_final import bootstrap, check_report, sha  # noqa: E402

VERSION = 'root-cause-v4-final-v1'
PLANS = {'hybrid': 'docs/root-cause-v4-sealed-plan-hybrid.json',
         'single': 'docs/root-cause-v4-sealed-plan-single.json'}
BASELINES = ('rule_only', 'rule_fixed_request', 'keyword_request')
SEED = 20261024


def _rate(fraction: str, rows: list[dict]) -> float | None:
    needing = [r for r in rows if r['truth']['decisive_kinds']]
    return round(int(fraction.split('/')[0]) / len(needing), 4) if needing else None


def analyse(reports: dict[str, dict], truth: dict[str, dict], categories: dict) -> dict:
    by_method: dict[str, list[dict]] = defaultdict(list)
    usage: dict[str, dict] = defaultdict(lambda: {'samples': 0, 'paid_requests': 0, 'tokens': 0})
    for name, report in reports.items():
        for row in report['cases']:
            method = row['method']
            if name != 'hybrid' and method in BASELINES:
                continue  # baselines are taken once, from the hybrid run
            by_method[method].append({**row, 'truth': truth[row['case_id']]})
            usage[method]['samples'] += 1
            usage[method]['paid_requests'] += row.get('paid_requests') or 0
            usage[method]['tokens'] += row.get('tokens') or 0
    metrics = {method: score(rows, categories) for method, rows in by_method.items()}
    strongest = max(BASELINES, key=lambda m: (metrics[m]['category_macro_f1'] or 0,
                                              metrics[m]['conclusion_accuracy'] or 0))
    has_single = 'single' in by_method
    pairs = [('hybrid', strongest)] + ([('single', strongest), ('hybrid', 'single')] if has_single else [])
    intervals = bootstrap(dict(by_method), categories, pairs, seed=SEED)
    f1 = {m: metrics[m]['category_macro_f1'] or 0 for m in metrics}
    per_case = {m: u['paid_requests'] / u['samples'] for m, u in usage.items() if u['samples']}
    routed = [r for r in by_method['hybrid'] if r.get('route') != 'deterministic']
    routes = defaultdict(list)
    for row in by_method['hybrid']:
        routes[row.get('route')].append(row)
    targets = {
        'T1_gain_over_strongest_at_least_0_05': f1['hybrid'] - f1[strongest] >= 0.05,
        'T2_gain_interval_above_zero': intervals['macro_f1_difference'][f'hybrid-{strongest}']['lower'] > 0,
        'T3_hybrid_at_least_single': (f1['hybrid'] >= f1['single']) if has_single else None,
        'T4_routed_structural_failures_at_most_1': sum(bool(r['prediction'].get('error')) for r in routed) <= 1,
        'T5_requests_per_case_at_most_60pct_of_single': (per_case['hybrid'] <= 0.6 * per_case['single']
                                                         if has_single and per_case.get('single') else None),
        'goal_macro_f1_at_least_0_85': f1['hybrid'] >= 0.85,
        'goal_decisive_evidence_rate': _rate(metrics['hybrid']['decisive_evidence_obtained'], by_method['hybrid']),
    }
    return {
        'strongest_same_budget_baseline': strongest,
        'metrics': {m: {k: v for k, v in metrics[m].items() if k != 'family_bootstrap_conclusion'}
                    | {'conclusion_bootstrap': metrics[m]['family_bootstrap_conclusion']} for m in metrics},
        'hybrid_by_route': {route: {'samples': len(rows), 'failures': sum(bool(r['prediction'].get('error')) for r in rows),
                                    'paid_requests': sum(r.get('paid_requests') or 0 for r in rows),
                                    'per_category_f1': score(rows, categories)['per_category_f1']}
                            for route, rows in sorted(routes.items(), key=lambda kv: str(kv[0]))},
        'usage': {m: {**u, 'paid_requests_per_sample': round(u['paid_requests'] / u['samples'], 2),
                      'tokens_per_sample': round(u['tokens'] / u['samples'])} for m, u in usage.items()},
        'bootstrap': intervals, 'preregistered_targets': targets,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', type=Path, default=ROOT / '.local/datasets-v4')
    parser.add_argument('--hybrid', type=Path, required=True)
    parser.add_argument('--single', type=Path, help='omit only when the frozen plan dropped the single-agent run')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    if options.output.exists():
        parser.error('Refusing to overwrite an existing analysis')
    manifest_path = options.dataset / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('version') != 'root-cause-v4.0.0':
        parser.error('The v4 analysis needs the v4 dataset')
    manifest_sha = sha(manifest_path)
    entries = {case['case_id']: case for case in manifest['cases']}
    reports, paths, problems = {}, {}, []
    for name in ('hybrid', 'single'):
        path = getattr(options, name)
        if path is None:
            continue
        report = json.loads(path.read_text(encoding='utf-8'))
        plan = json.loads((ROOT / PLANS[name]).read_text(encoding='utf-8'))
        problems += check_report(name, report, plan, manifest_sha)
        reports[name], paths[name] = report, path
    if problems:
        raise SystemExit('Sealed runs do not match their frozen plans: ' + '; '.join(problems))
    truth = {}
    for case_id in reports['hybrid']['selected_case_ids']:
        raw = (options.dataset / 'truth' / f'{case_id}.json').read_bytes()
        expected = entries[case_id].get('file_sha256', {}).get('truth')
        if expected is None or hashlib.sha256(raw).hexdigest() != expected:
            raise SystemExit('Frozen truth hash mismatch: ' + case_id)
        truth[case_id] = json.loads(raw)
    result = {'schema_version': VERSION, 'dataset_version': manifest['version'],
              'dataset_manifest_sha256': manifest_sha,
              'reports': {name: {'path': str(paths[name]).replace('\\', '/'), 'sha256': sha(paths[name]),
                                 'status': report['status'], 'repeat': report['repeat'],
                                 'paid_requests': report['paid_requests'], 'cost_budget': report.get('cost_budget')}
                          for name, report in reports.items()},
              **analyse(reports, truth, manifest['categories']),
              'truth_read_after_all_predictions': True, 'truth_files_verified': len(truth)}
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({m: {k: v for k, v in result['metrics'][m].items()
                          if k in ('samples', 'failures', 'category_macro_f1', 'conclusion_accuracy')}
                      for m in result['metrics']}, ensure_ascii=False, indent=1))
    print('strongest baseline:', result['strongest_same_budget_baseline'])
    print(json.dumps(result['preregistered_targets'], indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
