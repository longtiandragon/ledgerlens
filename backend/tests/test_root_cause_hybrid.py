"""Pre-registered deterministic-first hybrid (root-cause v4) with a mock provider; no paid requests."""
import argparse
import json

import httpx
import pytest

from app.context_transport import deserialize_context
from app.model_gateway import DeepSeekGateway
from benchmarks import root_cause_v2_models as module
from benchmarks.root_cause_v2_models import hybrid_route, run_hybrid_model
from datasets import generate_v4 as v4
from datasets.generate_v2 import CATEGORIES

# Expected routes derived by hand from the generator semantics (datasets/generate_v2.py):
# duplicate signals and receipt gaps whose fixed receipt request returns a receipt are
# settled by the rules; gaps whose receipt request returns nothing, and every case
# without a rule signal, go to the model.
EXPECTED_ROUTES = {
    'dup_true': 'deterministic', 'dup_second_delivery': 'deterministic',
    'gap_late_document': 'deterministic', 'gap_in_transit': 'deterministic',
    'gap_order_reduced': 'model_after_request', 'gap_undeterminable': 'model_after_request',
    'normal_benign_text': 'model_no_signal', 'invoice_voided_text': 'model_no_signal',
    'price_adjustment_text': 'model_no_signal',
}


def reply(payload):
    return httpx.Response(200, json={'model': 'offline', 'usage': {'total_tokens': 10}, 'choices': [{
        'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(payload)}}]})


INSUFFICIENT = {'status': 'insufficient', 'cause_assessment': {
    'categories': [], 'explanation': '资料不足，保留判断。', 'evidence_ids': []}}


@pytest.mark.parametrize('cause, route', sorted(EXPECTED_ROUTES.items()))
def test_routing_uses_only_observable_rule_results(cause, route):
    observed, withheld, _ = v4.build_case(cause, 'development', 0)
    decided = hybrid_route(observed, withheld)
    assert decided['route'] == route
    if route == 'deterministic':
        assert decided['outcome']['categories'] and decided['outcome']['request']
    if route == 'model_after_request':
        assert decided['outcome']['request'] == {'kind': 'receipt'} and decided['outcome']['released'] == []
    if route == 'model_no_signal':
        assert decided['outcome']['request'] is None


@pytest.mark.asyncio
async def test_after_request_route_answers_once_without_a_second_request():
    observed, withheld, _ = v4.build_case('gap_order_reduced', 'development', 0)
    briefs = []

    def handler(request):
        briefs.append(deserialize_context(json.loads(request.content)['messages'][1]['content']))
        return reply(INSUFFICIENT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_hybrid_model(observed, withheld, CATEGORIES, hybrid_route(observed, withheld),
                                        DeepSeekGateway('offline-key', client=client))
    assert len(result['rounds']) == 1 and len(briefs) == 1
    finding = briefs[0]['finding']
    assert finding['metrics']['evidence_request_allowed'] is False
    assert finding['metrics']['released_records'] == []
    assert '程序规则按固定方式提出的补证请求（receipt）' in finding['explanation']
    assert '没有符合的快照外记录' in finding['explanation']
    # The spent request is the program's, so the case still used exactly one.
    assert result['prediction'] == {'categories': [], 'conclusion': 'insufficient', 'request': {'kind': 'receipt'}}


@pytest.mark.asyncio
async def test_deterministic_route_never_reaches_the_model():
    observed, withheld, _ = v4.build_case('gap_late_document', 'development', 0)
    with pytest.raises(ValueError, match='needs no model'):
        await run_hybrid_model(observed, withheld, CATEGORIES, hybrid_route(observed, withheld), gateway=None)


def _options(tmp_path, dataset, split='development', modes=('hybrid',), report='r.json'):
    return argparse.Namespace(dataset=dataset, split=split, per_family=1, modes=list(modes), repeat=1,
                              max_paid_requests=60, env_file=None, report=tmp_path / report)


def _offline_config(monkeypatch):
    monkeypatch.setattr(module, '_model_configuration',
                        lambda _: {'DEEPSEEK_API_KEY': 'offline', 'DEEPSEEK_BASE_URL': '', 'DEEPSEEK_MODEL': ''})


@pytest.mark.asyncio
async def test_runner_routes_spend_model_requests_only_where_needed(tmp_path, monkeypatch):
    v4.write_dataset(tmp_path / 'ds')
    calls = []

    def handler(request):
        calls.append(request)
        return reply(INSUFFICIENT)

    _offline_config(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DeepSeekGateway('offline-key', client=client)
        monkeypatch.setattr(module, 'DeepSeekGateway', lambda *a, **k: gateway)
        report = await module.run(_options(tmp_path, tmp_path / 'ds'))
    rows = report['cases']
    assert report['hybrid_routes'] == {'deterministic': 4, 'model_after_request': 2, 'model_no_signal': 3}
    for row in rows:
        if row['route'] == 'deterministic':
            assert row['paid_requests'] == 0 and row['model_requests'] == [] and not row['error']
        else:
            assert row['paid_requests'] >= 1 and row['rounds'] == 1
        # One evidence request at most per case, whoever made it: after the program's
        # receipt request the model gets no second request; without a signal the model
        # keeps its own single request (this mock makes none).
        if row['route'] == 'model_after_request':
            assert row['prediction']['request'] == {'kind': 'receipt'} and row['released'] == []
        if row['route'] == 'model_no_signal':
            assert row['prediction'].get('request') is None and row['released'] == []
    assert len(calls) == report['paid_requests'] == sum(r['paid_requests'] for r in rows)
    assert report['metrics']['hybrid']['samples'] == 9


@pytest.mark.asyncio
async def test_model_failures_stay_failures_and_rules_answers_are_unaffected(tmp_path, monkeypatch):
    v4.write_dataset(tmp_path / 'ds')

    def broken(request):
        return reply({'status': 'normal', 'cause_assessment': {'categories': ['invented']}})

    _offline_config(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        gateway = DeepSeekGateway('offline-key', client=client)
        monkeypatch.setattr(module, 'DeepSeekGateway', lambda *a, **k: gateway)
        report = await module.run(_options(tmp_path, tmp_path / 'ds'))
    by_route = {}
    for row in report['cases']:
        by_route.setdefault(row['route'], []).append(row)
    assert all(not row['error'] for row in by_route['deterministic'])
    assert all(row['error'] for route in ('model_after_request', 'model_no_signal') for row in by_route[route])
    assert report['metrics']['hybrid']['failures'] == 5
    assert report['status'] == 'completed_with_failures'


@pytest.mark.asyncio
async def test_hybrid_refuses_the_used_v3_sealed_split(tmp_path, monkeypatch):
    from datasets import generate_v3
    generate_v3.write_dataset(tmp_path / 'v3')
    _offline_config(monkeypatch)
    with pytest.raises(ValueError, match='v4 sealed split'):
        await module.run(_options(tmp_path, tmp_path / 'v3', split='sealed_test'))
