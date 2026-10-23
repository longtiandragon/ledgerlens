"""Fresh-data, request-budget and evaluation-integrity regressions. No paid calls."""
import argparse
from copy import deepcopy
import hashlib
import json
from decimal import Decimal

import httpx
import pytest

from benchmarks.root_cause_v2 import KEYWORDS
from benchmarks import root_cause_v2_models as runner
from benchmarks.root_cause_v5_protocol import BASELINES, _macro, analyse, check_smoke, plan_identity, validate_rows
from datasets import generate_v2 as v2, generate_v3 as v3, generate_v4 as v4, generate_v5 as v5
from app.model_gateway import DeepSeekGateway


def test_new_balanced_wording_and_all_slots_selected():
    old = {p for mod in (v2, v3, v4) for splits in mod.TEXT.values() for phrases in splits.values() for p in phrases}
    fresh = [p for splits in v5.TEXT.values() for phrases in splits.values() for p in phrases]
    assert len(fresh) == len(set(fresh)) == 48
    assert not old.intersection(fresh)
    mapping = {'order_reduced': 'order_change', 'invoice_voided': 'invoice_voided', 'price_adjustment': 'price_adjustment'}
    all_words = {w for words in KEYWORDS.values() for w in words}
    for name, splits in v5.TEXT.items():
        own = set(KEYWORDS[mapping[name]]) if name in mapping else set()
        for phrases in splits.values():
            assert sum(any(w in p for w in own) for p in phrases) == (2 if own else 0)
            assert not any(w in p for w in all_words - own for p in phrases)
    assert not set(map(tuple, v5.TOPOLOGIES)).intersection(
        tuple(t) for mod in (v2, v3, v4) for t in mod.TOPOLOGIES)


