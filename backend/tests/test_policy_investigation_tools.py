"""Independent saved-policy reads through the actual tool/memory/graph boundary.

The two ledger amounts, ten-yuan difference and inventory outcome are handwritten.
No production accountant generates an expected answer; MockTransport never calls
a provider and the tests never open the application database.
"""
import hashlib
import json
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from app.context_transport import deserialize_context
from app.evidence_planning import PlanningRejected, plan_evidence_query
from app.investigation import (
    EvidenceMemory, PolicyInterpretation, ProposalRejected, SavedPolicyQuery, ToolContext,
    investigate, planning_tools, proposal_schema, tool_schemas, validate_proposal,
)
from app.model_gateway import DeepSeekGateway, RunBudget
from policy_context_fixtures import checksum, frozen_payload


def multiple_payload(count=7):
    payload = frozen_payload()
    template = payload['checks'][0]
    payload['checks'], payload['observation_manifest'] = [], []
    for index in range(count):
        check = deepcopy(template)
        observation_id = f'context-annotation-{index:02}'
        check['id'], check['observation_id'] = f'context-check-{index:02}', observation_id
        binding = check['binding']
        observation = binding['observation']
        observation['observation_id'] = observation_id
        annotation = binding['annotation_envelope']
        annotation.update(id=observation_id, logical_key=f'handwritten-resale-{index:02}', payload=deepcopy(observation))
        check['observation_digest'] = checksum(annotation)
        binding.update(observation_id=observation_id, observation_digest=check['observation_digest'])
        check['artifact_digest'] = checksum({key: value for key, value in check.items() if key != 'artifact_digest'} |
            {'book_id': payload['book_id'], 'snapshot_id': payload['snapshot_id']})
        payload['checks'].append(check)
        payload['observation_manifest'].append({'id': observation_id, 'digest': check['observation_digest'],
            'logical_key': annotation['logical_key'], 'version': 1})
    return payload


@pytest.fixture
def scope():
    frozen = multiple_payload()
    policy = {'framework': 'CAS', 'version': 'tool-fixture-v1', 'book_id': 'context-book',
        'snapshot_id': 'context-snapshot', 'snapshot_digest': 'e' * 64, 'period': '2026-09',
        'accounts': ['1403', '2202'], 'snapshot_cutoff': '2026-10-02T00:00:00+00:00',
        'investigation_policy_context': frozen}
    common = {'book_id': 'context-book', 'currency': 'CNY', 'kind': 'journal', 'voucher_id': 'MANUAL-1',
        'business_date': '2026-09-01', 'period': '2026-09', 'observed_at': '2026-10-01T00:00:00+00:00'}
    rows = [{**common, 'id': 'context-record', 'evidence_id': 'context-evidence', 'source_id': 'context-source',
        'account': '1403', 'debit': '100.00', 'credit': '0.00', 'description': '测试原件：购进待售商品。'},
        {**common, 'id': 'second-record', 'evidence_id': 'second-evidence', 'source_id': 'second-source',
        'account': '2202', 'debit': '0.00', 'credit': '90.00', 'description': '测试原件：记账金额待复核。'}]
    finding = {'rule_id': 'VOUCHER_BALANCE', 'record_ids': ['context-record', 'second-record'],
        'evidence_ids': ['context-evidence', 'second-evidence'], 'status': 'supported',
        'metrics': {'difference': '10.00'}, 'missing_evidence': []}
    return rows, policy, finding


def context(scope):
    result = ToolContext(*deepcopy(scope), budget=RunBudget())
    result.verified_finding = deepcopy(scope[2])
    return result


def interpretation(ids=None):
    return {'status': 'normal', 'claims': [], 'hypotheses': [], 'missing_evidence': [],
        'policy_interpretations': [{'text': '人工确认条件下存货检查有依据；该解释仍待复核。',
            'policy_check_ids': ids or ['context-check-00']}]}


def completion(payload=None):
    return httpx.Response(200, json={'model': 'offline-policy-model', 'usage': {'total_tokens': 10},
        'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(
            payload if payload is not None else {'status': 'insufficient', 'claims': [],
                'hypotheses': [], 'missing_evidence': []})}}]})


