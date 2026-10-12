"""Independent regressions for program state across fresh model conversations.

Every provider response is offline; none of these tests reads API credentials.
"""
import json
from copy import deepcopy

import httpx
import pytest
from app.context_transport import deserialize_context

from app.domain import check_records
from app.investigation import EvidenceMemory, MAX_PROGRAM_OBSERVATIONS, ToolContext, investigate
from app.model_gateway import BudgetExceeded, DeepSeekGateway, RunBudget


@pytest.fixture
def context_fixture():
    policy = {"framework": "CAS", "version": "test-v1", "period": "2026-09", "book_id": "one-book",
              "accounts": ["1403", "2202"], "snapshot_cutoff": "2026-09-30T23:59:59Z"}
    common = {"currency": "CNY", "book_id": "one-book", "business_date": "2026-09-01", "period": "2026-09",
              "observed_at": "2026-09-02T00:00:00Z", "kind": "journal", "voucher_id": "J-1"}
    rows = [{**common, "id": "one-row", "evidence_id": "one-evidence", "account": "1403",
             "debit": "100.00", "credit": "0.00"},
            {**common, "id": "two-row", "evidence_id": "two-evidence", "account": "2202",
             "debit": "0.00", "credit": "90.00"}]
    return rows, policy, check_records(rows, policy)[0]


def verification_claims():
    return [{"text": "incorrect numeric proposal", "evidence_ids": ["one-evidence"],
             "rule_id": "VOUCHER_BALANCE", "calculation": {"difference": "999.00"}},
            {"text": "no calculation requested", "evidence_ids": ["one-evidence"],
             "rule_id": "VOUCHER_BALANCE", "calculation": {}}]


def completion(proposal=None, *, finish="stop"):
    return httpx.Response(200, json={"model": "offline-model", "usage": {"total_tokens": 50}, "choices": [{
        "finish_reason": finish, "message": {"role": "assistant", "content": json.dumps(proposal or {
            "status": "insufficient", "claims": [], "hypotheses": [], "missing_evidence": []})}}]})


def tool_batch(operations):
    return httpx.Response(200, json={"model": "offline-model", "usage": {"total_tokens": 50}, "choices": [{
        "finish_reason": "tool_calls", "message": {"role": "assistant", "reasoning_content": "PRIVATE-CHAIN",
        "tool_calls": [{"id": str(i), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                       for i, (name, args) in enumerate(operations)]}}]})


def request_operation():
    return "request_evidence", {"description": "Need signed contract", "reason": "Separate duplicate from split posting"}


def assert_observations(brief):
    observations = brief["shared_evidence"]["program_observations"]
    assert [entry["tool"] for entry in observations] == ["request_evidence", "verify_claims"]
    request = observations[0]["result"]
    assert request["description"] == "Need signed contract" and request["reason"] == "Separate duplicate from split posting"
    assert request["retrieved"] is False and request["requires_new_snapshot"] is True
    checks = observations[1]["result"]["claims"]
    assert checks[0]["calculations_verified"] is False and checks[0]["calculation_status"] == "not_verified"
    assert checks[1]["calculations_verified"] is None and checks[1]["calculation_status"] == "not_requested"
    assert all(check["semantic_status"] == "unverified_model_proposal" for check in checks)


@pytest.mark.asyncio
async def test_noncached_observations_survive_later_tool_batches_and_role_handoff(context_fixture):
    original = deepcopy(context_fixture)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body["messages"][1]["content"])
        if len(requests) == 1:
            return tool_batch([request_operation(), ("verify_claims", {"claims": verification_claims()})])
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        assert "PRIVATE-CHAIN" not in json.dumps(body)
        assert_observations(brief)
        if len(requests) == 3:  # New role starts from durable observations.
            assert len(brief["prior_proposal_index"]) == 1
            return tool_batch([("query_journal_lines", {"reference": "not-in-the-fixed-snapshot"})])
        if len(requests) == 4:
            assert brief["recent_tool_results"] == []
            result = brief["shared_evidence"]["query_results"][0]["result"]
            assert result["total"] == 0 and result["record_evidence_ids"] == []
        return completion()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*context_fixture, mode="multi", gateway=DeepSeekGateway("offline-key", client=client))
    assert context_fixture == original and result["usage"]["calls"] == 7 and result["usage"]["tools"] == 3
    assert result["missing_evidence"] == ["Need signed contract"]
    assert_observations({"shared_evidence": {"program_observations": result["program_observations"]}})


