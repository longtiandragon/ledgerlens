"""Combine the frozen v3 sealed-test runs into the pre-registered comparison (evaluator only).

Reads the three sealed reports only after every prediction has been written, checks each
against its frozen plan and the dataset manifest, verifies every truth file hash, then
scores each method with the track's own scorer. Failures stay in the denominator.
Family-level bootstrap intervals resample scenario families with replacement and keep
all repeats of a drawn family; differences are paired on the same family draws.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT))

from benchmarks.root_cause_v2 import score  # noqa: E402

VERSION = 'root-cause-v3-final-v1'
PLANS = {'single': 'docs/root-cause-v3-sealed-plan-single.json',
         'active': 'docs/root-cause-v3-sealed-plan-active.json',
         'multi': 'docs/root-cause-v3-sealed-plan-multi.json'}
BASELINES = ('rule_only', 'rule_fixed_request', 'keyword_request')
MODELS = ('single', 'active', 'multi')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_report(name: str, report: dict, plan: dict, manifest_sha: str) -> list[str]:
    problems = []
    for key in ('selected_case_ids', 'modes', 'repeat', 'prompt_version', 'runner_version', 'split'):
        if report.get(key) != plan.get(key):
            problems.append(f'{name}: {key} differs from frozen plan')
    if report.get('source_sha256') != plan.get('source_sha256'):
        problems.append(f'{name}: runner sources differ from frozen plan')
    if report.get('dataset_manifest_sha256') != manifest_sha or plan.get('dataset_manifest_sha256') != manifest_sha:
        problems.append(f'{name}: dataset manifest differs')
    if report.get('status') not in ('completed', 'completed_with_failures'):
        problems.append(f'{name}: run did not complete ({report.get("status")})')
    return problems


def macro_f1(rows: list[dict], categories: dict) -> float | None:
    return score(rows, categories)['category_macro_f1'] if rows else None


def bootstrap(by_method: dict[str, list[dict]], categories: dict, pairs: list[tuple[str, str]],
              iterations: int = 2000, seed: int = 20261014) -> dict:
    families = sorted({row['family_id'] for rows in by_method.values() for row in rows})
    grouped = {m: defaultdict(list) for m in by_method}
    for method, rows in by_method.items():
        for row in rows:
            grouped[method][row['family_id']].append(row)
    rng = random.Random(seed)
    draws = {m: [] for m in by_method}
    differences = {f'{a}-{b}': [] for a, b in pairs}
    for _ in range(iterations):
        chosen = [rng.choice(families) for _ in families]
        values = {}
        for method in by_method:
            sample = [row for family in chosen for row in grouped[method][family]]
            values[method] = macro_f1(sample, categories) or 0.0
            draws[method].append(values[method])
        for a, b in pairs:
            differences[f'{a}-{b}'].append(values[a] - values[b])

    def interval(values):
        ordered = sorted(values)
        return {'lower': round(ordered[int(.025 * iterations)], 4),
                'upper': round(ordered[int(.975 * iterations) - 1], 4)}
    return {'families': len(families), 'iterations': iterations, 'seed': seed,
            'macro_f1': {m: interval(v) for m, v in draws.items()},
            'macro_f1_difference': {k: interval(v) for k, v in differences.items()}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / '.local/datasets-v3')
    parser.add_argument('--single', type=Path, required=True)
    parser.add_argument('--active', type=Path, required=True)
    parser.add_argument('--multi', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    if options.output.exists():
        parser.error('Refusing to overwrite an existing analysis')
    manifest_path = options.dataset / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest_sha = sha(manifest_path)
    entries = {case['case_id']: case for case in manifest['cases']}
    categories = manifest['categories']
    reports, problems = {}, []
    for name in MODELS:
        path = getattr(options, name)
        report = json.loads(path.read_text(encoding='utf-8'))
        plan = json.loads((ROOT / PLANS[name]).read_text(encoding='utf-8'))
        problems += check_report(name, report, plan, manifest_sha)
        reports[name] = (path, report)
    if problems:
        raise SystemExit('Sealed runs do not match their frozen plans: ' + '; '.join(problems))
    truth = {}
    for case_id in reports['single'][1]['selected_case_ids']:
        raw = (options.dataset / 'truth' / f'{case_id}.json').read_bytes()
        expected = entries[case_id].get('file_sha256', {}).get('truth')
        if expected is None or hashlib.sha256(raw).hexdigest() != expected:
            raise SystemExit('Frozen truth hash mismatch: ' + case_id)
        truth[case_id] = json.loads(raw)
    by_method: dict[str, list[dict]] = defaultdict(list)
    usage: dict[str, dict] = defaultdict(lambda: {'samples': 0, 'paid_requests': 0, 'tokens': 0})
    for name, (_, report) in reports.items():
        for row in report['cases']:
            method = row['method']
            if name != 'single' and method in BASELINES:
                continue    # deterministic baselines are taken once, from the single-agent run
            by_method[method].append({**row, 'truth': truth[row['case_id']]})
            usage[method]['samples'] += 1
            usage[method]['paid_requests'] += row.get('paid_requests') or 0
            usage[method]['tokens'] += row.get('tokens') or 0
    metrics = {method: score(rows, categories) for method, rows in by_method.items()}
    strongest = max(BASELINES, key=lambda m: (metrics[m]['category_macro_f1'] or 0, metrics[m]['conclusion_accuracy'] or 0))
    pairs = [(model, strongest) for model in MODELS] + [('multi', 'single'), ('active', 'single')]
    intervals = bootstrap(dict(by_method), categories, pairs)
    targets = {}
    for model in MODELS:
        m, base = metrics[model], metrics[strongest]
        needing = [r for r in by_method[model] if r['truth']['decisive_kinds']]
        targets[model] = {
            'macro_f1_at_least_0_85': (m['category_macro_f1'] or 0) >= 0.85,
            'gain_over_strongest_at_least_0_05': (m['category_macro_f1'] or 0) - (base['category_macro_f1'] or 0) >= 0.05,
            'decisive_evidence_rate': (round(int(m['decisive_evidence_obtained'].split('/')[0]) / len(needing), 4)
                                       if needing else None)}
    result = {
        'schema_version': VERSION, 'dataset_version': manifest['version'], 'dataset_manifest_sha256': manifest_sha,
        'reports': {name: {'path': str(path).replace('\\', '/'), 'sha256': sha(path), 'status': report['status'],
                           'repeat': report['repeat'], 'paid_requests': report['paid_requests'],
                           'cost_budget': report.get('cost_budget')}
                    for name, (path, report) in reports.items()},
        'strongest_same_budget_baseline': strongest,
        'metrics': {m: {k: v for k, v in metrics[m].items() if k != 'family_bootstrap_conclusion'}
                    | {'conclusion_bootstrap': metrics[m]['family_bootstrap_conclusion']} for m in metrics},
        'usage': {m: {**u, 'paid_requests_per_sample': round(u['paid_requests'] / u['samples'], 2),
                      'tokens_per_sample': round(u['tokens'] / u['samples'])} for m, u in usage.items()},
        'bootstrap': intervals, 'preregistered_targets': targets,
        'truth_read_after_all_predictions': True, 'truth_files_verified': len(truth),
    }
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({m: {k: metrics[m][k] for k in ('samples', 'failures', 'category_macro_f1', 'conclusion_accuracy',
                                                     'decisive_evidence_obtained')} for m in metrics},
                     ensure_ascii=False, indent=1))
    print('strongest baseline:', strongest)
    print(json.dumps(intervals['macro_f1_difference'], indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