def tool_completion(name, arguments):
    return httpx.Response(200, json={'model': 'offline-policy-model', 'usage': {'total_tokens': 10},
        'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'tool_calls': [{
            'id': 'offline-read', 'type': 'function', 'function': {'name': name,
                'arguments': json.dumps(arguments)}}]}}]})


def test_default_read_returns_six_full_checks_with_original_scoped_records(scope):
    original = deepcopy(scope)
    ctx = context(scope)
    data = ctx.execute('read_policy_checks', {})
    assert data['policy_check_ids'] == [f'context-check-{index:02}' for index in range(6)]
    assert data['policy_checks'] == scope[1]['investigation_policy_context']['checks'][:6]
    assert data['total'] == 7 and data['truncated'] is True and data['status'] == 'available'
    assert data['available_check_ids'] == [f'context-check-{index:02}' for index in range(7)]
    assert [row['evidence_id'] for row in data['records']] == ['context-evidence']
    assert data['records'][0]['debit'] == '100.00'
    assert data['records'][0]['description'] == '测试原件：购进待售商品。'
    assert 'book_id' not in data['records'][0]
    assert data['policy_checks'][0]['result']['expected_treatment'] == 'inventory'
    assert data['policy_checks'][0]['binding']['annotation_recorded_at'] > data['policy_checks'][0]['binding']['source_cutoff']
    assert ctx.budget.tools == 1 and scope == original


def test_explicit_saved_check_order_is_preserved_and_outputs_are_deep_copies(scope):
    ctx = context(scope)
    ids = ['context-check-06', 'context-check-00']
    data = ctx.execute('read_policy_checks', {'check_ids': ids})
    assert [item['id'] for item in data['policy_checks']] == ids
    data['policy_checks'][0]['binding']['observation']['conditions']['held_for_sale']['value'] = 'no'
    data['records'][0]['debit'] = '999.00'
    replay = ctx.execute('read_policy_checks', {'check_ids': ids})
    assert replay['policy_checks'][0]['binding']['observation']['conditions']['held_for_sale']['value'] == 'yes'
    assert replay['records'][0]['debit'] == '100.00'


@pytest.mark.parametrize('args', [
    {'check_ids': ['context-check-00', 'foreign-check']}, {'check_ids': ['context-check-00'] * 2},
    {'check_ids': [f'context-check-{index:02}' for index in range(7)]}, {'check_ids': 'context-check-00'},
    {'check_ids': [1]}, {'check_ids': [[]]}, {'book_id': 'other'}, {'sql': 'SELECT *'},
])
def test_saved_check_read_rejects_whole_invalid_batch(scope, args):
    ctx = context(scope)
    before = deepcopy((ctx.records, ctx.policy_checks))
    with pytest.raises(ValueError):
        ctx.execute('read_policy_checks', args)
    assert (ctx.records, ctx.policy_checks) == before


@pytest.mark.parametrize('mode', ['legacy', 'captured_empty'])
def test_no_saved_checks_is_explicit_without_inventing_a_policy_outcome(scope, mode):
    rows, policy, finding = deepcopy(scope)
    if mode == 'legacy':
        policy.pop('investigation_policy_context')
    else:
        policy['investigation_policy_context']['checks'] = []
    data = ToolContext(rows, policy, finding, RunBudget()).execute('read_policy_checks', {})
    assert data['status'] == 'not_provided'
    assert data['policy_checks'] == data['records'] == data['available_check_ids'] == []
    assert data['total'] == 0 and data['truncated'] is False
    assert data['policy_check_context']['provided'] is (mode == 'captured_empty')
    if mode == 'captured_empty':
        assert data['policy_check_context']['observation_count'] == 7  # latest uncomputed assertions remain visible as a count


