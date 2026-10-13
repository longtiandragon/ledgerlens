import hashlib
import json

import pytest

from benchmarks import root_cause_v3_final as final

CATEGORIES = {'late_document': '单据迟到', 'duplicate_posting': '重复入账'}


def truth(category, decisive=()):
    return {'categories': [category], 'decisive_kinds': list(decisive),
            'conclusion_with_evidence': 'normal', 'conclusion_without_evidence': 'insufficient'}


def dataset(tmp_path):
    root = tmp_path / 'data'
    (root / 'truth').mkdir(parents=True)
    cases = []
    for case_id, family, value in [('c1', 'f1', truth('late_document', ['receipt'])),
                                   ('c2', 'f2', truth('duplicate_posting'))]:
        raw = json.dumps(value).encode()
        (root / 'truth' / f'{case_id}.json').write_bytes(raw)
        cases.append({'case_id': case_id, 'family_id': family, 'split': 'sealed_test',
                      'file_sha256': {'truth': hashlib.sha256(raw).hexdigest()}})
    (root / 'manifest.json').write_text(json.dumps({'version': 'root-cause-v3.0.0', 'categories': CATEGORIES,
                                                    'cases': cases}), encoding='utf-8')
    return root, hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()


def row(method, case_id, family, categories, conclusion, released=(), error=None, repeat=1):
    prediction = {'error': error} if error else {'categories': categories, 'conclusion': conclusion, 'request': None}
    return {'case_id': case_id, 'family_id': family, 'method': method, 'repeat': repeat, 'prediction': prediction,
            'released': [{'kind': kind} for kind in released], 'paid_requests': 0 if method in final.BASELINES else 4,
            'tokens': 0 if method in final.BASELINES else 1000}


def write_reports(tmp_path, monkeypatch, manifest_sha, single_rows, changed_plan=None):
    common = {'selected_case_ids': ['c1', 'c2'], 'prompt_version': 'investigation-v27', 'runner_version': 'rc-runner-v3',
              'split': 'sealed_test', 'source_sha256': {'x': '0' * 64}, 'dataset_manifest_sha256': manifest_sha}
    plans, paths = {}, {}
    for name, modes, repeat, rows in [
            ('single', [*final.BASELINES, 'single'], 1, single_rows),
            ('active', ['active'], 1, [row('active', 'c1', 'f1', ['late_document'], 'normal', ['receipt']),
                                       row('active', 'c2', 'f2', ['duplicate_posting'], 'normal')]),
            ('multi', ['multi'], 1, [row('multi', 'c1', 'f1', [], 'insufficient', error=None),
                                     row('multi', 'c2', 'f2', [], None, error={'code': 'model_output'})])]:
        plan = {**common, 'modes': modes, 'repeat': repeat}
        report = {**plan, 'status': 'completed', 'paid_requests': 8, 'cases': rows}
        plans[name] = tmp_path / f'plan-{name}.json'
        plans[name].write_text(json.dumps({**plan, **((changed_plan or {}).get(name, {}))}), encoding='utf-8')
        paths[name] = tmp_path / f'report-{name}.json'
        paths[name].write_text(json.dumps(report), encoding='utf-8')
    # Absolute plan paths: pathlib joins ROOT / absolute to the absolute path.
    monkeypatch.setattr(final, 'PLANS', {name: str(path.resolve()) for name, path in plans.items()})
    return paths


def baseline_rows(repeat=1):
    return [row('rule_only', 'c1', 'f1', [], 'insufficient', repeat=repeat),
            row('rule_only', 'c2', 'f2', ['duplicate_posting'], 'normal', repeat=repeat),
            row('rule_fixed_request', 'c1', 'f1', ['late_document'], 'normal', ['receipt'], repeat=repeat),
            row('rule_fixed_request', 'c2', 'f2', ['duplicate_posting'], 'normal', repeat=repeat),
            row('keyword_request', 'c1', 'f1', ['late_document'], 'normal', ['receipt'], repeat=repeat),
            row('keyword_request', 'c2', 'f2', [], 'insufficient', repeat=repeat),
            row('single', 'c1', 'f1', ['late_document'], 'normal', ['receipt'], repeat=repeat),
            row('single', 'c2', 'f2', ['late_document'], 'normal', repeat=repeat)]


def test_combines_frozen_runs_scores_failures_and_pairs_differences(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha, baseline_rows())
    output = tmp_path / 'final.json'
    assert final.main(['--dataset', str(root), '--single', str(paths['single']), '--active', str(paths['active']),
                       '--multi', str(paths['multi']), '--output', str(output)]) == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['strongest_same_budget_baseline'] == 'rule_fixed_request'
    assert result['metrics']['rule_fixed_request']['category_macro_f1'] == 1.0
    assert result['metrics']['multi']['failures'] == 1 and result['metrics']['multi']['samples'] == 2
    assert result['metrics']['active']['category_macro_f1'] == 1.0
    assert result['usage']['single'] == {'samples': 2, 'paid_requests': 8, 'tokens': 2000,
                                         'paid_requests_per_sample': 4.0, 'tokens_per_sample': 1000}
    assert set(result['bootstrap']['macro_f1_difference']) == {
        'single-rule_fixed_request', 'active-rule_fixed_request', 'multi-rule_fixed_request',
        'multi-single', 'active-single'}
    assert result['preregistered_targets']['active']['macro_f1_at_least_0_85'] is True
    assert result['preregistered_targets']['active']['gain_over_strongest_at_least_0_05'] is False
    with pytest.raises(SystemExit):          # never overwrites an earlier analysis
        final.main(['--dataset', str(root), '--single', str(paths['single']), '--active', str(paths['active']),
                    '--multi', str(paths['multi']), '--output', str(output)])


def test_rejects_runs_that_differ_from_their_frozen_plan(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha, baseline_rows(),
                          changed_plan={'active': {'repeat': 2}})
    with pytest.raises(SystemExit, match='frozen plans'):
        final.main(['--dataset', str(root), '--single', str(paths['single']), '--active', str(paths['active']),
                    '--multi', str(paths['multi']), '--output', str(tmp_path / 'out.json')])


def test_rejects_changed_truth_files(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha, baseline_rows())
    (root / 'truth' / 'c1.json').write_text(json.dumps(truth('duplicate_posting')), encoding='utf-8')
    with pytest.raises(SystemExit, match='truth hash'):
        final.main(['--dataset', str(root), '--single', str(paths['single']), '--active', str(paths['active']),
                    '--multi', str(paths['multi']), '--output', str(tmp_path / 'out.json')])