@pytest.mark.asyncio
async def test_truncation_recovery_keeps_requests_and_false_or_unknown_verification(context_fixture):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return tool_batch([request_operation(), ("verify_claims", {"claims": verification_claims()})])
        assert_observations(deserialize_context(body["messages"][1]["content"]))
        assert "PRIVATE-CHAIN" not in json.dumps(body)
        if len(requests) == 2:
            return completion(finish="length")
        assert len(requests) == 3 and body["thinking"] == {"type": "disabled"} and "tools" not in body
        return completion()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*context_fixture, mode="single", gateway=DeepSeekGateway("offline-key", client=client))
    assert result["usage"]["calls"] == 3 and result["usage"]["tokens"] == 150
    assert result["proposals"][0]["truncation_recovery_used"] is True
    assert result["missing_evidence"] == ["Need signed contract"]


def test_program_observations_roundtrip_and_do_not_cache_verification(context_fixture):
    context = ToolContext(*context_fixture, budget=RunBudget())
    context.verified_finding = {**deepcopy(context_fixture[2]), "metrics": {"difference": "10.00"}}
    memory = EvidenceMemory()
    first = memory.execute(context, "verify_claims", {"claims": verification_claims()})
    assert first["claims"][0]["calculations_verified"] is False
    context.verified_finding = {**deepcopy(context_fixture[2]), "metrics": {"difference": "999.00"}}
    second = memory.execute(context, "verify_claims", {"claims": verification_claims()})
    assert second["claims"][0]["calculations_verified"] is True
    name, args = request_operation()
    memory.execute(context, name, args)
    memory.execute(context, name, args)
    assert memory.queries == {} and len(memory.program_observations) == 4
    assert context.requests == ["Need signed contract"]  # Tracking remains idempotent; observations describe every call.
    stored = memory.stored()
    restored = EvidenceMemory(stored)
    assert restored.brief() == memory.brief()
    stored["program_observations"][0]["result"]["claims"][0]["calculations_verified"] = True
    assert restored.program_observations[0]["result"]["claims"][0]["calculations_verified"] is False


@pytest.mark.parametrize("wrong_source", ["wrong_rule", "other_transaction"])
def test_same_numeric_value_cannot_verify_a_different_rule_or_transaction(context_fixture, wrong_source):
    rows, policy, finding = deepcopy(context_fixture)
    rows.append({**rows[0], "id": "unrelated-row", "evidence_id": "unrelated-evidence", "voucher_id": "J-UNRELATED"})
    context = ToolContext(rows, policy, finding, RunBudget())
    context.verified_finding = deepcopy(finding)
    claim = {"text": "The identical amount does not establish this source", "evidence_ids": ["one-evidence"],
             "rule_id": "VOUCHER_BALANCE", "calculation": {"difference": "10.00"}}
    if wrong_source == "wrong_rule":
        claim["rule_id"] = "TRIAL_BALANCE"  # Supported identifier, but this computation was never run.
    else:
        claim["evidence_ids"] = ["unrelated-evidence"]  # Exists in the same fixed snapshot.
    result = context.verify([claim])["claims"][0]
    assert result["citations_valid"] is True and result["rule_known"] is True
    assert result["calculations_verified"] is False and result["calculation_status"] == "not_verified"
    assert result["calculation_source_matches"] is False
    assert result["metric_source"] == {"rule_id": "VOUCHER_BALANCE", "evidence_ids": ["one-evidence", "two-evidence"]}
    assert result["semantic_status"] == "unverified_model_proposal"


