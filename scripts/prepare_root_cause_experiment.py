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
    args = parser.parse_args()
    raw = (args.dataset / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest['version'] != 'root-cause-v3.0.0':
        parser.error('Fresh v3 data required')
    plan = {'schema_version': 'root-cause-experiment-plan-v1', 'status': 'prepared_not_authorized_or_run',
            'dataset_version': manifest['version'], 'dataset_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'source_sha256': source_hashes(), 'prompt_version': PROMPT_VERSION, 'runner_version': RUNNER_VERSION,
            'model': 'deepseek-flash', 'split': 'development', 'per_family': 1, 'repeat': 1,
            'modes': ['rule_only', 'rule_fixed_request', 'keyword_request', 'single'],
            'selected_case_ids': [c['case_id'] for c in select(manifest, 'development', 1)],
            'max_paid_requests': 60, 'max_cny': '1.20',
            'estimated_total_tokens': [300000, 600000], 'estimated_offpeak_cny': ['0.50', '0.90'],
            'estimate_basis': 'Prior v25 smoke, not a promise of completion or billed cost.',
            'stop_policy': 'Peak-price pre-dispatch reservation; retain unknown attempts and all failures.',
            'hypothesis': 'Narrow schema reduces output-contract failures; model accuracy may still be below baselines.',
            'smoke_success': 'No schema/citation rejection after repair in all nine model samples; report accuracy separately.',
            'limitations': ['Development smoke only, not an unseen performance claim.',
                            'Conservative peak reservations may stop before all nine cases complete.',
                            'No sealed evaluation or extra paid retry is authorized by preparing this file.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'prepared': str(args.output), 'cases': 9, 'max_paid_requests': 60, 'max_cny': '1.20'}))


if __name__ == '__main__':
    main()
