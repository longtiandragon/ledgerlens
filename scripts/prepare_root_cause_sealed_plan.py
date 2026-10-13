"""Freeze a sealed-test plan for the v3 root-cause comparison. Never reads truth or API keys.

The runner validates the dataset manifest, its 13 frozen sources, selected cases, model and
run parameters. This plan additionally records the other modules on the investigation import
path, checked by --verify before any paid run, and pre-registers the analysis.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.root_cause_v2_models import PROMPT_VERSION, RUNNER_VERSION, select, source_hashes  # noqa: E402

# Imported by investigation.py at module level, or lazily on the reference/policy/matching paths.
PATH_SOURCES = ('backend/app/business_chain.py', 'backend/app/context_transport.py',
                'backend/app/policy_context_contract.py', 'backend/app/reference_selection.py',
                'backend/app/knowledge.py', 'backend/app/matching.py', 'backend/app/financial_math.py',
                'backend/app/config.py')


def path_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PATH_SOURCES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / '.local/datasets-v3')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify', type=Path, help='check an existing plan against the current checkout')
    parser.add_argument('--per-family', type=int, default=1)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--modes', nargs='+', default=['rule_only', 'rule_fixed_request', 'keyword_request', 'single'])
    parser.add_argument('--max-paid-requests', type=int, required=False)
    parser.add_argument('--max-cny', required=False)
    parser.add_argument('--smoke-report', type=Path, help='development smoke the plan is based on')
    args = parser.parse_args()
    raw = (args.dataset / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if args.verify:
        plan = json.loads(args.verify.read_text(encoding='utf-8'))
        problems = [name for name, digest in plan['path_source_sha256'].items()
                    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
        if plan['source_sha256'] != source_hashes():
            problems.append('runner frozen sources')
        if plan['dataset_manifest_sha256'] != hashlib.sha256(raw).hexdigest():
            problems.append('dataset manifest')
        print(json.dumps({'plan': str(args.verify), 'ok': not problems, 'problems': problems}))
        raise SystemExit(1 if problems else 0)
    if manifest['version'] != 'root-cause-v3.0.0':
        parser.error('Fresh v3 data required')
    if not (args.output and args.max_paid_requests and args.max_cny and args.smoke_report):
        parser.error('--output, --max-paid-requests, --max-cny and --smoke-report are required')
    smoke = json.loads(args.smoke_report.read_text(encoding='utf-8'))
    cases = select(manifest, 'sealed_test', args.per_family)
    plan = {'schema_version': 'root-cause-experiment-plan-v1', 'status': 'frozen_for_sealed_final',
            'dataset_version': manifest['version'], 'dataset_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'source_sha256': source_hashes(), 'path_source_sha256': path_hashes(),
            'prompt_version': PROMPT_VERSION, 'runner_version': RUNNER_VERSION, 'model': 'deepseek-flash',
            'split': 'sealed_test', 'per_family': args.per_family, 'repeat': args.repeat, 'modes': args.modes,
            'selected_case_ids': [c['case_id'] for c in cases],
            'max_paid_requests': args.max_paid_requests, 'max_cny': args.max_cny,
            'based_on_smoke': {'report': str(args.smoke_report).replace('\\', '/'),
                               'sha256': hashlib.sha256(args.smoke_report.read_bytes()).hexdigest(),
                               'status': smoke.get('status'), 'paid_requests': smoke.get('paid_requests')},
            'stop_policy': 'Peak-price pre-dispatch reservation under max_cny; external guard; retain every '
                           'failure, unknown attempt and budget stop in the denominator.',
            'preregistered_analysis': {
                'primary_metric': 'cause category macro-F1 per method over all samples, failures scored as wrong',
                'secondary_metrics': ['conclusion accuracy', 'decisive evidence obtained', 'output failures',
                                      'paid requests and tokens per sample'],
                'strongest_same_budget_baseline': 'best deterministic method on this sealed run '
                                                  '(expected keyword_request from validation)',
                'targets_from_plan': {'macro_f1': '>= 0.85', 'insufficient_or_valid_request': '>= 0.90',
                                      'gain_over_strongest_baseline': '>= +0.05 F1, or quality loss <= 0.02 '
                                                                      'with cost reduction >= 20%'},
                'uncertainty': 'family-level bootstrap interval (cases resampled by family, repeats kept)',
                'no_tuning': 'Sealed split never used before; no prompt, contract, keyword or parameter '
                             'change after any sealed result is seen.'},
            'limitations': ['Same-author synthetic data; not real-ledger evidence.',
                            'Small sample: one case per family unless per_family > 1.',
                            'deepseek-flash now serves DeepSeek-V4.1-Flash; earlier v2 runs used an older model.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(args.output), 'cases': len(cases), 'modes': args.modes, 'repeat': args.repeat}))


if __name__ == '__main__':
    main()
