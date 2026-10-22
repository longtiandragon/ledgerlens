"""Strict v5 preregistration and result audit; evaluator only, no provider access."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random

from benchmarks.root_cause_v2 import load_case, release, score

BASELINES = ('rule_only', 'rule_fixed_request', 'keyword_request')
VERSION = 'root-cause-v5-protocol-v1'
SHARED = ('dataset_version', 'dataset_manifest_sha256', 'source_sha256', 'path_source_sha256',
          'prompt_version', 'runner_version', 'model', 'per_family', 'analysis')


def plan_identity(plan):
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate_rows(report: dict, plan: dict) -> None:
    """Do not trust a report's success marker or its cached metric values."""
    keys = ('dataset_version', 'dataset_manifest_sha256', 'source_sha256', 'selected_case_ids',
            'modes', 'repeat', 'split', 'prompt_version', 'runner_version')
    if any(report.get(key) != plan.get(key) for key in keys):
        raise ValueError('Report does not match the frozen plan')
    if (report.get('frozen_plan_sha256') != plan_identity(plan)
            or report.get('path_source_sha256') != plan.get('path_source_sha256')
            or report.get('model') != plan.get('model')):
        raise ValueError('Report does not identify its frozen execution configuration')
    if report.get('status') not in ('completed', 'completed_with_failures'):
        raise ValueError('Incomplete run cannot be scored as complete')
    expected = Counter((case_id, method, repeat) for case_id in plan['selected_case_ids']
                       for method in plan['modes'] for repeat in range(1, plan['repeat'] + 1))
    actual = Counter((row.get('case_id'), row.get('method'), row.get('repeat')) for row in report.get('cases', []))
    if actual != expected:
        raise ValueError('Missing, duplicate or foreign prediction rows')
    paid = report.get('paid_requests')
    if type(paid) is not int or not 0 <= paid <= plan['max_paid_requests']:
        raise ValueError('Invalid paid request count')
    counts = [row.get('paid_requests') for row in report['cases']]
    if any(type(n) is not int or n < 0 for n in counts) or sum(counts) != paid:
        raise ValueError('Request counts do not reconcile')
    if any('truth' in row for row in report['cases']):
        raise ValueError('Truth must not enter persisted prediction rows')
    if any(not isinstance(row.get('prediction'), dict) or not isinstance(row.get('released'), list)
           for row in report['cases']):
        raise ValueError('Malformed prediction rows')
    if any(bool(row.get('error')) != bool(row['prediction'].get('error')) for row in report['cases']):
        raise ValueError('Contradictory failure markers')
    if report['status'] == 'completed' and any(row.get('error') for row in report['cases']):
        raise ValueError('Success status hides failed predictions')


def check_smoke(root: Path, requirement: dict, sealed_plan: dict | None = None) -> None:
    plan_path, report_path = (root / requirement[key] for key in ('plan', 'report'))
    if not plan_path.resolve().is_relative_to(root) or not report_path.resolve().is_relative_to(root):
        raise ValueError('Smoke paths must stay inside the repository')
    if hashlib.sha256(plan_path.read_bytes()).hexdigest() != requirement['plan_sha256']:
        raise ValueError('Smoke plan hash mismatch')
    plan, report = read_json(plan_path), read_json(report_path)
    validate_rows(report, plan)
    if sealed_plan is not None and any(plan.get(key) != sealed_plan.get(key) for key in SHARED):
        raise ValueError('Smoke and sealed execution configuration differ')
    if (plan['split'] != 'development' or report['status'] != 'completed'
            or any(row['prediction'].get('error') or row.get('error') for row in report['cases'])):
        raise ValueError('Development smoke failed; no sealed dispatch permitted')


def _macro(rows, categories):
    """F1 only, avoiding nested bootstrap inside every bootstrap draw."""
    values = []
    for name in categories:
        tp = fp = fn = support = 0
        for row in rows:
            want = name in row['truth']['categories']
            got = not row['prediction'].get('error') and name in (row['prediction'].get('categories') or [])
            support += want
            tp += want and got
            fp += got and not want
            fn += want and not got
        if support:
            values.append(2 * tp / (2 * tp + fp + fn))
    return sum(values) / len(values) if values else 0.0


def paired_interval(left, right, categories, iterations=2000):
    families = sorted({row['family_id'] for row in left})
    if set(families) != {row['family_id'] for row in right}:
        raise ValueError('Unpaired scenario families')
    grouped = [{f: [r for r in rows if r['family_id'] == f] for f in families} for rows in (left, right)]
    rng, values = random.Random(20261022), []
    for _ in range(iterations):
        chosen = [rng.choice(families) for _ in families]
        values.append(_macro([r for f in chosen for r in grouped[0][f]], categories)
                      - _macro([r for f in chosen for r in grouped[1][f]], categories))
    values.sort()
    return {'families': len(families), 'draws': iterations, 'seed': 20261022,
            'lower': round(values[int(.025 * iterations)], 4),
            'upper': round(values[int(.975 * iterations) - 1], 4)}


