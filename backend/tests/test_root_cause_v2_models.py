"""Two-round root-cause runner with a mock provider; no paid requests."""
import json

import httpx
import pytest

from app.context_transport import deserialize_context
from app.model_gateway import DeepSeekGateway
from benchmarks.root_cause_v2 import expected, score
from benchmarks.root_cause_v2_models import run_model_case
from datasets.generate_v2 import CATEGORIES, build_case


def reply(payload):
    return httpx.Response(200, json={'model': 'offline', 'usage': {'total_tokens': 10}, 'choices': [{
        'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(payload)}}]})


@pytest.mark.asyncio
async def test_request_releases_withheld_evidence_into_a_fresh_second_round():
    observed, withheld, truth = build_case('gap_order_reduced', 'validation', 0)
    briefs = []

    def handler(request):
        brief = deserialize_context(json.loads(request.content)['messages'][1]['content'])
        briefs.append(brief)
        if len(briefs) == 1:
            assert [c['rule_id'] for c in brief['verified_claims']] == ['RECEIPT_GAP']
            return reply({'status': 'insufficient', 'cause_assessment': {
                'categories': [], 'explanation': '请求订单变更资料。', 'evidence_ids': [],
                'evidence_request': {'kind': 'order'}}})
        assert brief['finding']['metrics']['evidence_request_allowed'] is False
        assert '取得1条' in brief['finding']['explanation']
        assert brief['finding']['metrics']['released_records'] == withheld  # the new evidence is shown, not hidden
        return reply({'status': 'normal', 'cause_assessment': {
            'categories': ['order_change'], 'explanation': '已取得订单减少记录。',
            'evidence_ids': [withheld[0]['evidence_id']]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_model_case(observed, withheld, CATEGORIES, 'single',
                                      DeepSeekGateway('offline-key', client=client))
    assert len(result['rounds']) == 2 and len(briefs) == 2
    assert result['released'] == withheld
    assert result['prediction'] == {'categories': ['order_change'], 'conclusion': 'normal', 'request': {'kind': 'order', 'reference': None}}
    assert [r['request_index'] for r in result['model_requests']] == [1, 2]  # renumbered for cost accounting
    assert expected(truth, result['released'])['conclusion'] == 'normal'
    metrics = score([{'family_id': 'f', 'truth': truth, **result}], CATEGORIES)
    assert metrics['conclusion_accuracy'] == 1.0 and metrics['decisive_evidence_obtained'] == '1/1'


@pytest.mark.asyncio
async def test_no_request_means_one_round_and_a_failed_round_is_an_error():
    observed, withheld, _ = build_case('normal_benign_text', 'validation', 1)

    def answer(request):
        body = json.loads(request.content)
        brief = deserialize_context(body['messages'][1]['content'])
        if not brief['shared_evidence']['query_results']:
            return httpx.Response(200, json={'model': 'offline', 'usage': {'total_tokens': 10}, 'choices': [{
                'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                    'reasoning_content': '', 'tool_calls': [{'id': 'read-1', 'type': 'function',
                        'function': {'name': 'get_business_chain', 'arguments': '{"order_id":"PO-001"}'}}]}}]})
        return reply({'status': 'normal', 'cause_assessment': {
            'categories': ['normal_business'], 'explanation': '已读取正常交易链。',
            'evidence_ids': [observed['records'][0]['evidence_id']]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        result = await run_model_case(observed, withheld, CATEGORIES, 'single',
                                      DeepSeekGateway('offline-key', client=client))
    assert len(result['rounds']) == 1 and result['released'] == []
    assert result['prediction']['categories'] == ['normal_business']

    def broken(request):
        return reply({'status': 'normal', 'cause_assessment': {'categories': ['invented']}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        failed = await run_model_case(observed, withheld, CATEGORIES, 'single',
                                      DeepSeekGateway('offline-key', client=client))
    assert failed['prediction']['error']['code'] == 'model_output'


@pytest.mark.asyncio
async def test_runner_records_validation_codes_without_model_text(tmp_path, monkeypatch):
    import argparse
    from benchmarks import root_cause_v2_models as module
    from datasets.generate_v3 import write_dataset
    write_dataset(tmp_path / 'ds')

    async def failing(observed, withheld, categories, mode, gateway):
        # Simulate actual dispatch accounting; fabricated result usage must not consume the cap.
        for _ in range(2):
            if gateway.budget.used < gateway.budget.maximum:
                await gateway.budget.before_attempt(gateway.sample_id, {
                    'event': 'model_attempt', 'context_bytes': 1000, 'response_token_limit': 8192})
        return {'prediction': {'error': {'code': 'model_output'}}, 'released': [], 'model_requests': [],
                'rounds': [{'usage': {'calls': 2, 'tokens': 9}, 'steps': [{'event': 'output_validation_failed',
                    'role': 'single', 'diagnostics': [{'code': 'unknown_cause_category', 'path': ['cause_assessment'],
                                                       'secret_text': 'never copied'}]}]}]}

    monkeypatch.setattr(module, 'run_model_case', failing)
    monkeypatch.setattr(module, '_model_configuration',
                        lambda _: {'DEEPSEEK_API_KEY': 'offline', 'DEEPSEEK_BASE_URL': '', 'DEEPSEEK_MODEL': ''})
    options = argparse.Namespace(dataset=tmp_path / 'ds', split='development', per_family=1, modes=['single'],
                                 repeat=1, max_paid_requests=5, env_file=None, report=tmp_path / 'r.json')
    report = await module.run(options)
    rows = report['cases']
    assert rows[0]['validation_failures'] == [{'round': 1, 'role': 'single',
        'codes': [{'code': 'unknown_cause_category', 'path': ['cause_assessment']}]}]
    assert 'never copied' not in (tmp_path / 'r.json').read_text(encoding='utf-8')
    assert any(row['prediction']['error']['code'] == 'batch_budget_exhausted' for row in rows)  # cap enforced
    assert report['metrics']['single']['failures'] == len(rows)


@pytest.mark.asyncio
async def test_global_request_cap_stops_inside_a_case_and_counts_failed_attempts(tmp_path, monkeypatch):
    import argparse
    from benchmarks import root_cause_v2_models as module
    from datasets.generate_v3 import write_dataset
    write_dataset(tmp_path / 'ds')
    calls = []

    def broken(request):
        calls.append(request)
        return reply({'status': 'normal'})  # Forces a repair in the same case.

    options = argparse.Namespace(dataset=tmp_path / 'ds', split='development', per_family=1, modes=['single'],
                                 repeat=1, max_paid_requests=1, env_file=None, report=tmp_path / 'r.json')
    monkeypatch.setattr(module, '_model_configuration',
                        lambda _: {'DEEPSEEK_API_KEY': 'offline', 'DEEPSEEK_BASE_URL': '', 'DEEPSEEK_MODEL': ''})
    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        gateway = DeepSeekGateway('offline-key', client=client)
        monkeypatch.setattr(module, 'DeepSeekGateway', lambda *a, **k: gateway)
        report = await module.run(options)
    assert len(calls) == report['paid_requests'] == 1
    assert report['cases'][0]['prediction']['error']['code'] == 'budget_exhausted'
    assert len(report['cases'][0]['request_not_dispatched']) == 1
    assert all(r['prediction']['error']['code'] == 'batch_budget_exhausted' for r in report['cases'][1:])
    with pytest.raises(ValueError, match='overwrite'):
        await module.run(options)