def test_bound_source_verifies_only_reported_metrics_and_empty_calculation_stays_unknown(context_fixture):
    context = ToolContext(*context_fixture, budget=RunBudget())
    context.verified_finding = deepcopy(context_fixture[2])
    base = {"text": "Still an unverified semantic proposal", "evidence_ids": ["one-evidence"], "rule_id": "VOUCHER_BALANCE"}
    checks = context.verify([{**base, "calculation": {"difference": "10.00"}},
                             {**base, "calculation": {"uncomputed_metric": "10.00"}},
                             {**base, "calculation": {}}])["claims"]
    assert checks[0]["calculations_verified"] is True and checks[0]["calculation_source_matches"] is True
    assert checks[1]["calculations_verified"] is False and checks[1]["calculation_source_matches"] is True
    assert checks[1]["calculation_status"] == "not_verified"
    assert checks[2]["calculations_verified"] is None and checks[2]["calculation_status"] == "not_requested"
    assert "metric_source" not in checks[2] and "calculation_source_matches" not in checks[2]
    assert all(check["semantic_status"] == "unverified_model_proposal" for check in checks)


def test_observation_limit_fails_without_discarding_or_executing_an_extra_request(context_fixture):
    context = ToolContext(*context_fixture, budget=RunBudget())
    memory = EvidenceMemory()
    for number in range(MAX_PROGRAM_OBSERVATIONS):
        memory.execute(context, "request_evidence", {"description": f"request-{number}", "reason": "distinguish"})
    before = memory.stored()
    with pytest.raises(BudgetExceeded, match="程序观察记录"):
        memory.execute(context, "request_evidence", {"description": "extra", "reason": "not executed"})
    assert memory.stored() == before and "extra" not in context.requests
    with pytest.raises(ValueError, match="observation history"):
        EvidenceMemory({"program_observations": [*memory.program_observations, {}]})


def test_unavailable_prior_report_is_not_cached_and_completed_report_is_immutable(context_fixture):
    context = ToolContext(*context_fixture, budget=RunBudget())
    memory = EvidenceMemory()
    assert memory.execute(context, "read_prior_proposal", {"index": 0})["status"] == "unavailable"
    assert memory.queries == {}
    context.prior_proposals = [{"status": "insufficient", "claims": [], "hypotheses": [],
                                "missing_evidence": ["first role unresolved"]}]
    result = memory.execute(context, "read_prior_proposal", {"index": 0})
    assert result["status"] == "available" and result["cache_hit"] is False
    assert result["proposal"]["missing_evidence"] == ["first role unresolved"]
    assert memory.execute(context, "read_prior_proposal", {"index": 0})["cache_hit"] is True
    assert memory.records == {} and memory.references == {}  # A model report is never promoted to evidence.
    with pytest.raises(ValueError):
        memory.execute(context, "read_prior_proposal", {"index": 0, "book_id": "foreign"})


@pytest.mark.asyncio
async def test_unavailable_history_is_not_progress_and_still_consumes_tool_budget(context_fixture):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return tool_batch([("read_prior_proposal", {"index": 0})])
        assert "tools" not in body
        brief = deserialize_context(body["messages"][1]["content"])
        assert brief["shared_evidence"]["query_results"] == []
        assert brief["recent_tool_results"][0]["output"]["status"] == "unavailable"
        return completion()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*context_fixture, mode="single", gateway=DeepSeekGateway("offline-key", client=client))
    assert result["usage"]["calls"] == 2 and result["usage"]["tools"] == 1
    assert result["evidence_cache"]["queries"] == 0
    assert sum(step["event"] == "evidence_stagnation" for step in result["steps"]) == 1


def policy_passages(count=12):
    return [{"source_id": f"policy-{number}", "text": f"FULL-APPLICABILITY-CLAUSE-{number}",
             "url": f"https://www.mof.gov.cn/example/{number}", "framework": "CAS",
             "title": f"Applicability {number}", "effective_from": "2026-01-01", "effective_to": "2026-12-31"}
            for number in range(count)]


@pytest.mark.parametrize("arguments", [{}, {"rule_id": "VOUCHER_BALANCE"}, {"query": " \n\t "},
                                     {"rule_id": "VOUCHER_BALANCE", "query": "  "}])
