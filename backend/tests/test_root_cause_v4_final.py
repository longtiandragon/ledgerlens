import hashlib
import json

import pytest

from benchmarks import root_cause_v4_final as final

CATEGORIES = {'late_document': '单据迟到', 'duplicate_posting': '重复入账', 'invoice_voided': '发票作废',
              'normal_business': '正常业务'}


def truth(category, decisive=()):
    return {'categories': [category], 'decisive_kinds': list(decisive),
            'conclusion_with_evidence': 'normal', 'conclusion_without_evidence': 'insufficient'}


def dataset(tmp_path, version='root-cause-v4.0.0'):
    root = tmp_path / 'data'
    (root / 'truth').mkdir(parents=True)
    cases = []
    for case_id, family, value in [('c1', 'f1', truth('late_document', ['receipt'])),
                                   ('c2', 'f2', truth('duplicate_posting')),
                                   ('c3', 'f3', truth('invoice_voided'))]:
        raw = json.dumps(value).encode()
        (root / 'truth' / f'{case_id}.json').write_bytes(raw)
        cases.append({'case_id': case_id, 'family_id': family, 'split': 'sealed_test',
                      'file_sha256': {'truth': hashlib.sha256(raw).hexdigest()}})
    (root / 'manifest.json').write_text(json.dumps({'version': version, 'categories': CATEGORIES,
                                                    'cases': cases}), encoding='utf-8')
    return root, hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()


def row(method, case_id, family, categories, conclusion, released=(), error=None, route=None, paid=0):
    prediction = {'error': error} if error else {'categories': categories, 'conclusion': conclusion, 'request': None}
    entry = {'case_id': case_id, 'family_id': family, 'method': method, 'repeat': 1, 'prediction': prediction,
             'released': [{'kind': kind} for kind in released], 'paid_requests': paid, 'tokens': paid * 250}
    return {**entry, 'route': route} if route else entry


HYBRID_RUN = [
    row('rule_only', 'c1', 'f1', [], 'insufficient'),
    row('rule_only', 'c2', 'f2', ['duplicate_posting'], 'supported'),
    row('rule_only', 'c3', 'f3', ['normal_business'], 'normal'),
    row('rule_fixed_request', 'c1', 'f1', ['late_document'], 'normal', ['receipt']),
    row('rule_fixed_request', 'c2', 'f2', ['duplicate_posting'], 'supported'),
    row('rule_fixed_request', 'c3', 'f3', ['normal_business'], 'normal'),
    row('keyword_request', 'c1', 'f1', ['late_document'], 'normal', ['receipt']),
    row('keyword_request', 'c2', 'f2', [], 'insufficient'),
    row('keyword_request', 'c3', 'f3', ['normal_business'], 'normal'),
    row('hybrid', 'c1', 'f1', ['late_document'], 'normal', ['receipt'], route='deterministic'),
    row('hybrid', 'c2', 'f2', ['duplicate_posting'], 'supported', route='deterministic'),
    row('hybrid', 'c3', 'f3', ['invoice_voided'], 'supported', route='model_no_signal', paid=4),
]
SINGLE_RUN = [
    row('single', 'c1', 'f1', ['late_document'], 'normal', ['receipt'], paid=4),
    row('single', 'c2', 'f2', ['late_document'], 'normal', paid=4),
    row('single', 'c3', 'f3', ['invoice_voided'], 'supported', paid=4),
]


def write_reports(tmp_path, monkeypatch, manifest_sha, changed_plan=None):
    common = {'selected_case_ids': ['c1', 'c2', 'c3'], 'prompt_version': 'investigation-v27',
              'runner_version': 'rc-runner-v4', 'split': 'sealed_test', 'source_sha256': {'x': '0' * 64},
              'dataset_manifest_sha256': manifest_sha}
    plans, paths = {}, {}
    for name, modes, rows in [('hybrid', [*final.BASELINES, 'hybrid'], HYBRID_RUN), ('single', ['single'], SINGLE_RUN)]:
        plan = {**common, 'modes': modes, 'repeat': 1}
        report = {**plan, 'status': 'completed', 'paid_requests': 4, 'cases': rows}
        plans[name] = tmp_path / f'plan-{name}.json'
        plans[name].write_text(json.dumps({**plan, **((changed_plan or {}).get(name, {}))}), encoding='utf-8')
        paths[name] = tmp_path / f'report-{name}.json'
        paths[name].write_text(json.dumps(report), encoding='utf-8')
    monkeypatch.setattr(final, 'PLANS', {name: str(path.resolve()) for name, path in plans.items()})
    return paths


