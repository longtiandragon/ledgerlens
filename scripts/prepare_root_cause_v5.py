"""Freeze all v5 plans before paid work. Preparation does not authorize spending."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from benchmarks.root_cause_v2_models import PROMPT_VERSION, RUNNER_VERSION, select, source_hashes  # noqa: E402
from benchmarks.root_cause_v5_protocol import BASELINES  # noqa: E402
from scripts.prepare_root_cause_sealed_plan import path_hashes  # noqa: E402


def prepare(dataset: Path, destination: Path):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Refusing to overwrite plan directory')
    raw = (dataset / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest.get('version') != 'root-cause-v5.0.0':
        raise ValueError('Fresh v5 data required')
    destination.resolve().relative_to(ROOT)
    destination.mkdir(parents=True, exist_ok=True)
    sources = path_hashes()
    for name in ('scripts/prepare_root_cause_v5.py', 'scripts/analyse_root_cause_v5.py', 'scripts/run_root_cause_v5.py'):
        sources[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    common = {'schema_version': 'root-cause-experiment-plan-v1', 'status': 'frozen_pending_budget_authorization',
              'dataset_version': manifest['version'], 'dataset_manifest_sha256': hashlib.sha256(raw).hexdigest(),
              'source_sha256': source_hashes(), 'path_source_sha256': sources,
              'prompt_version': PROMPT_VERSION, 'runner_version': RUNNER_VERSION,
              'model': 'deepseek-flash', 'per_family': 4,
              'total_pre_dispatch_cap_cny': '8.00', 'budget_authorized_by_plan': False,
              'price_verified_on': '2026-10-22',
              'price_source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
              'reservation_policy': 'Before every request reserve peak input 2 CNY/M and output 8 CNY/M; unknown usage remains reserved.',
              'analysis': {'primary': 'hybrid_selective macro-F1 minus strongest of three same-budget baselines >= .05',
                           'uncertainty': 'paired family bootstrap, 2000 draws, seed 20261022; lower bound > 0',
                           'absolute_targets': ['macro-F1 >= .85', 'decisive evidence >= .90'],
                           'regression_target': 'conclusion accuracy at least old hybrid on the same cases',
                           'failure_policy': 'Every failed or budget-stopped row counts. No omitted samples or retries.',
                           'subgroups': 'Report each wording slot without changing the primary result.',
                           'no_tuning': 'No sealed predictions until all code, data, plans and smoke are frozen.'},
              'limitations': manifest['limitations']}
    requirements = {
        'smoke': ('development', 1, [*BASELINES, 'hybrid_selective'], 180, '2.00', '2.00'),
        'selective': ('sealed_test', 2, [*BASELINES, 'hybrid_selective'], 400, '3.50', '3.50'),
        'comparator': ('sealed_test', 2, ['hybrid'], 280, '2.50', '2.50'),
    }
    output = {}
    for name, (split, repeat, modes, requests, reservation, guard) in requirements.items():
        plan = {**common, 'split': split, 'repeat': repeat, 'modes': modes,
                'selected_case_ids': [c['case_id'] for c in select(manifest, split, 4)],
                'max_paid_requests': requests, 'max_cny': reservation, 'batch_pre_dispatch_cap_cny': guard,
                'report_path': f'.local/reports/root-cause-v5-{name}.json'}
        if name != 'smoke':
            plan['required_smoke'] = {'plan': (destination / 'smoke.json').relative_to(ROOT).as_posix(),
                                     'plan_sha256': output['smoke']['sha256'],
                                     'report': '.local/reports/root-cause-v5-smoke.json'}
        path = destination / f'{name}.json'
        payload = (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        with path.open('xb') as stream:
            stream.write(payload)
        output[name] = {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(),
                        'cases': len(plan['selected_case_ids']), 'repeat': repeat, 'pre_dispatch_cap_cny': guard}
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(prepare(options.dataset.resolve(), options.output.resolve()), ensure_ascii=False, indent=2))