def test_default_or_whitespace_policy_query_does_not_fetch_unrelated_fulltext(context_fixture, arguments):
    rows, policy, finding = deepcopy(context_fixture)
    policy["reference_passages"] = policy_passages()
    context = ToolContext(rows, policy, finding, RunBudget())
    memory = EvidenceMemory()
    result = memory.execute(context, "retrieve_policy", arguments)
    assert result["references_requested"] is False and result["available_reference_count"] == 12
    assert result["matched_reference_count"] is None and result["reference_count"] == 0
    assert result["reference_source_ids"] == [] and result["new_reference_passages"] == []
    assert result["references_truncated"] is False and memory.references == {}
    assert context.reference_ids == {f"policy-{number}" for number in range(12)}
    assert result["rules"]  # Executable-rule metadata remains available.
    if arguments.get("rule_id"):
        assert result["rules"][0]["rule_id"] == "VOUCHER_BALANCE" and result["rules"][0]["source_sha256"]
    assert "FULL-APPLICABILITY-CLAUSE" not in json.dumps(memory.brief())


def test_explicit_policy_keyword_returns_complete_matching_passage_and_trims_cache_parameters(context_fixture):
    rows, policy, finding = deepcopy(context_fixture)
    policy["reference_passages"] = policy_passages()
    context = ToolContext(rows, policy, finding, RunBudget())
    memory = EvidenceMemory()
    result = memory.execute(context, "retrieve_policy", {"query": " \n FULL-APPLICABILITY-CLAUSE-0 \t"})
    assert result["references_requested"] is True and result["available_reference_count"] == 12
    assert result["matched_reference_count"] == result["reference_count"] == 1
    assert result["reference_source_ids"] == ["policy-0"] and result["references_truncated"] is False
    passage = result["new_reference_passages"][0]
    for name in ("source_id", "text", "url", "framework", "title", "effective_from", "effective_to"):
        assert passage[name] == policy["reference_passages"][0][name]
    reused = memory.execute(context, "retrieve_policy", {"query": "FULL-APPLICABILITY-CLAUSE-0"})
    assert reused["cache_hit"] is True and len(memory.queries) == 1 and len(memory.references) == 1
    assert memory.brief()["query_results"][0]["parameters"]["query"] == "FULL-APPLICABILITY-CLAUSE-0"


def test_rule_directory_transport_pool_is_lossless_and_preserves_query_scope(context_fixture):
    context = ToolContext(*deepcopy(context_fixture), RunBudget())
    memory = EvidenceMemory()
    # Distinct searches return the same catalog, including unsuccessful ones.
    for keyword in ["", "missing-first", "missing-second", "missing-third", "missing-fourth"]:
        memory.execute(context, "retrieve_policy", {"query": keyword})
    memory.execute(context, "retrieve_policy", {"rule_id": "VOUCHER_BALANCE"})
    memory.execute(context, "retrieve_policy", {"rule_id": "NONEXISTENT"})
    original = memory.stored()
    brief = memory.brief()
    definitions = brief["rule_definitions"]
    for query in brief["query_results"]:
        stored_query = original["queries"][query["query_id"]]
        reconstructed = deepcopy(query)
        reconstructed["result"]["rules"] = [definitions[identity]
            for identity in reconstructed["result"].pop("rule_definition_ids")]
        assert reconstructed == stored_query
    # The targeted full rule and its directory summary must remain different.
    balance_definitions = [value for value in definitions.values() if value["rule_id"] == "VOUCHER_BALANCE"]
    assert len(balance_definitions) == 2
    assert sum("source_sha256" in definition for definition in balance_definitions) == 1
    assert brief["query_results"][-1]["result"]["rule_definition_ids"] == []
    assert brief["query_results"][0]["result"]["references_requested"] is False
    assert brief["query_results"][1]["result"]["matched_reference_count"] == 0
    assert memory.stored() == original
    assert EvidenceMemory(original).brief() == brief
    naive = {**brief, "rule_definitions": {}, "query_results": list(original["queries"].values())}
    assert len(json.dumps(brief).encode()) < len(json.dumps(naive).encode()) * 0.75
    definitions[next(iter(definitions))]["title"] = "mutated transport copy"
    assert memory.stored() == original