def test_preregistered_targets_and_route_breakdown(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha)
    output = tmp_path / 'final.json'
    assert final.main(['--dataset', str(root), '--hybrid', str(paths['hybrid']), '--single', str(paths['single']),
                       '--output', str(output)]) == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    # Hand-computed: the fixed request gets late 1.0, duplicate 1.0, voided 0 -> macro 0.6667;
    # the hybrid gets all three; single gets late 2/3, duplicate 0, voided 1 -> 0.5556.
    assert result['strongest_same_budget_baseline'] == 'rule_fixed_request'
    assert result['metrics']['rule_fixed_request']['category_macro_f1'] == 0.6667
    assert result['metrics']['hybrid']['category_macro_f1'] == 1.0
    assert result['metrics']['single']['category_macro_f1'] == 0.5556
    targets = result['preregistered_targets']
    assert targets['T1_gain_over_strongest_at_least_0_05'] is True
    # Three families: about 30% of draws omit the voided family, so the 2.5% bound is exactly 0.
    assert targets['T2_gain_interval_above_zero'] is False
    assert targets['T3_hybrid_at_least_single'] is True
    assert targets['T4_routed_structural_failures_at_most_1'] is True
    assert targets['T5_requests_per_case_at_most_60pct_of_single'] is True  # 4/3 per case against 4
    assert targets['goal_decisive_evidence_rate'] == 1.0
    assert result['hybrid_by_route']['deterministic']['samples'] == 2
    assert result['hybrid_by_route']['model_no_signal'] == {'samples': 1, 'failures': 0, 'paid_requests': 4,
                                                            'per_category_f1': {'invoice_voided': 1.0}}
    assert result['bootstrap']['seed'] == final.SEED
    assert set(result['bootstrap']['macro_f1_difference']) == {
        'hybrid-rule_fixed_request', 'single-rule_fixed_request', 'hybrid-single'}
    with pytest.raises(SystemExit):  # never overwrites an earlier analysis
        final.main(['--dataset', str(root), '--hybrid', str(paths['hybrid']), '--output', str(output)])


def test_single_run_may_be_absent_only_as_unknown_targets(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha)
    output = tmp_path / 'final.json'
    assert final.main(['--dataset', str(root), '--hybrid', str(paths['hybrid']), '--output', str(output)]) == 0
    targets = json.loads(output.read_text(encoding='utf-8'))['preregistered_targets']
    assert targets['T3_hybrid_at_least_single'] is None
    assert targets['T5_requests_per_case_at_most_60pct_of_single'] is None


def test_rejects_changed_plans_old_data_and_changed_truth(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha, changed_plan={'hybrid': {'repeat': 3}})
    with pytest.raises(SystemExit, match='frozen plans'):
        final.main(['--dataset', str(root), '--hybrid', str(paths['hybrid']), '--output', str(tmp_path / 'a.json')])
    paths = write_reports(tmp_path, monkeypatch, manifest_sha)
    (root / 'truth' / 'c1.json').write_text(json.dumps(truth('duplicate_posting')), encoding='utf-8')
    with pytest.raises(SystemExit, match='truth hash'):
        final.main(['--dataset', str(root), '--hybrid', str(paths['hybrid']), '--output', str(tmp_path / 'b.json')])
    old_root, _ = dataset(tmp_path / 'old', version='root-cause-v3.0.0')
    with pytest.raises(SystemExit):
        final.main(['--dataset', str(old_root), '--hybrid', str(paths['hybrid']), '--output', str(tmp_path / 'c.json')])