@pytest.mark.parametrize('field,value', [
    ('book_id', 'other-book'), ('snapshot_id', 'other-snapshot'), ('snapshot_digest', 'f' * 64),
    ('framework', 'SCAS'), ('period', '2026-08'), ('snapshot_cutoff', '2026-10-01T23:00:00Z'),
    ('snapshot_id', None), ('snapshot_digest', None),
])
def test_capture_must_agree_with_actual_tool_snapshot_scope(scope, field, value):
    rows, policy, finding = deepcopy(scope)
    policy[field] = value
    with pytest.raises(ValueError):
        ToolContext(rows, policy, finding, RunBudget())


def test_snapshot_membership_and_saved_artifact_tampering_fail_before_tools(scope):
    rows, policy, finding = deepcopy(scope)
    policy['investigation_policy_context']['checks'][0]['result']['reason'] = 'tampered free text'
    with pytest.raises(ValueError, match='binding'):
        ToolContext(rows, policy, finding, RunBudget())
    rows, policy, finding = deepcopy(scope)
    rows[0]['evidence_id'] = 'different-evidence'
    with pytest.raises(ValueError, match='fixed snapshot'):
        ToolContext(rows, policy, finding, RunBudget())


def test_role_read_memory_preserves_full_checks_without_conferring_index_access(scope):
    ctx, memory = context(scope), EvidenceMemory()
    memory.bind(ctx)
    initial = memory.brief(set())
    assert initial['policy_checks'] == [] and len(initial['policy_check_index']) == 7
    output = memory.execute(ctx, 'read_policy_checks', {'check_ids': ['context-check-00']})
    key = output['query_id']
    stored = memory.stored()
    first_role = memory.brief({key})
    assert first_role['policy_checks'] == [scope[1]['investigation_policy_context']['checks'][0]]
    assert memory.policy_content_ids({key}) == {'context-check-00'}
    other_role = memory.brief(set())
    assert other_role['policy_checks'] == other_role['records'] == []
    assert other_role['query_manifest'][0]['metadata']['policy_check_ids'] == ['context-check-00']
    assert other_role['query_manifest'][0]['payload_in_view'] is False
    assert memory.policy_content_ids(set()) == set()
    read = memory.execute(ctx, 'read_evidence_queries', {'query_ids': [key]})
    assert read['evidence']['policy_checks'] == first_role['policy_checks']
    assert read['evidence']['records'] == first_role['records']
    assert memory.stored() == stored  # bounded historical read does not fabricate another query
    replay = memory.execute(ctx, 'read_policy_checks', {'check_ids': ['context-check-00']})
    assert replay['cache_hit'] is True and replay['query_id'] == key
    assert ctx.budget.tools == 0


