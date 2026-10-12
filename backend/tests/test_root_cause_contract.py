"""Root-cause task contract through the real proposal/graph boundary (offline).

Records and taxonomy are hand-written; MockTransport never calls a provider.
"""
import json
from copy import deepcopy

import httpx
import pytest

from app.context_transport import deserialize_context
from app.investigation import ProposalRejected, ToolContext, investigate, proposal_schema, validate_proposal
from app.model_gateway import DeepSeekGateway, RunBudget

TAXONOMY = {'late_document': '单据期后取得', 'goods_in_transit': '货物在途', 'order_change': '订单变更'}
COMMON = {'book_id': 'rc-book', 'currency': 'CNY', 'period': '2026-09', 'business_date': '2026-09-20',
          'observed_at': '2026-09-30T12:00:00+00:00', 'counterparty': '合成供应商甲', 'order_id': 'PO-001'}


def records():
    return [{**COMMON, 'id': 'o1', 'evidence_id': 'ev-o1', 'source_id': 'PO-001', 'reference': 'PO-001',
             'kind': 'order', 'amount': '100.00'},
            {**COMMON, 'id': 'r1', 'evidence_id': 'ev-r1', 'source_id': 'RCV-001', 'reference': 'RCV-001',
             'kind': 'receipt', 'amount': '60.00'}]


def policy():
    return {'framework': 'CAS', 'version': 'rc-fixture', 'period': '2026-09', 'currency': 'CNY',
            'accounts': ['1002', '1405', '2202'], 'snapshot_cutoff': '2026-09-30T23:59:59+00:00'}


def task(allowed=True):
    return {'task_type': 'root_cause', 'rule_id': 'ROOT_CAUSE_TASK', 'title': '原因调查', 'status': 'unsupported',
            'record_ids': ['o1', 'r1'], 'evidence_ids': ['ev-o1', 'ev-r1'], 'missing_evidence': [],
            'metrics': {'cause_taxonomy': TAXONOMY, 'evidence_request_allowed': allowed}}


def context(finding=None):
    return ToolContext(records(), policy(), finding or task(), budget=RunBudget())


def proposal(**cause):
    return json.dumps({'status': 'insufficient',
                       'cause_assessment': {'explanation': '当前资料不能区分原因。', 'evidence_ids': [], **cause}})


def test_schema_offers_taxonomy_enum_and_request_only_when_granted():
    schema = proposal_schema(context(), allow_plan=False)
    assert set(schema['properties']) == {'status', 'cause_assessment'}
    assert schema['required'] == ['status', 'cause_assessment']
    assert schema['$defs']['RootCauseAssessment']['properties']['categories']['items']['enum'] == sorted(TAXONOMY)
    assert schema['$defs']['RootCauseAssessment']['properties']['evidence_ids']['items']['enum'] == ['ev-o1', 'ev-r1']
    assert schema['$defs']['EvidenceAsk']['properties']['kind']['enum'] == ['order', 'receipt', 'invoice', 'bank', 'journal']
    closed = proposal_schema(context(task(allowed=False)), allow_plan=False)
    assert closed['$defs']['RootCauseAssessment']['properties']['evidence_request'] == {'type': 'null'}
    assert 'EvidenceAsk' not in json.dumps(closed)


def test_other_tasks_cannot_emit_a_cause_assessment():
    review = {**task(), 'task_type': 'review', 'metrics': {}}
    ctx = context(review)
    schema = proposal_schema(ctx, allow_plan=False)
    assert schema['properties']['cause_assessment'] == {'type': 'null'}
    assert 'CauseAssessment' not in json.dumps(schema)
    with pytest.raises(ProposalRejected) as failure:
        validate_proposal(proposal(categories=['late_document']), ctx)
    assert failure.value.diagnostics == [{'code': 'cause_assessment_outside_task', 'path': ['cause_assessment']}]


@pytest.mark.parametrize('cause,code', [
    ({'categories': ['invented_cause']}, 'unknown_cause_category'),
    ({'categories': ['late_document', 'late_document']}, 'unknown_cause_category'),
    ({'categories': [], 'evidence_request': {'kind': 'receipt', 'reference': 'PO-999'}}, 'reference_not_visible'),
])
def test_invalid_assessments_are_rejected_with_safe_codes(cause, code):
    with pytest.raises(ProposalRejected) as failure:
        validate_proposal(proposal(**cause), context())
    assert failure.value.diagnostics[0]['code'] == code


def test_request_is_refused_once_the_budget_is_used_and_accepted_when_granted():
    ask = {'categories': [], 'evidence_request': {'kind': 'receipt', 'reference': 'PO-001'}}
    assert validate_proposal(proposal(**ask), context()).cause_assessment.evidence_request.kind == 'receipt'
    with pytest.raises(ProposalRejected) as failure:
        validate_proposal(proposal(**ask), context(task(allowed=False)))
    assert failure.value.diagnostics[0]['code'] == 'evidence_request_not_allowed'
    with pytest.raises(ProposalRejected):  # free-text kinds are not part of the contract
        validate_proposal(proposal(categories=[], evidence_request={'kind': 'notice'}), context())


