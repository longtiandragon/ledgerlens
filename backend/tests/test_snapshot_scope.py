"""Snapshot inventory and rule applicability given to model roles.

Hand-authored records and expectations; MockTransport never calls a provider.
Live v22 pilots treated unread kinds as absent and inapplicable rules as gaps.
"""
import json

import httpx
import pytest

from app.context_transport import deserialize_context
from app.investigation import ToolContext, investigate, tool_schemas
from app.model_contract import PROTOCOL
from app.model_gateway import DeepSeekGateway, RunBudget

COMMON = {'book_id': 'scope-book', 'currency': 'CNY', 'period': '2026-09', 'business_date': '2026-09-20',
          'observed_at': '2026-10-01T00:00:00+00:00'}


def records():
    return [
        {**COMMON, 'id': 'j1', 'evidence_id': 'ev-j1', 'source_id': 's-j1', 'kind': 'journal', 'voucher_id': 'V1',
         'account': '1405', 'debit': '100.00', 'credit': '0.00'},
        {**COMMON, 'id': 'j2', 'evidence_id': 'ev-j2', 'source_id': 's-j2', 'kind': 'journal', 'voucher_id': 'V1',
         'account': '2202', 'debit': '0.00', 'credit': '100.00'},
        {**COMMON, 'id': 'i1', 'evidence_id': 'ev-i1', 'source_id': 's-i1', 'kind': 'invoice', 'invoice_id': 'FIXTURE-INV-77',
         'amount': '123.45'},
    ]


def policy():
    return {'framework': 'CAS', 'version': 'scope-fixture', 'period': '2026-09', 'book_id': 'scope-book',
            'snapshot_id': 'scope-snapshot', 'snapshot_digest': 'f' * 64,
            'snapshot_cutoff': '2026-10-02T00:00:00+00:00'}


def finding():
    return {'rule_id': 'REVIEW', 'task_type': 'review', 'record_ids': [], 'evidence_ids': [],
            'status': 'normal', 'metrics': {}, 'missing_evidence': []}


def test_inventory_counts_kinds_and_lists_missing_kinds_per_rule():
    context = ToolContext(records(), policy(), finding(), budget=RunBudget())
    scope = context.snapshot_scope
    assert scope['record_kind_counts'] == {'invoice': 1, 'journal': 2}
    missing = {item['rule_id']: item['missing_kinds'] for item in scope['rule_applicability']}
    # Hand-derived from the catalog's documented required kinds.
    assert missing['VOUCHER_BALANCE'] == []
    assert missing['DUPLICATE_INVOICE'] == []
    assert missing['RECEIPT_GAP'] == ['order', 'receipt']
    assert missing['OPENING_REQUIRED'] == ['opening', 'trial_balance']
    assert missing['TRIAL_BALANCE'] == ['opening', 'trial_balance']
    assert missing['BANK_UNMATCHED'] == ['bank']
    # Rule 0.2.0: the funds reconciliation results became month-end cases and
    # are documented in the catalog (16 ledger/purchase/period/funds + 7 funds).
    assert missing['BANK_MATCH_AMBIGUOUS'] == ['bank'] and missing['BANK_CANDIDATES_ALIGNED'] == ['bank']
    assert len(missing) == 23
    assert 'FIXTURE-INV-77' not in json.dumps(scope) and '123.45' not in json.dumps(scope)  # counts only


def test_saved_check_tool_is_hidden_only_when_nothing_was_captured():
    context = ToolContext(records(), policy(), finding(), budget=RunBudget())
    names = {tool['function']['name'] for tool in tool_schemas(context)}
    assert 'read_policy_checks' not in names and 'query_journal_lines' in names
    assert 'not missing evidence' in context.policy_check_manifest['notice']
    assert 'read_policy_checks' in {tool['function']['name'] for tool in tool_schemas()}  # unscoped catalog


def test_protocol_separates_unread_kinds_and_inapplicable_rules_from_insufficiency():
    assert 'never report them as missing' in PROTOCOL
    assert 'never makes\nthe task insufficient' in PROTOCOL


@pytest.mark.asyncio
async def test_every_role_brief_carries_the_scope_and_inputs_stay_unchanged():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        brief = deserialize_context(body['messages'][1]['content'])
        seen.append((brief['snapshot_scope'], {tool['function']['name'] for tool in body.get('tools') or []}))
        return httpx.Response(200, json={'model': 'offline', 'usage': {'total_tokens': 10}, 'choices': [{
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(
                {'status': 'normal', 'claims': [], 'hypotheses': [], 'missing_evidence': []})}}]})

    rows, rules, task = records(), policy(), finding()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(rows, rules, task, mode='single',
                                   gateway=DeepSeekGateway('offline-key', client=client))
    assert result['error'] is None and seen
    for scope, tools in seen:
        assert scope['record_kind_counts'] == {'invoice': 1, 'journal': 2}
        assert 'read_policy_checks' not in tools
    assert rows == records() and rules == policy()