def test_cached_policy_query_hash_binds_the_complete_captured_content(scope):
    ctx, memory = context(scope), EvidenceMemory()
    key = memory.execute(ctx, 'read_policy_checks', {'check_ids': ['context-check-00']})['query_id']
    stored = memory.stored()
    envelope = {'query': stored['queries'][key], 'records': stored['records'], 'reference_passages': {},
        'policy_checks': stored['policy_checks']}
    expected = hashlib.sha256(json.dumps(envelope, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    assert memory.query_hash(key) == expected
    memory.policy_checks['context-check-00']['result']['reason'] = 'altered content'
    assert memory.query_hash(key) != expected
    with pytest.raises(ValueError, match='frozen run capture'):
        memory.read_queries(ctx, [key])


@pytest.mark.parametrize('tamper', ['pool_missing', 'query_ids', 'scope'])
def test_restored_policy_memory_refuses_lost_or_foreign_check_payloads(scope, tamper):
    ctx, memory = context(scope), EvidenceMemory()
    key = memory.execute(ctx, 'read_policy_checks', {'check_ids': ['context-check-00']})['query_id']
    stored = memory.stored()
    if tamper == 'pool_missing':
        stored['policy_checks'] = {}
    elif tamper == 'query_ids':
        stored['queries'][key]['result']['policy_check_ids'] = ['context-check-01']
    else:
        stored['scope'] = 'different-run'
    with pytest.raises(ValueError):
        EvidenceMemory(stored).read_queries(ctx, [key])


def test_previous_role_opinion_citations_never_count_as_current_full_policy_read(scope):
    ctx, memory = context(scope), EvidenceMemory()
    ctx.prior_proposals = [{**interpretation(), 'role': 'ledger'}]
    prior = memory.execute(ctx, 'read_prior_proposal', {'index': 0})
    assert memory.policy_content_ids({prior['query_id']}) == set()
    with pytest.raises(ProposalRejected) as failure:
        validate_proposal(json.dumps(interpretation()), ctx,
            read_policy_check_ids=memory.policy_content_ids({prior['query_id']}))
    assert failure.value.diagnostics == [{'code': 'policy_check_content_not_read',
        'path': ['policy_interpretations', 0, 'policy_check_ids']}]


def test_interpretations_require_actual_read_and_valid_captured_ids(scope):
    ctx = context(scope)
    with pytest.raises(ProposalRejected) as missing:
        validate_proposal(json.dumps(interpretation()), ctx)
    assert missing.value.diagnostics[0]['code'] == 'policy_check_content_not_read'
    with pytest.raises(ProposalRejected) as foreign:
        validate_proposal(json.dumps(interpretation(['foreign-check'])), ctx, read_policy_check_ids={'foreign-check'})
    assert foreign.value.diagnostics[0]['code'] == 'policy_check_outside_run'
    valid = validate_proposal(json.dumps(interpretation()), ctx, read_policy_check_ids={'context-check-00'})
    assert valid.policy_interpretations[0].policy_check_ids == ['context-check-00']


@pytest.mark.parametrize('item', [
    {'text': '', 'policy_check_ids': ['context-check-00']},
    {'text': 'x' * 2001, 'policy_check_ids': ['context-check-00']},
    {'text': 'x', 'policy_check_ids': []}, {'text': 'x', 'policy_check_ids': ['context-check-00'] * 2},
    {'text': 'x', 'policy_check_ids': [str(index) for index in range(7)]},
    {'text': 'x', 'policy_check_ids': ['context-check-00'], 'calculation': {'difference': '0.00'}},
    {'text': 'x', 'policy_check_ids': ['context-check-00'], 'status': 'supported'},
    {'text': 'x', 'policy_check_ids': ['context-check-00'], 'rule_id': 'POLICY_CAS_RESALE_INVENTORY'},
])
def test_interpretations_have_a_separate_strict_bounded_contract(item):
    with pytest.raises(ValidationError):
        PolicyInterpretation.model_validate(item)


def test_tool_and_proposal_schemas_separate_saved_check_ids_from_core_rules(scope):
    ctx = context(scope)
    schema = proposal_schema(ctx, allow_plan=False)
    ids = schema['$defs']['PolicyInterpretation']['properties']['policy_check_ids']['items']['enum']
    assert ids == [f'context-check-{index:02}' for index in range(7)]
    core_ids = schema['$defs']['Claim']['properties']['rule_id']['enum']
    assert 'VOUCHER_BALANCE' in core_ids and 'POLICY_CAS_RESALE_INVENTORY' not in core_ids
    read = next(tool['function'] for tool in tool_schemas(ctx) if tool['function']['name'] == 'read_policy_checks')
    assert read['parameters']['properties']['check_ids']['items']['enum'] == ids
    bad = {'text': '不能把政策结果转为核心规则', 'evidence_ids': ['context-evidence'],
        'rule_id': 'POLICY_CAS_RESALE_INVENTORY', 'calculation': {}}
    assert ctx.execute('verify_claims', {'claims': [bad]})['claims'][0]['rule_known'] is False
    with pytest.raises(ProposalRejected) as failure:
        validate_proposal(json.dumps({**interpretation(), 'claims': [bad]}), ctx,
            read_policy_check_ids={'context-check-00'})
    assert {'code': 'unknown_rule', 'path': ['claims', 0, 'rule_id']} in failure.value.diagnostics


def test_active_planning_cost_and_availability_are_server_owned(scope):
    ctx, memory = context(scope), EvidenceMemory()
    binding = planning_tools(ctx, memory)['read_policy_checks']
    assert binding.cost_units == 1
    assert binding.availability(SavedPolicyQuery()) == 'available'
    assert binding.availability(SavedPolicyQuery(check_ids=['foreign-check'])) == 'unavailable'
    payload = {'hypotheses': [{'hypothesis_id': 'h1', 'explanation': '原条件支持存货'},
        {'hypothesis_id': 'h2', 'explanation': '原条件不足'}], 'queries': [{'query_id': 'hand-query',
        'tool': 'read_policy_checks', 'arguments': {'check_ids': ['context-check-00']},
        'predictions': {'h1': {'status': 'predicted', 'label': 'A'}, 'h2': {'status': 'predicted', 'label': 'B'}}}]}
    compiled = plan_evidence_query(payload, tools=planning_tools(ctx, memory))
    assert compiled.decision.selected_query.tool == 'read_policy_checks'
    assert compiled.decision.selected_query.cost_units == 1
    assert compiled.decision.execution_performed is False and memory.queries == {}
    payload['queries'][0]['cost_units'] = 0
    with pytest.raises(PlanningRejected):
        plan_evidence_query(payload, tools=planning_tools(ctx, memory))


@pytest.mark.asyncio
async def test_single_model_reads_saved_check_then_keeps_interpretation_outside_facts(scope):
    before, requests = deepcopy(scope), []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body['messages'][1]['content'])
        if len(requests) == 1:
            assert brief['shared_evidence']['policy_checks'] == []
            assert len(brief['shared_evidence']['policy_check_index']) == 7
            assert 'expected_treatment' not in json.dumps(brief)
            return tool_completion('read_policy_checks', {'check_ids': ['context-check-00']})
        assert brief['shared_evidence']['policy_checks'] == [scope[1]['investigation_policy_context']['checks'][0]]
        assert brief['shared_evidence']['records'][0]['debit'] == '100.00'
        return completion(interpretation())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scope, mode='single', gateway=DeepSeekGateway('offline-key', client=client))
    assert result['error'] is None and len(requests) == 2 and result['usage']['tools'] == 1
    assert result['status'] == result['fact_status'] == 'supported'
    assert result['claims'][0]['calculation']['difference'] == '10.00'
    assert result['proposed_claims'] == []
    assert result['policy_interpretations'] == [{**interpretation()['policy_interpretations'][0],
        'origin': 'model_proposal', 'semantic_status': 'unverified_model_interpretation', 'role': 'single'}]
    assert result['policy_check_context']['captured_at'] == '2026-10-05T00:00:00+00:00'
    assert result['policy_check_cache_count'] == 1 and scope == before


