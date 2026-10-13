import hashlib
import json

import pytest

from benchmarks import root_cause_v3_partial as partial

CATEGORIES = {'late_document': '单据迟到', 'duplicate_posting': '重复入账'}
CASES = [('c1', 'f1', 'late_document', ['receipt']), ('c2', 'f2', 'duplicate_posting', []),
         ('c3', 'f3', 'late_document', ['receipt'])]


def dataset(tmp_path):
    root = tmp_path / 'data'
    (root / 'truth').mkdir(parents=True)
    cases = []
    for case_id, family, category, decisive in CASES:
        raw = json.dumps({'categories': [category], 'decisive_kinds': decisive, 'conclusion_with_evidence': 'normal',
                          'conclusion_without_evidence': 'insufficient' if decisive else 'supported'}).encode()
        (root / 'truth' / f'{case_id}.json').write_bytes(raw)
        cases.append({'case_id': case_id, 'family_id': family, 'split': 'sealed_test',
                      'file_sha256': {'truth': hashlib.sha256(raw).hexdigest()}})
    (root / 'manifest.json').write_text(json.dumps({'version': 'root-cause-v3.0.0', 'categories': CATEGORIES,
                                                    'cases': cases}), encoding='utf-8')
    return root, hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()


def row(method, case_id, categories, conclusion, released=(), error=None, repeat=1):
    family = 'f' + case_id[1:]
    prediction = {'error': error} if error else {'categories': categories, 'conclusion': conclusion, 'request': None}
    return {'case_id': case_id, 'family_id': family, 'method': method, 'repeat': repeat, 'prediction': prediction,
            'released': [{'kind': kind} for kind in released], 'paid_requests': 0 if method in partial.BASELINES else 4,
            'tokens': 0 if method in partial.BASELINES else 1000}


def single_rows():
    rows = []
    for repeat in (1, 2):
        rows += [row('rule_only', 'c1', [], 'insufficient', repeat=repeat),
                 row('rule_only', 'c2', ['duplicate_posting'], 'supported', repeat=repeat),
                 row('rule_only', 'c3', [], 'insufficient', repeat=repeat),
                 row('rule_fixed_request', 'c1', ['late_document'], 'normal', ['receipt'], repeat=repeat),
                 row('rule_fixed_request', 'c2', ['duplicate_posting'], 'supported', repeat=repeat),
                 row('rule_fixed_request', 'c3', ['late_document'], 'normal', ['receipt'], repeat=repeat),
                 row('keyword_request', 'c1', [], 'insufficient', repeat=repeat),
                 row('keyword_request', 'c2', ['duplicate_posting'], 'supported', repeat=repeat),
                 row('keyword_request', 'c3', [], 'insufficient', repeat=repeat),
                 row('single', 'c1', ['late_document'], 'normal', ['receipt'], repeat=repeat),
                 row('single', 'c2', ['late_document'], 'supported', repeat=repeat),
                 row('single', 'c3', [], 'insufficient', repeat=repeat)]
    return rows


def write_reports(tmp_path, monkeypatch, manifest_sha, active_status='running', changed_plan=None):
    common = {'selected_case_ids': ['c1', 'c2', 'c3'], 'prompt_version': 'investigation-v27',
              'runner_version': 'rc-runner-v3', 'split': 'sealed_test', 'source_sha256': {'x': '0' * 64},
              'dataset_manifest_sha256': manifest_sha}
    runs = [('single', [*partial.BASELINES, 'single'], 2, 'completed', single_rows()),
            # interrupted after two samples of repeat 1; the third sample was in flight
            ('active', ['active'], 2, active_status,
             [row('active', 'c1', ['late_document'], 'normal', ['receipt']),
              row('active', 'c2', [], None, error={'code': 'model_output'})]),
            ('multi', ['multi'], 1, 'running', [row('multi', 'c1', ['late_document'], 'normal', ['receipt'])])]
    plans, paths = {}, {}
    for name, modes, repeat, status, rows in runs:
        plan = {**common, 'modes': modes, 'repeat': repeat}
        pending = ({'case_id': 'c3', 'method': modes[-1], 'repeat': 1, 'model_requests': [], 'paid_requests': 1}
                   if status == 'running' else None)
        report = {**plan, 'status': status, 'cases': rows, 'current_sample': pending,
                  'paid_requests': sum(r['paid_requests'] for r in rows) + (1 if pending else 0)}
        plans[name] = tmp_path / f'plan-{name}.json'
        plans[name].write_text(json.dumps({**plan, **((changed_plan or {}).get(name, {}))}), encoding='utf-8')
        paths[name] = tmp_path / f'report-{name}.json'
        paths[name].write_text(json.dumps(report), encoding='utf-8')
    monkeypatch.setattr(partial, 'PLANS', {name: str(path.resolve()) for name, path in plans.items()})
    return paths


def run(root, paths, output):
    return partial.main(['--dataset', str(root), '--single', str(paths['single']), '--active', str(paths['active']),
                         '--multi', str(paths['multi']), '--interruption', 'host memory reaper',
                         '--output', str(output)])


def test_complete_run_is_analysed_as_planned_and_interrupted_runs_only_described(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha)
    output = tmp_path / 'partial.json'
    assert run(root, paths, output) == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    primary = result['primary_single_agent']
    assert primary['strongest_same_budget_baseline'] == 'rule_fixed_request'
    assert primary['methods']['single']['samples'] == 6 and primary['methods']['rule_fixed_request']['category_macro_f1'] == 1.0
    assert set(primary['bootstrap']['macro_f1_difference']) == {'single-rule_fixed_request'}
    assert primary['preregistered_targets']['single']['decisive_evidence_rate'] == 0.5
    described = result['interrupted_runs_descriptive']
    assert described['all_completed_samples']['active']['samples'] == 2
    assert described['all_completed_samples']['active']['failures'] == 1
    same = described['same_cases_first_repeat']
    assert same['case_ids'] == ['c1'] and same['families'] == 1
    assert {m: v['samples'] for m, v in same['methods'].items()} == {
        'rule_only': 1, 'rule_fixed_request': 1, 'keyword_request': 1, 'single': 1, 'active': 1, 'multi': 1}
    assert result['reports']['active']['planned_samples'] == 6
    assert result['reports']['active']['completed_samples'] == 2
    assert result['reports']['active']['lost_in_flight_sample'] is True
    assert result['reports']['single']['lost_in_flight_sample'] is False
    assert 'host memory reaper' in result['deviation']
    with pytest.raises(SystemExit):          # never overwrites an earlier analysis
        run(root, paths, output)


def test_only_the_completion_status_may_differ_from_the_frozen_plan(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha, changed_plan={'multi': {'repeat': 3}})
    with pytest.raises(SystemExit, match='frozen plans'):
        run(root, paths, tmp_path / 'out.json')
    paths = write_reports(tmp_path, monkeypatch, manifest_sha, active_status='failed')
    with pytest.raises(SystemExit, match=r'run did not complete \(failed\)'):
        run(root, paths, tmp_path / 'out2.json')


def test_the_single_agent_run_must_be_complete(tmp_path, monkeypatch):
    root, manifest_sha = dataset(tmp_path)
    paths = write_reports(tmp_path, monkeypatch, manifest_sha)
    report = json.loads(paths['single'].read_text(encoding='utf-8'))
    paths['single'].write_text(json.dumps({**report, 'status': 'running'}), encoding='utf-8')
    with pytest.raises(SystemExit, match=r'single: run did not complete'):
        run(root, paths, tmp_path / 'out.json')