def analyse(dataset: Path, reports: dict[str, dict], plans: dict[str, dict]) -> dict:
    raw = (dataset / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest.get('version') != 'root-cause-v5.0.0' or set(reports) != {'selective', 'comparator'}:
        raise ValueError('Both v5 sealed runs are required')
    digest = hashlib.sha256(raw).hexdigest()
    for name, report in reports.items():
        validate_rows(report, plans[name])
        if (plans[name]['dataset_manifest_sha256'] != digest or plans[name]['split'] != 'sealed_test'
                or plans[name]['per_family'] != 4 or plans[name]['repeat'] != 2):
            raise ValueError('Wrong sealed dataset or sampling design')
        if report.get('scoring_deferred') is not True or 'metrics' in report:
            raise ValueError('Sealed batches must defer truth scoring until paired analysis')
    if any(plans['selective'].get(key) != plans['comparator'].get(key) for key in SHARED):
        raise ValueError('Paired frozen execution configurations differ')
    if plans['selective']['selected_case_ids'] != plans['comparator']['selected_case_ids']:
        raise ValueError('Unpaired selected cases')
    entries = {c['case_id']: c for c in manifest['cases'] if c['split'] == 'sealed_test'}
    if set(entries) != set(plans['selective']['selected_case_ids']) or len(entries) != 36:
        raise ValueError('Every sealed case must be present')
    if (plans['selective']['modes'] != [*BASELINES, 'hybrid_selective']
            or plans['comparator']['modes'] != ['hybrid']):
        raise ValueError('Wrong comparison methods')
    for report in reports.values():
        for row in report['cases']:
            case_id = row['case_id']
            observed, withheld = load_case(dataset, case_id)
            for folder in ('observed', 'on_demand'):
                if hashlib.sha256((dataset / folder / f'{case_id}.json').read_bytes()).hexdigest() != entries[case_id]['file_sha256'][folder]:
                    raise ValueError('Observable data hash mismatch')
            expected_release = release(withheld, row['prediction'].get('request'), observed['records'])
            if row['released'] != expected_release:
                raise ValueError('Released evidence differs from the recorded single request')
            if not set(row['prediction'].get('categories') or []) <= set(manifest['categories']):
                raise ValueError('Unknown predicted categories')
    # No truth file is opened until both reports pass structural validation.
    truth = {}
    for case_id, entry in entries.items():
        truth_raw = (dataset / 'truth' / f'{case_id}.json').read_bytes()
        if hashlib.sha256(truth_raw).hexdigest() != entry['file_sha256']['truth']:
            raise ValueError('Frozen truth hash mismatch')
        truth[case_id] = json.loads(truth_raw)
    methods = {}
    for report in reports.values():
        for row in report['cases']:
            if row.get('family_id') != entries[row['case_id']]['family_id']:
                raise ValueError('Scenario family mismatch')
            methods.setdefault(row['method'], []).append({**row, 'truth': truth[row['case_id']]})
    metrics = {name: score(rows, manifest['categories']) for name, rows in methods.items()}
    strongest = max(BASELINES, key=lambda m: metrics[m]['category_macro_f1'])
    candidate = metrics['hybrid_selective']
    delta = _macro(methods['hybrid_selective'], manifest['categories']) - _macro(methods[strongest], manifest['categories'])
    interval = paired_interval(methods['hybrid_selective'], methods[strongest], manifest['categories'])
    obtained, needed = map(int, candidate['decisive_evidence_obtained'].split('/'))
    evidence_rate = Decimal(obtained) / Decimal(needed) if needed else None
    targets = {'gain_at_least_0_05': delta >= .05, 'gain_interval_above_zero': interval['lower'] > 0,
               'macro_f1_at_least_0_85': candidate['category_macro_f1'] >= .85,
               'decisive_evidence_at_least_0_90': evidence_rate >= Decimal('.90') if evidence_rate is not None else None,
               'conclusion_at_least_old_hybrid': candidate['conclusion_accuracy'] >= metrics['hybrid']['conclusion_accuracy']}
    by_slot = {str(slot): {m: score([r for r in rows if entries[r['case_id']]['wording_slot'] == slot], manifest['categories'])
                          for m, rows in methods.items()} for slot in range(4)}
    return {'schema_version': VERSION, 'dataset_manifest_sha256': digest, 'metrics': metrics,
            'strongest_same_budget_baseline': strongest, 'delta': round(delta, 4), 'paired_interval': interval,
            'preregistered_targets': targets, 'research_claim_supported': all(v is True for v in targets.values()),
            'by_wording_slot': by_slot, 'truth_read_after_all_predictions': True, 'truth_files_verified': len(truth),
            'limitations': ['Synthetic same-author replication, not independent enterprise evidence.',
                            'Nine scenario families; wording and repeats do not increase the family count.']}
