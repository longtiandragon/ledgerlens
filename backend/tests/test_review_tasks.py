"""Review requests must not acquire an abnormal premise or detector labels."""
import json
from copy import deepcopy

import httpx
import pytest
from app.context_transport import deserialize_context
from app.investigation import ToolContext, investigate, proposal_schema, tool_schemas, validate_proposal, ProposalRejected
from app.model_gateway import DeepSeekGateway, RunBudget
from benchmarks.run_agents import neutral_finding


def scope():
    rows = [{'id': 'line-a', 'evidence_id': 'cell-a', 'kind': 'journal', 'voucher_id': 'voucher-1',
             'account': '1001', 'debit': '10.00', 'credit': '0.00', 'currency': 'CNY',
             'business_date': '2026-09-01', 'period': '2026-09', 'observed_at': '2026-09-02T00:00:00Z'},
            {'id': 'line-b', 'evidence_id': 'cell-b', 'kind': 'journal', 'voucher_id': 'voucher-1',
             'account': '2001', 'debit': '0.00', 'credit': '10.00', 'currency': 'CNY',
             'business_date': '2026-09-01', 'period': '2026-09', 'observed_at': '2026-09-02T00:00:00Z'}]
    policy = {'framework': 'CAS', 'accounts': ['1001', '2001'], 'period': '2026-09', 'version': 'example'}
    return rows, policy


@pytest.mark.asyncio
@pytest.mark.parametrize('anomaly_present', [False, True])
async def test_observed_review_does_not_predeclare_anomaly_or_hide_answer_in_context(anomaly_present):
    rows, policy = scope()
    if anomaly_present:
        rows[1]['credit'] = '9.00'
    task = neutral_finding(rows)
    original = deepcopy((rows, policy, task))
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        visible = deserialize_context(body['messages'][1]['content'])
        assert visible['finding']['task_type'] == 'review'
        assert visible['verified_claims'] == [] and visible['missing_evidence'] == []
        assert all('当前规则发现数据差异' not in h['explanation'] for h in visible['unresolved_hypotheses'])
        assert '当前快照无法独立复现该异常' not in body['messages'][1]['content']
        return httpx.Response(200, json={'model': 'example', 'usage': {'total_tokens': 10}, 'choices': [{
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({
                'status': 'normal', 'claims': [], 'hypotheses': [], 'missing_evidence': []})}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(rows, policy, task, mode='single', gateway=DeepSeekGateway('fixture-api-key', client=client))
    assert len(bodies) == 1 and result['proposals'][0]['status'] == 'normal'
    assert result['claims'] == [] and result['missing_evidence'] == []
    assert (rows, policy, task) == original
    # A model's declaration is still only a proposal, even in a normal task.
    assert result['fact_status'] == 'insufficient'


@pytest.mark.parametrize('allow_plan', [False, True])
def test_generated_output_and_tools_limit_rule_ids_to_fixed_framework(allow_plan):
    rows, policy = scope()
    context = ToolContext(rows, policy, neutral_finding(rows), RunBudget())
    schema = proposal_schema(context, allow_plan=allow_plan)
    assert set(schema['$defs']['Claim']['properties']['rule_id']['enum']) == context.rule_ids
    tools = {s['function']['name']: s['function'] for s in tool_schemas(context)}
    assert set(tools['verify_claims']['parameters']['$defs']['Claim']['properties']['rule_id']['enum']) == context.rule_ids
    assert set(tools['retrieve_policy']['parameters']['properties']['rule_id']['anyOf'][0]['enum']) == context.rule_ids
    assert 'BENCHMARK_SCOPE' not in context.rule_ids
    invalid = {'status': 'supported', 'claims': [{'text': 'Invented rule', 'rule_id': 'INVENTED',
              'evidence_ids': ['cell-a'], 'calculation': {}}], 'hypotheses': [], 'missing_evidence': []}
    with pytest.raises(ProposalRejected) as error:
        validate_proposal(json.dumps(invalid), context, allow_plan=allow_plan)
    assert {'code': 'unknown_rule', 'path': ['claims', 0, 'rule_id']} in error.value.diagnostics


def test_unknown_task_type_is_rejected_before_model_dispatch():
    rows, policy = scope()
    with pytest.raises(ValueError, match='task type'):
        ToolContext(rows, policy, {**neutral_finding(rows), 'task_type': 'post_to_ledger'}, RunBudget())