def test_root_cause_task_requires_a_taxonomy():
    with pytest.raises(ValueError):
        context({**task(), 'metrics': {}})


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,roles', [('single', 1), ('multi', 5), ('active', 3)])
async def test_rules_seed_the_task_and_the_final_assessment_reaches_the_result(mode, roles):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        brief = deserialize_context(body['messages'][1]['content'])
        seen.append(brief['verified_claims'])
        return httpx.Response(200, json={'model': 'offline', 'usage': {'total_tokens': 10}, 'choices': [{
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': proposal(
                categories=[], evidence_request={'kind': 'receipt', 'reference': 'PO-001'})}}]})

    rows, rules, finding = records(), policy(), task()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(rows, rules, finding, mode=mode,
                                   gateway=DeepSeekGateway('offline-key', client=client))
    assert result['error'] is None and result['fact_status'] == 'insufficient'
    assert len(result['proposals']) == roles
    assert [claim['rule_id'] for claim in seen[0]] == ['RECEIPT_GAP']      # deterministic result supplied first
    assert result['proposals'][-1]['cause_assessment'] == {
        'categories': [], 'evidence_request': {'kind': 'receipt', 'reference': 'PO-001'},
        'explanation': '当前资料不能区分原因。', 'evidence_ids': []}
    assert result['proposals'][-1]['cause_assessment_semantic_status'] == 'unverified_model_interpretation'
    assert rows == records() and rules == policy() and finding == task()
    assert deepcopy(result['claims'][0]['origin']) == 'deterministic_rule'


@pytest.mark.parametrize('payload,code', [
    ('{', 'invalid_json'),
    ('{"status":"normal","status":"insufficient"}', 'invalid_json'),
    (json.dumps({'status': 'normal'}), 'missing'),
    (json.dumps({'status': 'normal', 'cause_assessment': None}), 'model_type'),
    (json.dumps({'status': 'insufficient', 'cause_assessment': {
        'categories': [], 'explanation': '待查', 'evidence_ids': [], 'arbitrary_secret_key': 'secret'}}), 'extra_forbidden'),
    (json.dumps({'status': 'insufficient', 'claims': [], 'cause_assessment': {
        'categories': [], 'explanation': '待查', 'evidence_ids': []}}), 'extra_forbidden'),
])
def test_narrow_wire_contract_rejects_malformed_or_legacy_outputs(payload, code):
    with pytest.raises(ProposalRejected) as failure:
        validate_proposal(payload, context())
    assert failure.value.diagnostics[0]['code'] == code
    assert 'secret' not in json.dumps(failure.value.diagnostics)


@pytest.mark.parametrize('status,categories,citations,read,code', [
    ('normal', ['late_document'], ['outside'], {'outside'}, 'citation_outside_snapshot'),
    ('normal', ['late_document'], ['ev-r1'], set(), 'evidence_content_not_read'),
    ('normal', ['late_document'], [], {'ev-r1'}, 'evidence_content_not_read'),
    ('normal', ['late_document'], ['ev-r1', 'ev-r1'], {'ev-r1'}, 'duplicate_citation'),
    ('normal', [], ['ev-r1'], {'ev-r1'}, 'inconsistent_cause_conclusion'),
    ('insufficient', ['late_document'], [], set(), 'inconsistent_cause_conclusion'),
    ('unsupported', ['late_document'], [], set(), 'inconsistent_cause_conclusion'),
    ('insufficient', [], ['ev-r1'], set(), 'evidence_content_not_read'),
])
def test_narrow_citations_and_conclusion_reject_codes(status, categories, citations, read, code):
    payload = json.dumps({'status': status, 'cause_assessment': {
        'categories': categories, 'explanation': '手写说明', 'evidence_ids': citations}})
    with pytest.raises(ProposalRejected) as failure:
        validate_proposal(payload, context(), read_evidence_ids=read)
    assert code in {d['code'] for d in failure.value.diagnostics}


def test_read_citations_do_not_make_a_model_cause_a_rule_claim():
    payload = json.dumps({'status': 'normal', 'cause_assessment': {
        'categories': ['late_document'], 'explanation': '期内已收货。', 'evidence_ids': ['ev-r1']}})
    result = validate_proposal(payload, context(), read_evidence_ids={'ev-r1'})
    assert result.claims == [] and result.hypotheses == []
    assert result.cause_assessment.evidence_ids == ['ev-r1']


@pytest.mark.parametrize('supplied', [
    [{'evidence_id': 'ev-r1'}],
    [{**records()[1], 'amount': '999.00'}],
    [{**records()[1], 'book_id': 'another-book'}],
    ['ev-r1'], None,
])
def test_initial_documents_cannot_grant_reads_using_an_id_or_rewritten_content(supplied):
    finding = task()
    finding['metrics']['released_records'] = supplied
    with pytest.raises(ValueError, match='complete snapshot records'):
        context(finding)


def test_active_contract_still_supports_bounded_query_plans():
    schema = proposal_schema(context(), allow_plan=True)
    assert set(schema['properties']) == {'status', 'cause_assessment', 'evidence_plan'}
    raw = json.loads(proposal(categories=[]))
    raw['evidence_plan'] = None
    assert validate_proposal(json.dumps(raw), context(), allow_plan=True).evidence_plan is None