def test_new_dataset_hashes_no_truth_in_observed_and_no_overwrite(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    assert v5.write_dataset(a) == v5.write_dataset(b)
    manifest = json.loads((a / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['case_count'] == 108
    for split in v2.SPLITS:
        selected = runner.select(manifest, split, 4)
        assert len(selected) == 36
        for family in {c['family_id'] for c in selected}:
            assert sorted(c['wording_slot'] for c in selected if c['family_id'] == family) == [0, 1, 2, 3]
    for entry in manifest['cases']:
        for folder, digest in entry['file_sha256'].items():
            raw = (a / folder / f"{entry['case_id']}.json").read_bytes()
            assert hashlib.sha256(raw).hexdigest() == digest
            if folder != 'truth':
                assert not any(cause.encode() in raw for cause in v2.CAUSES)
    with pytest.raises(ValueError, match='overwrite'):
        v5.write_dataset(a)


def test_hand_computed_development_amounts():
    observed, withheld, truth = v5.build_case('gap_order_reduced', 'development', 0)
    # 173619 cents per percent * 66 percent / 100 = 114588.54 CNY.
    assert withheld[0]['amount'] == '-114588.54'
    assert truth['conclusion_without_evidence'] == 'insufficient'
    assert sum(Decimal(r.get('debit', '0')) for r in observed['records']) == sum(
        Decimal(r.get('credit', '0')) for r in observed['records'])


@pytest.mark.parametrize('cause', [c for c in v2.CAUSES if not c.startswith('dup_')])
def test_selective_does_not_look_at_withheld_or_spend_request_before_model(cause):
    class Unreadable:
        def __iter__(self):
            raise AssertionError('withheld material was read before a request')
    observed, _, _ = v5.build_case(cause, 'development', 0)
    assert runner.selective_route(observed, Unreadable()) == {'route': 'model_choose_request', 'outcome': None}


@pytest.mark.parametrize('cause, expected', [('dup_true', 'duplicate_posting'), ('dup_second_delivery', 'reference_mismatch')])
def test_duplicate_route_preserves_fixed_single_invoice_request(cause, expected):
    observed, withheld, _ = v5.build_case(cause, 'development', 0)
    route = runner.selective_route(observed, withheld)
    assert route['route'] == 'deterministic'
    assert route['outcome']['request'] == {'kind': 'invoice'}
    assert route['outcome']['categories'] == [expected]


@pytest.mark.asyncio
async def test_new_runner_rejects_unbalanced_selection_before_any_provider(tmp_path):
    v5.write_dataset(tmp_path / 'ds')
    options = argparse.Namespace(dataset=tmp_path / 'ds', split='sealed_test', per_family=1,
                                 modes=['hybrid_selective'], repeat=2, max_paid_requests=400,
                                 report=tmp_path / 'report.json')
    with pytest.raises(ValueError, match='four wording slots'):
        await runner.run(options)
    assert not options.report.exists()


@pytest.mark.asyncio
async def test_selective_runner_offline_mock_keeps_failures_and_request_counts(tmp_path, monkeypatch):
    from app.context_transport import deserialize_context
    v5.write_dataset(tmp_path / 'ds')
    seen = []

    def respond(request):
        context = deserialize_context(json.loads(request.content)['messages'][1]['content'])
        seen.append(context['finding']['metrics']['evidence_request_allowed'])
        payload = {'status': 'insufficient', 'cause_assessment': {
            'categories': [], 'explanation': '资料不足，保留判断。', 'evidence_ids': []}}
        return httpx.Response(200, json={'model': 'offline', 'usage': {'prompt_tokens': 6, 'completion_tokens': 4, 'total_tokens': 10},
                                       'choices': [{'finish_reason': 'stop', 'message': {
                                           'role': 'assistant', 'content': json.dumps(payload)}}]})

    monkeypatch.setattr(runner, '_model_configuration', lambda _: {
        'DEEPSEEK_API_KEY': 'offline-key', 'DEEPSEEK_BASE_URL': '', 'DEEPSEEK_MODEL': ''})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        gateway = DeepSeekGateway('offline-key', client=client)
        monkeypatch.setattr(runner, 'DeepSeekGateway', lambda *a, **kw: gateway)
        options = argparse.Namespace(dataset=tmp_path / 'ds', split='development', per_family=4,
                                     modes=['hybrid_selective'], repeat=1, max_paid_requests=180,
                                     env_file=None, report=tmp_path / 'report.json')
        report = await runner.run(options)
    assert len(report['cases']) == 36
    assert report['paid_requests'] == 28
    assert seen == [True] * 28  # Every model keeps its request instead of receiving a spent one.
    assert sum(r['paid_requests'] == 0 for r in report['cases']) == 8
    assert report['metrics']['hybrid_selective']['category_macro_f1'] < .85  # mock success is not research success


def small_report():
    plan = {'dataset_version': 'v5-test', 'dataset_manifest_sha256': 'hash', 'source_sha256': {},
            'selected_case_ids': ['c1'], 'modes': ['single'], 'repeat': 1, 'split': 'development',
            'prompt_version': 'test', 'runner_version': 'test', 'max_paid_requests': 1,
            'path_source_sha256': {}, 'model': 'offline'}
    report = {**plan, 'status': 'completed', 'paid_requests': 1, 'cases': [
        {'case_id': 'c1', 'method': 'single', 'repeat': 1, 'paid_requests': 1,
         'prediction': {'categories': []}, 'released': []}], 'frozen_plan_sha256': plan_identity(plan)}
    return plan, report


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'foreign', 'cost', 'truth', 'incomplete'])
def test_report_success_marker_cannot_hide_missing_or_tampered_rows(damage):
    plan, report = small_report()
    validate_rows(report, plan)
    if damage == 'missing':
        report['cases'].clear()
    elif damage == 'duplicate':
        report['cases'].append(deepcopy(report['cases'][0]))
    elif damage == 'foreign':
        report['cases'][0]['case_id'] = 'other'
    elif damage == 'cost':
        report['paid_requests'] = 0
    elif damage == 'truth':
        report['cases'][0]['truth'] = {}
    else:
        report['status'] = 'running'
    with pytest.raises(ValueError):
        validate_rows(report, plan)


def test_failed_smoke_cannot_unlock_sealed_run(tmp_path):
    plan, report = small_report()
    report['cases'][0]['prediction']['error'] = {'code': 'output_rejected'}
    report['cases'][0]['error'] = {'code': 'output_rejected'}
    report['status'] = 'completed_with_failures'
    plan_path, report_path = tmp_path / 'plan.json', tmp_path / 'report.json'
    plan_path.write_text(json.dumps(plan), encoding='utf-8')
    report_path.write_text(json.dumps(report), encoding='utf-8')
    requirement = {'plan': 'plan.json', 'report': 'report.json',
                   'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='smoke failed'):
        check_smoke(tmp_path, requirement)


def test_bootstrap_f1_retains_failures_as_false_negatives():
    rows = [{'truth': {'categories': ['a']}, 'prediction': {'categories': ['a']}},
            {'truth': {'categories': ['a']}, 'prediction': {'error': {'code': 'failed'}}}]
    assert _macro(rows, {'a': 'A'}) == pytest.approx(2 / 3)


def paired_fixture(tmp_path):
    dataset = tmp_path / 'ds'
    v5.write_dataset(dataset)
    raw = (dataset / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    cases = runner.select(manifest, 'sealed_test', 4)
    plans, reports = {}, {}
    for name, modes in [('selective', [*BASELINES, 'hybrid_selective']), ('comparator', ['hybrid'])]:
        plan = {'dataset_version': v5.VERSION, 'dataset_manifest_sha256': hashlib.sha256(raw).hexdigest(),
                'source_sha256': {'code': 'digest'}, 'path_source_sha256': {'path': 'digest'},
                'selected_case_ids': [c['case_id'] for c in cases], 'modes': modes, 'repeat': 2,
                'split': 'sealed_test', 'per_family': 4, 'model': 'deepseek-flash',
                'prompt_version': 'test', 'runner_version': 'test', 'max_paid_requests': 400, 'analysis': {}}
        reports[name] = {**plan, 'frozen_plan_sha256': plan_identity(plan), 'status': 'completed_with_failures',
                         'paid_requests': 0, 'scoring_deferred': True, 'cases': [
                             {'case_id': c['case_id'], 'family_id': c['family_id'], 'method': mode, 'repeat': repeat,
                              'paid_requests': 0, 'error': {'code': 'batch_budget_exhausted'},
                              'prediction': {'error': {'code': 'batch_budget_exhausted'}}, 'released': []}
                             for c in cases for mode in modes for repeat in (1, 2)]}
        plans[name] = plan
    return dataset, reports, plans


@pytest.mark.parametrize('damage', ['source', 'model', 'scoring', 'release', 'missing', 'truth_hash'])
def test_paired_analysis_rejects_invalid_or_unpaired_evidence(tmp_path, damage):
    dataset, reports, plans = paired_fixture(tmp_path)
    if damage in ('source', 'model'):
        key = 'source_sha256' if damage == 'source' else 'model'
        plans['comparator'][key] = {'different': 'code'} if damage == 'source' else 'different-model'
        reports['comparator'][key] = plans['comparator'][key]
        reports['comparator']['frozen_plan_sha256'] = plan_identity(plans['comparator'])
    elif damage == 'scoring':
        reports['selective']['metrics'] = {}
    elif damage == 'release':
        reports['selective']['cases'][0]['released'] = [{'kind': 'invoice', 'amount': '999.00'}]
    elif damage == 'missing':
        reports['selective']['cases'].pop()
    else:
        case_id = plans['selective']['selected_case_ids'][0]
        (dataset / 'truth' / f'{case_id}.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        analyse(dataset, reports, plans)


def test_all_failed_completed_runs_do_not_establish_research_gain(tmp_path):
    dataset, reports, plans = paired_fixture(tmp_path)
    result = analyse(dataset, reports, plans)
    assert result['truth_files_verified'] == 36
    assert result['metrics']['hybrid_selective']['failures'] == 72
    assert result['metrics']['hybrid_selective']['category_macro_f1'] == 0
    assert result['research_claim_supported'] is False


def test_wrapper_default_has_no_truth_or_model_access_and_checks_files(tmp_path, monkeypatch):
    from scripts import run_root_cause_v5 as wrapper
    dataset, _, plans = paired_fixture(tmp_path)
    plan = plans['selective'] | {'report_path': '.local/reports/offline-test.json', 'max_cny': '3.50'}
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan), encoding='utf-8')
    monkeypatch.setattr(wrapper, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'validate_plan', lambda *a: None)  # source digests are fixture-only
    read_bytes = type(plan_path).read_bytes

    def reject_truth(path):
        if path.parent.name == 'truth':
            raise AssertionError('No truth access during verification')
        return read_bytes(path)

    monkeypatch.setattr(type(plan_path), 'read_bytes', reject_truth)
    assert wrapper.main(['--dataset', str(dataset), '--plan', str(plan_path)]) == 0
    observed = next((dataset / 'observed').iterdir())
    # Pick a selected sealed record so the verification must catch the change.
    observed = dataset / 'observed' / f"{plan['selected_case_ids'][0]}.json"
    observed.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='observable file'):
        wrapper.main(['--dataset', str(dataset), '--plan', str(plan_path)])


def test_wrapper_execution_passes_explicit_env_file_without_blank_key_shadow(tmp_path, monkeypatch):
    from scripts import run_root_cause_v5 as wrapper
    from benchmarks.run_agents import _model_configuration
    dataset, _, plans = paired_fixture(tmp_path)
    plan = plans['selective'] | {'report_path': '.local/reports/offline-test.json', 'max_cny': '3.50'}
    plan_path, env_path = tmp_path / 'plan.json', tmp_path / 'fake.env'
    plan_path.write_text(json.dumps(plan), encoding='utf-8')
    env_path.write_text('DEEPSEEK_API_KEY=fictional-test-only\n', encoding='utf-8')
    monkeypatch.setattr(wrapper, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'validate_plan', lambda *a: None)

    async def offline_run(options):
        assert _model_configuration(options.env_file)['DEEPSEEK_API_KEY'] == 'fictional-test-only'
        assert options.report.is_relative_to(tmp_path)
        return {'status': 'completed', 'paid_requests': 0}

    monkeypatch.setattr(runner, 'run', offline_run)
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    assert wrapper.main(['--dataset', str(dataset), '--plan', str(plan_path), '--execute', '--env-file', str(env_path)]) == 0


@pytest.mark.asyncio
async def test_sealed_runner_defers_all_truth_reads_until_paired_analysis(tmp_path, monkeypatch):
    dataset = tmp_path / 'ds'
    v5.write_dataset(dataset)
    read_bytes = type(dataset).read_bytes

    def reject_truth(path):
        if path.parent.name == 'truth':
            raise AssertionError('A sealed batch must not open any truth file')
        return read_bytes(path)

    monkeypatch.setattr(type(dataset), 'read_bytes', reject_truth)
    monkeypatch.setattr(runner, '_model_configuration', lambda _: {
        'DEEPSEEK_API_KEY': '', 'DEEPSEEK_BASE_URL': '', 'DEEPSEEK_MODEL': ''})
    options = argparse.Namespace(dataset=dataset, split='sealed_test', per_family=4, modes=list(BASELINES),
                                 repeat=2, max_paid_requests=0, env_file=None, report=tmp_path / 'r.json')
    result = await runner.run(options)
    assert result['scoring_deferred'] and 'metrics' not in result
    assert len(result['cases']) == 216 and result['paid_requests'] == 0


def test_legacy_cli_can_print_deferred_v5_report_without_false_crash(tmp_path, monkeypatch, capsys):
    async def offline_run(options):
        return {'status': 'completed', 'paid_requests': 0, 'scoring_deferred': True}

    monkeypatch.setattr(runner, 'run', offline_run)
    assert runner.main(['--report', str(tmp_path / 'report.json')]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['scoring_deferred'] and value['metrics'] == {}