@pytest.mark.parametrize("framework", ["CAS", "SCAS"])
def test_explicit_policy_keyword_respects_fixed_framework_and_effective_period(context_fixture, framework):
    rows, policy, finding = deepcopy(context_fixture)
    policy["framework"] = framework
    base = policy_passages(1)[0]
    policy["reference_passages"] = [{**base, "source_id": "cas", "framework": "CAS"},
                                  {**base, "source_id": "scas", "framework": "SCAS"},
                                  {**base, "source_id": "common", "framework": "BOTH"},
                                  {**base, "source_id": "future", "framework": framework, "effective_from": "2027-01-01"}]
    context = ToolContext(rows, policy, finding, RunBudget())
    result = context.execute("retrieve_policy", {"query": "FULL-APPLICABILITY-CLAUSE"})
    expected = {framework.lower(), "common"}
    assert result["references_requested"] is True
    assert result["available_reference_count"] == result["matched_reference_count"] == 2
    assert {passage["source_id"] for passage in result["reference_passages"]} == expected
    assert context.reference_ids == expected and "future" not in context.reference_ids
    assert all(rule["framework"] == framework for rule in result["rules"])


def test_policy_queries_share_complete_references_once_and_keep_negative_and_truncated_results(context_fixture):
    rows, policy, finding = deepcopy(context_fixture)
    policy["reference_passages"] = policy_passages()
    context = ToolContext(rows, policy, finding, RunBudget())
    memory = EvidenceMemory()
    first = memory.execute(context, "retrieve_policy", {"rule_id": "VOUCHER_BALANCE", "query": "FULL-APPLICABILITY-CLAUSE"})
    second = memory.execute(context, "retrieve_policy", {"rule_id": "ACCOUNT_VALIDITY", "query": "FULL-APPLICABILITY-CLAUSE"})
    negative = memory.execute(context, "retrieve_policy", {"query": "NO-SUCH-POLICY-TEXT"})
    assert len(first["new_reference_passages"]) == 10 and second["new_reference_passages"] == []
    assert first["reference_source_ids"] == second["reference_source_ids"] == [f"policy-{i}" for i in range(10)]
    assert second["reference_count"] == 12 and second["references_truncated"] is True
    assert negative["reference_count"] == 0 and negative["reference_source_ids"] == []
    assert negative["references_truncated"] is False
    assert negative["references_requested"] is True and negative["matched_reference_count"] == 0
    assert negative["available_reference_count"] == 12
    brief = memory.brief()
    assert len(brief["reference_passages"]) == 10
    for passage in brief["reference_passages"]:
        assert passage["effective_from"] == "2026-01-01" and passage["effective_to"] == "2026-12-31"
        assert json.dumps(brief).count(passage["text"]) == 1
    assert all("reference_passages" not in query["result"] for query in brief["query_results"])
    assert EvidenceMemory(memory.stored()).brief() == brief
    assert context.reference_ids == {f"policy-{i}" for i in range(12)} and context.evidence_ids == {"one-evidence", "two-evidence"}


@pytest.mark.parametrize("conflict_in_initial_batch", [False, True])
def test_same_policy_source_identity_cannot_overwrite_different_content(context_fixture, conflict_in_initial_batch):
    rows, policy, finding = deepcopy(context_fixture)
    passages = policy_passages(1)
    policy["reference_passages"] = passages
    context = ToolContext(rows, policy, finding, RunBudget())
    memory = EvidenceMemory()
    if conflict_in_initial_batch:
        context.references.append({**context.references[0], "text": "conflicting full content"})
    else:
        memory.execute(context, "retrieve_policy", {"rule_id": "VOUCHER_BALANCE", "query": "Applicability"})
        context.references[0]["text"] = "conflicting full content"
    before = memory.stored()
    with pytest.raises(ValueError, match="Conflicting policy passage"):
        memory.execute(context, "retrieve_policy", {"rule_id": "ACCOUNT_VALIDITY", "query": "Applicability"})
    assert memory.stored() == before
    assert context_fixture[1].get("reference_passages") is None


