"""Prepare an unpaid, reviewable development smoke plan. Never reads truth or API keys."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.root_cause_v2_models import PROMPT_VERSION, RUNNER_VERSION, select, source_hashes  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / '.local/datasets-v3')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--modes', nargs='+', default=['rule_only', 'rule_fixed_request', 'keyword_request', 'single'])
    parser.add_argument('--max-paid-requests', type=int, default=60)
    parser.add_argument('--max-cny', default='1.20')
    parser.add_argument('--estimated-offpeak-cny', nargs=2, default=['0.50', '0.90'])
    parser.add_argument('--estimate-basis', default='Prior v25 smoke, not a promise of completion or billed cost.')
    parser.add_argument('--hypothesis',
                        default='Narrow schema reduces output-contract failures; model accuracy may still be below baselines.')
    args = parser.parse_args()
    raw = (args.dataset / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest['version'] != 'root-cause-v3.0.0':
        parser.error('Fresh v3 data required')
    plan = {'schema_version': 'root-cause-experiment-plan-v1', 'status': 'prepared_not_authorized_or_run',
            'dataset_version': manifest['version'], 'dataset_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'source_sha256': source_hashes(), 'prompt_version': PROMPT_VERSION, 'runner_version': RUNNER_VERSION,
            'model': 'deepseek-flash', 'split': 'development', 'per_family': 1, 'repeat': 1,
            'modes': args.modes,
            'selected_case_ids': [c['case_id'] for c in select(manifest, 'development', 1)],
            'max_paid_requests': args.max_paid_requests, 'max_cny': args.max_cny,
            'estimated_offpeak_cny': args.estimated_offpeak_cny, 'estimate_basis': args.estimate_basis,
            'stop_policy': 'Peak-price pre-dispatch reservation; retain unknown attempts and all failures.',
            'hypothesis': args.hypothesis,
            'smoke_success': 'No output rejection after repair in any model sample of any model mode; report accuracy separately.',
            'limitations': ['Development smoke only, not an unseen performance claim.',
                            'Conservative peak reservations may stop before all nine cases complete.',
                            'No sealed evaluation or extra paid retry is authorized by preparing this file.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'prepared': str(args.output), 'cases': len(plan['selected_case_ids']), 'modes': args.modes,
                      'max_paid_requests': args.max_paid_requests, 'max_cny': args.max_cny}))


if __name__ == '__main__':
    main()
