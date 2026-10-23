"""Verify a frozen v5 plan or explicitly execute one authorized paid batch.

No arguments can alter frozen samples, modes or budget. No new report replaces
an existing run. Default is source/data verification with no keys or model calls.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='Use only after this batch budget is authorized')
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args(argv)
    # Imported settings may create DATA_DIR; keep even that outside the working ledger.
    runtime = ROOT / '.local/root-cause-v5-isolated-runtime'
    os.environ.update(DATA_DIR=str(runtime), DATABASE_URL='sqlite:///' + (runtime / 'unused.sqlite').as_posix(),
                      TASK_MODE='local', AUTH_MODE='single_user', TELEMETRY_ENABLED='false', DEEPSEEK_API_KEY='')
    from benchmarks.root_cause_v2_models import run, select, validate_plan
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    manifest_raw = (args.dataset / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    if plan.get('dataset_version') != 'root-cause-v5.0.0' or manifest.get('version') != plan['dataset_version']:
        raise ValueError('v5 data and plan required')
    report = ROOT / plan['report_path']
    if not report.resolve().is_relative_to(ROOT / '.local/reports'):
        raise ValueError('Report must stay inside isolated reports directory')
    options = argparse.Namespace(**{key: plan[key] for key in (
        'split', 'per_family', 'repeat', 'modes', 'max_paid_requests', 'max_cny')},
        dataset=args.dataset, report=report, plan=args.plan, env_file=args.env_file)
    # Source/data verification may precede the smoke; execution must enforce its result.
    verify_plan = plan if args.execute else {k: v for k, v in plan.items() if k != 'required_smoke'}
    validate_plan(verify_plan, options, manifest_raw, select(manifest, plan['split'], plan['per_family']), plan['model'])
    checked_files = 0
    for case in select(manifest, plan['split'], plan['per_family']):
        for folder in ('observed', 'on_demand'):
            path = args.dataset / folder / f"{case['case_id']}.json"
            if hashlib.sha256(path.read_bytes()).hexdigest() != case['file_sha256'][folder]:
                raise ValueError('Frozen observable file hash mismatch')
            checked_files += 1
    if not args.execute:
        print(json.dumps({'verified': True, 'executed': False,
                          'observable_files_verified': checked_files, 'truth_files_read': 0,
                          'smoke_gate': 'checked_on_execution' if plan.get('required_smoke') else 'not_required',
                          'plan_sha256': hashlib.sha256(args.plan.read_bytes()).hexdigest(), 'max_cny': plan['max_cny']}))
        return 0
    if args.env_file is None:
        raise ValueError('--env-file is required for explicitly authorized execution')
    # Settings are already loaded with no provider key. The research transport
    # reads its explicit env file; do not let our verification-only blank mask it.
    os.environ.pop('DEEPSEEK_API_KEY', None)
    report.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(options))
    print(json.dumps({'status': result['status'], 'paid_requests': result['paid_requests'], 'report': str(report)}))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