@pytest.mark.asyncio
async def test_manifest_only_model_citation_fails_once_repaired_without_changing_facts(scope):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        return completion(interpretation())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scope, mode='single', gateway=DeepSeekGateway('offline-key', client=client))
    assert len(attempts) == 2 and result['error']['code'] == 'model_output'
    assert result['policy_interpretations'] == [] and result['policy_check_cache_count'] == 0
    assert result['fact_status'] == 'supported' and result['claims'][0]['calculation']['difference'] == '10.00'
    assert all(step['diagnostics'] == [{'code': 'policy_check_content_not_read',
        'path': ['policy_interpretations', 0, 'policy_check_ids']}]
        for step in result['steps'] if step['event'] == 'output_validation_failed')


@pytest.mark.asyncio
async def test_multi_role_can_read_complete_saved_policy_cache_with_separate_read_authority(scope):
    requests, key = [], None

    def handler(request):
        nonlocal key
        body = json.loads(request.content)
        requests.append(body)
        evidence = deserialize_context(body['messages'][1]['content'])['shared_evidence']
        number = len(requests)
        if number == 1:
            return tool_completion('read_policy_checks', {'check_ids': ['context-check-00']})
        if number == 2:
            key = evidence['query_manifest'][0]['query_id']
            return completion(interpretation())
        if number == 3:
            assert evidence['policy_checks'] == [] and evidence['query_manifest'][0]['payload_in_view'] is False
            return tool_completion('read_evidence_queries', {'query_ids': [key]})
        if number == 4:
            assert evidence['policy_checks'] == [scope[1]['investigation_policy_context']['checks'][0]]
            return completion(interpretation())
        assert evidence['policy_checks'] == []
        return completion()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scope, mode='multi', gateway=DeepSeekGateway('offline-key', client=client))
    assert result['error'] is None and len(requests) == 7 and result['usage']['tools'] == 2
    assert [item['role'] for item in result['policy_interpretations']] == ['ledger', 'evidence']
    reads = {step['role']: step['read_policy_check_ids'] for step in result['steps'] if step['event'] == 'agent_completed'}
    assert reads == {'ledger': ['context-check-00'], 'evidence': ['context-check-00'], 'policy': [], 'critic': [], 'report': []}
    assert result['fact_status'] == 'supported' and result['claims'][0]['calculation']['difference'] == '10.00'


