"""Offline challenge pairs for temporal scope and draft recomputation (Apache-2.0).

Handwritten expected outcomes never enter the investigation or simulation.
These deterministic stress tests measure mechanism coverage, not model gains.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.domain import simulate  # noqa: E402
from app.investigation import investigate  # noqa: E402

VERSION = 'mechanism-challenges-v1'
CUTOFF = '2026-09-30T23:59:59+00:00'
POLICY = {'framework': 'CAS', 'version': VERSION, 'currency': 'CNY', 'period': '2026-09',
          'snapshot_cutoff': CUTOFF, 'accounts': ['1002', '1405', '2202', '6602'], 'locked_periods': []}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def record(identity, kind, amount='0.00', **fields):
    return {'id': identity, 'evidence_id': 'ev-' + identity, 'source_id': identity,
            'reference': identity, 'kind': kind, 'amount': amount, 'book_id': 'ablation-book',
            'order_id': 'ORDER-A', 'counterparty': '合成供应商', 'currency': 'CNY',
            'business_date': '2026-09-20', 'period': '2026-09',
            'observed_at': '2026-09-30T12:00:00+00:00', **fields}


def temporal_fixtures():
    base = [record('a1', 'order', '100.00'), record('a2', 'receipt', '60.00')]
    late = record('a3', 'receipt', '40.00', observed_at='2026-10-03T09:00:00+00:00')
    return [
        {'id': 't1', 'description': '期内收货凭据在截止日后取得', 'visible': deepcopy(base), 'later': [late],
         'expected_at_cutoff': 'insufficient', 'expected_unscoped': 'normal'},
        {'id': 't2', 'description': '期后才收货，不能填入期末调查', 'visible': deepcopy(base),
         'later': [{**late, 'business_date': '2026-10-02', 'period': '2026-10'}],
         'expected_at_cutoff': 'insufficient', 'expected_unscoped': 'normal'},
        {'id': 't3', 'description': '负对照：后来凭据仍不足以填平数量缺口', 'visible': deepcopy(base),
         'later': [{**late, 'amount': '10.00'}],
         'expected_at_cutoff': 'insufficient', 'expected_unscoped': 'insufficient'},
        {'id': 't4', 'description': '负对照：截止时已完整，后来资料无关',
         'visible': [base[0], {**base[1], 'amount': '100.00'}],
         'later': [record('b1', 'bank', observed_at='2026-10-03T09:00:00+00:00')],
         'expected_at_cutoff': 'normal', 'expected_unscoped': 'normal'},
    ]


async def temporal_comparison():
    rows = []
    for fixture in temporal_fixtures():
        visible, later = fixture['visible'], fixture['later']
        original = digest([visible, later, POLICY])
        task = {'task_type': 'root_cause', 'rule_id': 'ROOT_CAUSE_TASK', 'record_ids': [],
                'evidence_ids': [], 'metrics': {'cause_taxonomy': {'unknown': '仅复核当前证据'}}}
        for method in ('snapshot', 'no_time_view'):
            config = ({'ablation': 'no_time_view', 'synthetic_evaluation': True,
                       'comparison_records': [*visible, *later]} if method == 'no_time_view' else None)
            result = await investigate(visible, POLICY, task, mode='rules', checkpoint_config=config)
            predicted = result['fact_status'] if result['error'] is None else None
            rows.append({'case_id': fixture['id'], 'method': method, 'expected_at_cutoff': fixture['expected_at_cutoff'],
                         'predicted': predicted, 'error': result['error'],
                         'correct_historical_status': predicted == fixture['expected_at_cutoff'],
                         'false_historical_normal': predicted == 'normal' and fixture['expected_at_cutoff'] != 'normal',
                         'source_unchanged': digest([visible, later, POLICY]) == original})
    return {'samples_per_method': 4, 'rows': rows, 'methods': {
        method: {'correct': sum(r['correct_historical_status'] for r in rows if r['method'] == method),
                 'false_historical_normal': sum(r['false_historical_normal'] for r in rows if r['method'] == method)}
        for method in ('snapshot', 'no_time_view')}}


def simulation_fixtures():
    source = [record('j1', 'journal', account='1405', debit='200.00', credit='0.00', voucher_id='J1'),
              record('j2', 'journal', account='2202', debit='0.00', credit='200.00', voucher_id='J1')]
    draft = [{'account': '2202', 'debit': '100.00', 'credit': '0.00', 'period': '2026-09', 'business_date': '2026-09-30'},
             {'account': '1405', 'debit': '0.00', 'credit': '100.00', 'period': '2026-09', 'business_date': '2026-09-30'}]
    delta = {'1405': '-100.00', '2202': '100.00'}
    # Independently authored validity labels; not obtained from simulate().
    return source, [
        {'id': 's1', 'description': '正确草稿及影响说明', 'draft': deepcopy(draft), 'policy': deepcopy(POLICY),
         'claimed_delta': delta, 'valid': True},
        {'id': 's2', 'description': '草稿金额正确但影响说明差一分', 'draft': deepcopy(draft), 'policy': deepcopy(POLICY),
         'claimed_delta': {'1405': '-99.99', '2202': '100.00'}, 'valid': False},
        {'id': 's3', 'description': '草稿借贷不平', 'draft': [draft[0], {**draft[1], 'credit': '99.99'}],
         'policy': deepcopy(POLICY), 'claimed_delta': {'1405': '-99.99', '2202': '100.00'}, 'valid': False},
        {'id': 's4', 'description': '草稿写入锁定期间', 'draft': deepcopy(draft),
         'policy': {**POLICY, 'locked_periods': ['2026-09']}, 'claimed_delta': delta, 'valid': False},
        {'id': 's5', 'description': '草稿使用未知科目', 'draft': [draft[0], {**draft[1], 'account': '9999'}],
         'policy': deepcopy(POLICY), 'claimed_delta': {'9999': '-100.00', '2202': '100.00'}, 'valid': False},
        {'id': 's6', 'description': '边界反例：科目合法且平衡，但把冲减存货错记为冲减费用',
         'draft': [draft[0], {**draft[1], 'account': '6602'}], 'policy': deepcopy(POLICY),
         'claimed_delta': {'6602': '-100.00', '2202': '100.00'}, 'valid': False},
    ]


def recompute_draft(source, draft, policy, claimed_delta):
    result = simulate(source, draft, policy)
    actual = {k: v for k, v in result['delta'].items() if Decimal(v) != 0}
    claimed = {k: v for k, v in claimed_delta.items() if Decimal(v) != 0}
    matches = actual == claimed
    return {'accepted': result['balanced'] and not result['violations'] and matches,
            'balanced': result['balanced'], 'violations': result['violations'],
            'declared_effect_matches': matches, 'actual_delta': actual}


def simulation_comparison():
    source, fixtures = simulation_fixtures()
    rows = []
    for fixture in fixtures:
        original = digest([source, fixture])
        # Both arms receive exactly the same frozen draft and claimed effect.
        verified = recompute_draft(source, fixture['draft'], fixture['policy'], fixture['claimed_delta'])
        for method in ('program_recompute', 'trust_proposal_without_recompute'):
            accepted = verified['accepted'] if method == 'program_recompute' else True
            rows.append({'case_id': fixture['id'], 'method': method, 'expected_valid': fixture['valid'],
                         'accepted': accepted, 'false_acceptance': accepted and not fixture['valid'],
                         'source_unchanged': digest([source, fixture]) == original,
                         'verification': verified if method == 'program_recompute' else None})
    return {'samples_per_method': len(fixtures), 'invalid_drafts': 5, 'rows': rows, 'methods': {
        method: {'false_acceptance': sum(r['false_acceptance'] for r in rows if r['method'] == method),
                 'correct': sum(r['accepted'] == r['expected_valid'] for r in rows if r['method'] == method)}
        for method in ('program_recompute', 'trust_proposal_without_recompute')}}


async def run():
    time_view = await temporal_comparison()
    simulation = simulation_comparison()
    return {'schema_version': VERSION, 'created_at': datetime.now(UTC).isoformat(), 'model_requests': 0,
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                'benchmarks/mechanism_ablations.py', 'backend/app/domain.py', 'backend/app/investigation.py')},
            'temporal': time_view, 'simulation': simulation,
            'all_sources_unchanged': all(r['source_unchanged'] for r in time_view['rows'] + simulation['rows']),
            'limitations': ['Handwritten synthetic challenge pairs, not sampled model output or real-world error rates.',
                            'Temporal metric measures historical overclaim, not semantic root-cause accuracy.',
                            'Simulation checks arithmetic and rule coverage; balanced wrong-purpose entries can pass.',
                            'No-counterproof and actual model-suggestion closed-loop ablations remain unmeasured.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Refusing to overwrite an existing report')
    report = asyncio.run(run())
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: report[key] for key in ('model_requests', 'all_sources_unchanged')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