def initial_active_plan():
    return {"hypotheses": [{"hypothesis_id": "split", "explanation": "split posting"},
                           {"hypothesis_id": "duplicate", "explanation": "duplicate posting"}],
            "queries": [{"query_id": "selected-ledger", "tool": "query_journal_lines", "arguments": {},
                         "predictions": {"split": {"status": "predicted", "label": "distinct"},
                                         "duplicate": {"status": "predicted", "label": "same"}}}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected_calls,expected_tools", [("single", 3, 4), ("multi", 7, 4), ("active", 5, 6)])
async def test_two_complete_tool_batches_then_nonthinking_handoff_preserves_unresolved(
        context_fixture, mode, expected_calls, expected_tools):
    requests = []
    active_role = 1 if mode == "active" else 0
    unresolved = {"explanation": "UNRESOLVED-CAUSE-FOR-NEXT-ROLE", "status": "unresolved",
                  "evidence_ids": ["one-evidence"], "missing_evidence": ["Need signed contract"]}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body["messages"][1]["content"])
        index = len(brief["prior_proposal_index"])
        if mode == "active" and index == 0:
            assert "tools" not in body and body["thinking"] == {"type": "enabled"}
            return completion({"status": "insufficient", "claims": [], "hypotheses": [], "missing_evidence": [],
                               "evidence_plan": initial_active_plan()})
        if index > active_role:
            assert unresolved in brief["unresolved_hypotheses"] and "Need signed contract" in brief["missing_evidence"]
            assert body["thinking"] == {"type": "enabled"} and body["tools"]
            # Later roles need not repeat the previous role's unresolved list:
            # the following role still receives the complete server history.
            return completion()
        turns = brief.get("current_role_tool_turns", 0)
        if turns == 0:
            assert body["tools"] and body["thinking"] == {"type": "enabled"}
            return tool_batch([("query_journal_lines", {"account": "1403"})])
        if turns == 1:
            assert body["tools"] and body["thinking"] == {"type": "enabled"}
            return tool_batch([("query_journal_lines", {"account": "2202"}), request_operation(),
                               ("verify_claims", {"claims": verification_claims()})])
        assert turns == 2 and "tools" not in body and body["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in body
        assert {row["evidence_id"] for row in brief["shared_evidence"]["records"]} == {"one-evidence", "two-evidence"}
        observations = brief["shared_evidence"]["program_observations"]
        assert any(entry["tool"] == "request_evidence" and entry["result"]["description"] == "Need signed contract"
                   for entry in observations)
        assert any(entry["tool"] == "verify_claims" and entry["result"]["claims"][0]["calculations_verified"] is False
                   for entry in observations)
        return completion({"status": "insufficient", "claims": [], "hypotheses": [unresolved],
                           "missing_evidence": ["Need signed contract"]})

    original = deepcopy(context_fixture)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*context_fixture, mode=mode, gateway=DeepSeekGateway("offline-key", client=client))
    assert context_fixture == original
    assert result["usage"]["calls"] == expected_calls and result["usage"]["tools"] == expected_tools
    assert result["claims"] and result["proposed_claims"] == []
    assert result["proposals"][active_role]["status"] == "insufficient"
    assert "Need signed contract" in result["missing_evidence"]
    assert any(hypothesis["explanation"] == unresolved["explanation"] and hypothesis["status"] == "unresolved"
               for hypothesis in result["hypotheses"])
    limits = [step for step in result["steps"] if step["event"] == "role_tool_limit"]
    assert len(limits) == 1 and limits[0]["tool_batches"] == 2 and limits[0]["fact_confirmed"] is False
    handoffs = [step for step in result["steps"] if step["event"] == "stage_handoff"]
    assert len(handoffs) == 1 and handoffs[0]["thinking"] is False and handoffs[0]["tools"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "multi", "active"])
async def test_role_finalization_does_not_bypass_shared_paid_request_limit(context_fixture, mode):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if mode == "active" and len(requests) == 1:
            return completion({"status": "insufficient", "claims": [], "hypotheses": [], "missing_evidence": [],
                               "evidence_plan": initial_active_plan()})
        return tool_batch([("query_journal_lines", {"account": "1403" if len(requests) == 1 else "2202"})])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*context_fixture, mode=mode, gateway=DeepSeekGateway("offline-key", client=client),
                                   checkpoint_config={"usage": {"calls": 22}})
    assert len(requests) == 2 and result["usage"]["calls"] == 24 and result["usage"]["tokens"] == 100
    assert result["status"] == "budget_exhausted" and result["error"]["code"] == "budget_exhausted"
    assert result["claims"] and result["proposed_claims"] == []
    assert result["evidence_cache"]["records"] == 2