@pytest.mark.asyncio
async def test_active_selector_executes_saved_read_and_hands_full_content_to_policy_role(scope):
    requests = []
    plan = {'hypotheses': [{'hypothesis_id': 'h1', 'explanation': '已记录条件支持存货'},
        {'hypothesis_id': 'h2', 'explanation': '条件未确认'}], 'queries': [{'query_id': 'read-saved-policy',
            'tool': 'read_policy_checks', 'arguments': {'check_ids': ['context-check-00']},
            'predictions': {'h1': {'status': 'predicted', 'label': 'A'}, 'h2': {'status': 'predicted', 'label': 'B'}}}]}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body['messages'][1]['content'])
        if len(requests) == 1:
            assert brief['shared_evidence']['policy_checks'] == []
            return completion({'status': 'insufficient', 'claims': [], 'hypotheses': [],
                'missing_evidence': [], 'evidence_plan': plan})
        assert brief['shared_evidence']['policy_checks'] == [scope[1]['investigation_policy_context']['checks'][0]]
        return completion({**interpretation(), 'evidence_plan': None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scope, mode='active', gateway=DeepSeekGateway('offline-key', client=client))
    assert result['error'] is None and len(requests) == 3
    assert [item['role'] for item in result['proposals']] == ['coordinator', 'policy', 'critic']
    assert result['evidence_selections'][0]['selected_query']['tool'] == 'read_policy_checks'
    assert result['evidence_selections'][0]['execution']['policy_check_ids'] == ['context-check-00']
    assert result['evidence_selections'][0]['selected_query']['cost_units'] == 1
    assert result['usage']['tools'] == 1 and result['fact_status'] == 'supported'


@pytest.mark.parametrize('frozen', ['none', 'empty'])
def test_runs_without_saved_checks_expose_only_an_empty_interpretation_array(scope, frozen):
    """Live v20 failure: with zero checks the model invented interpretation IDs."""
    rows, policy, finding = deepcopy(scope)
    if frozen == 'none':
        policy['investigation_policy_context'] = None
    else:
        policy['investigation_policy_context']['checks'] = []
        policy['investigation_policy_context']['observation_manifest'] = []
    ctx = ToolContext(rows, policy, finding, budget=RunBudget())
    ctx.verified_finding = deepcopy(finding)
    assert ctx.policy_checks == {}
    schema = proposal_schema(ctx, allow_plan=False)
    assert schema['properties']['policy_interpretations'] == {'type': 'array', 'maxItems': 0}
    assert 'PolicyInterpretation' not in schema['$defs']
    assert 'PolicyInterpretation' not in json.dumps(schema)  # no dangling reference
    assert proposal_schema(ctx, allow_plan=True)['properties']['policy_interpretations']['maxItems'] == 0
    empty = {'status': 'insufficient', 'claims': [], 'hypotheses': [], 'missing_evidence': [],
             'policy_interpretations': []}
    assert validate_proposal(json.dumps(empty), ctx).policy_interpretations == []
    with pytest.raises(ProposalRejected) as invented:
        validate_proposal(json.dumps(interpretation(['invented-check'])), ctx)
    assert invented.value.diagnostics[0]['code'] == 'policy_check_outside_run'
