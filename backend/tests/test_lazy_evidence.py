"""Independent offline regressions for role-scoped cached evidence reads.

Amounts, dates, documentary text and the expected difference are handwritten.
No generator, production calculator, credentials or external service supplies
the expected answers. MockTransport exercises the actual gateway and graph.
"""

import hashlib
import json
from copy import deepcopy

import httpx
import pytest

from app.context_transport import deserialize_context
from app.investigation import (
    EvidenceMemory, MAX_ROLE_TOOL_BATCHES, ProposalRejected, ToolContext, investigate, validate_proposal,
)
from app.model_gateway import DeepSeekGateway, RunBudget


@pytest.fixture
def scope():
    policy = {
        "framework": "CAS", "version": "lazy-test-v1", "period": "2026-09", "book_id": "lazy-book",
        "accounts": ["1403", "2202"], "snapshot_cutoff": "2026-09-30T23:59:59Z",
        "reference_passages": [{
            "source_id": f"lazy-policy-{i}", "title": "独立测试条款", "framework": "CAS",
            "text": f"完整测试条款 {i}：分批验收不能直接证明重复确认，需保留原始日期和单据。",
            "url": f"https://www.mof.gov.cn/test-only/lazy-{i}",
            "effective_from": "2026-01-01", "effective_to": "2026-12-31",
        } for i in range(12)],
    }
    common = {
        "book_id": "lazy-book", "currency": "CNY", "kind": "journal", "voucher_id": "LAZY-1",
        "business_date": "2026-09-01", "period": "2026-09", "observed_at": "2026-09-02T00:00:00Z",
    }
    rows = [
        {**common, "id": "lazy-row-1", "evidence_id": "lazy-evidence-1", "source_id": "lazy-source-1",
         "account": "1403", "debit": "100.00", "credit": "0.00", "description": "原始入库单原文：第一批。"},
        {**common, "id": "lazy-row-2", "evidence_id": "lazy-evidence-2", "source_id": "lazy-source-2",
         "account": "2202", "debit": "0.00", "credit": "90.00", "description": "原始应付记录原文：待复核。"},
    ]
    finding = {
        "rule_id": "VOUCHER_BALANCE", "record_ids": ["lazy-row-1", "lazy-row-2"],
        "evidence_ids": ["lazy-evidence-1", "lazy-evidence-2"],
        "status": "supported", "metrics": {"difference": "10.00"}, "missing_evidence": [],
    }
    return rows, policy, finding


def make_context(scope):
    context = ToolContext(*deepcopy(scope), budget=RunBudget())
    context.verified_finding = deepcopy(scope[2])
    return context


def populated_memory(scope):
    context = make_context(scope)
    memory = EvidenceMemory()
    operations = [
        ("query_journal_lines", {"limit": 1}),
        ("query_journal_lines", {"limit": 2}),
        ("query_journal_lines", {"reference": "missing-source"}),
        ("retrieve_policy", {}),
        ("retrieve_policy", {"rule_id": "VOUCHER_BALANCE", "query": "完整测试条款"}),
        ("retrieve_policy", {"query": "absent-keyword"}),
    ]
    identities = [memory.execute(context, name, arguments)["query_id"] for name, arguments in operations]
    memory.execute(context, "request_evidence", {"description": "补充迟到签收单", "reason": "区分两种解释"})
    memory.execute(context, "verify_claims", {"claims": [
        {"text": "错误数字仍是模型建议", "evidence_ids": ["lazy-evidence-1"],
         "rule_id": "VOUCHER_BALANCE", "calculation": {"difference": "999.00"}},
        {"text": "未要求计算验证", "evidence_ids": ["lazy-evidence-1"],
         "rule_id": "VOUCHER_BALANCE", "calculation": {}},
    ]})
    return context, memory, identities


def rebuild_query(view, query):
    rebuilt = deepcopy(query)
    if "rule_definition_ids" in rebuilt["result"]:
        rebuilt["result"]["rules"] = [deepcopy(view["rule_definitions"][identity])
            for identity in rebuilt["result"].pop("rule_definition_ids")]
    return rebuilt


def test_complete_internal_view_preserves_all_scoped_payloads_and_original_text(scope):
    _, memory, identities = populated_memory(scope)
    stored = memory.stored()
    full = memory.brief()
    assert "query_manifest" not in full and "unread_query_count" not in full
    assert [query["query_id"] for query in full["query_results"]] == identities
    for query in full["query_results"]:
        assert rebuild_query(full, query) == stored["queries"][query["query_id"]]
    assert {row["description"] for row in full["records"]} == {
        "原始入库单原文：第一批。", "原始应付记录原文：待复核。"}
    assert [row["debit"] for row in full["records"]] == ["100.00", "0.00"]
    assert full["reference_passages"] == list(stored["references"].values())
    assert len(full["reference_passages"]) == 10  # Original query was explicitly truncated, not silently completed.
    assert full["program_observations"] == stored["program_observations"]
    assert memory.stored() == stored


def test_empty_role_focus_exposes_complete_manifest_without_unread_source_payloads(scope):
    _, memory, identities = populated_memory(scope)
    stored = memory.stored()
    view = memory.brief(set())
    assert view["view"] == "explicit_role_reads" and view["unread_query_count"] == 6
    assert view["records"] == view["query_results"] == view["reference_passages"] == []
    assert view["rule_definitions"] == {}
    assert [entry["query_id"] for entry in view["query_manifest"]] == identities
    for entry in view["query_manifest"]:
        original = stored["queries"][entry["query_id"]]
        assert entry["payload_in_view"] is False and entry["parameters"] == original["parameters"]
        envelope = {
            "query": original,
            "records": {key: stored["records"][key]
                        for key in sorted(set(original["result"].get("record_evidence_ids", [])))},
            "reference_passages": {key: stored["references"][key]
                        for key in sorted(set(original["result"].get("reference_source_ids", [])))},
        }
        assert entry["hash_scope"] == "query_and_referenced_record_policy_payloads"
        assert entry["result_sha256"] == hashlib.sha256(json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    metadata = [entry["metadata"] for entry in view["query_manifest"]]
    assert metadata[0]["truncated"] is True and metadata[0]["total"] == 2
    assert metadata[2]["total"] == 0 and metadata[2]["record_evidence_ids"] == []
    assert metadata[3]["references_requested"] is False and metadata[3]["matched_reference_count"] is None
    assert metadata[4]["references_truncated"] is True and metadata[4]["reference_count"] == 12
    assert metadata[5]["references_requested"] is True and metadata[5]["matched_reference_count"] == 0
    observations = view["program_observations"]
    assert observations[0]["result"]["retrieved"] is False
    assert observations[0]["result"]["requires_new_snapshot"] is True
    assert observations[1]["result"]["claims"][0]["calculations_verified"] is False
    assert observations[1]["result"]["claims"][1]["calculations_verified"] is None
    assert all(item["semantic_status"] == "unverified_model_proposal"
               for item in observations[1]["result"]["claims"])
    serialized = json.dumps(view, ensure_ascii=False)
    assert "原始入库单原文" not in serialized and "分批验收不能直接证明" not in serialized
    assert memory.stored() == stored


def test_scoped_read_of_six_queries_returns_lossless_record_policy_and_hash_identity(scope):
    context, memory, identities = populated_memory(scope)
    before = memory.stored()
    output = memory.execute(context, "read_evidence_queries", {"query_ids": identities})
    assert output["status"] == "available" and output["cache_hit"] is True
    assert output["query_ids"] == identities and output["evidence"]["unread_query_count"] == 0
    full = output["evidence"]
    for query in full["query_results"]:
        assert rebuild_query(full, query) == before["queries"][query["query_id"]]
    assert all(entry["payload_in_view"] is True for entry in full["query_manifest"])
    assert full["records"] == list(before["records"].values())
    assert full["reference_passages"] == list(before["references"].values())
    assert memory.stored() == before  # Read never creates a new query or executes any source lookup.
    assert context.requests == ["补充迟到签收单"]


def test_role_read_of_one_query_never_materializes_another_query_payload(scope):
    context, memory, identities = populated_memory(scope)
    result = memory.execute(context, "read_evidence_queries", {"query_ids": [identities[0]]})["evidence"]
    assert [query["query_id"] for query in result["query_results"]] == [identities[0]]
    assert [row["evidence_id"] for row in result["records"]] == ["lazy-evidence-1"]
    assert result["reference_passages"] == [] and result["rule_definitions"] == {}
    assert result["unread_query_count"] == 5
    assert [entry["payload_in_view"] for entry in result["query_manifest"]] == [True, False, False, False, False, False]


@pytest.mark.parametrize("changed", ["book", "snapshot", "policy", "record", "finding"])
def test_persisted_cache_rejects_different_scope_before_exposing_any_payload(scope, changed):
    context, memory, identities = populated_memory(scope)
    memory.bind(context)
    before = memory.stored()
    rows, policy, finding = deepcopy(scope)
    if changed == "book":
        policy["book_id"] = "other-book"
        for row in rows:
            row["book_id"] = "other-book"
    elif changed == "snapshot":
        policy["snapshot_cutoff"] = "2026-10-01T23:59:59Z"
    elif changed == "policy":
        policy["version"] = "lazy-test-v2"
    elif changed == "record":
        rows[0]["description"] = "新增原文版本"
    else:
        finding["missing_evidence"] = ["新增案件条件"]
    other = ToolContext(rows, policy, finding, RunBudget())
    with pytest.raises(ValueError, match="different snapshot or policy"):
        memory.execute(other, "read_evidence_queries", {"query_ids": [identities[0]]})
    assert memory.stored() == before


@pytest.mark.parametrize("arguments", [
    {"query_ids": []}, {"query_ids": ["0" * 64] * 2},
    {"query_ids": [f"{i:064x}" for i in range(7)]}, {"query_ids": ["not-a-sha"]},
    {"query_ids": ["0" * 64], "book_id": "other-book"},
])
def test_cached_read_contract_rejects_empty_duplicate_oversized_and_scope_override_batches(scope, arguments):
    context, memory, _ = populated_memory(scope)
    before = memory.stored()
    with pytest.raises(ValueError):
        memory.execute(context, "read_evidence_queries", arguments)
    assert memory.stored() == before


def test_unknown_identity_rejects_whole_read_batch_without_a_partial_result_or_empty_cache(scope):
    context, memory, identities = populated_memory(scope)
    before = memory.stored()
    with pytest.raises(ValueError, match="Unknown cached query identity"):
        memory.execute(context, "read_evidence_queries", {"query_ids": [identities[0], "0" * 64]})
    assert memory.stored() == before and "0" * 64 not in memory.queries
    with pytest.raises(ValueError, match="Unknown cached query identity"):
        memory.brief({identities[0], "0" * 64})
    assert memory.stored() == before


def test_unbound_nonempty_cache_cannot_be_adopted_by_a_new_snapshot(scope):
    context, memory, identities = populated_memory(scope)
    unbound = memory.stored()
    unbound.pop("scope")
    restored = EvidenceMemory(unbound)
    before = restored.stored()
    with pytest.raises(ValueError, match="Unbound evidence memory"):
        restored.execute(context, "read_evidence_queries", {"query_ids": [identities[0]]})
    assert restored.stored() == before


def test_returned_views_and_restored_state_are_deep_copies(scope):
    context, memory, identities = populated_memory(scope)
    before = memory.stored()
    output = memory.execute(context, "read_evidence_queries", {"query_ids": identities})
    output["evidence"]["records"][0]["debit"] = "999.99"
    output["evidence"]["reference_passages"][0]["text"] = "altered"
    output["evidence"]["query_results"][0]["result"]["record_evidence_ids"].clear()
    output["evidence"]["query_manifest"][0]["metadata"]["record_evidence_ids"].clear()
    output["evidence"]["program_observations"][0]["result"]["retrieved"] = True
    next(iter(output["evidence"]["rule_definitions"].values()))["title"] = "altered"
    assert memory.stored() == before
    durable = memory.stored()
    restored = EvidenceMemory(durable)
    durable["queries"][identities[0]]["result"]["total"] = 999
    durable["records"]["lazy-evidence-1"]["debit"] = "999.99"
    assert restored.stored() == before and memory.stored() == before


def test_regular_cache_hits_cannot_mutate_durable_evidence_indirectly(scope):
    context, memory, identities = populated_memory(scope)
    before = memory.stored()
    hit = memory.execute(context, "query_journal_lines", {"limit": 1})
    assert hit["cache_hit"] is True and hit["query_id"] == identities[0]
    hit["record_evidence_ids"].clear()
    assert memory.stored() == before


def test_first_query_response_does_not_alias_stored_rules_identifiers_or_records(scope):
    context = make_context(scope)
    memory = EvidenceMemory()
    journals = memory.execute(context, "query_journal_lines", {})
    policy = memory.execute(context, "retrieve_policy", {"rule_id": "VOUCHER_BALANCE", "query": "完整测试条款"})
    before = memory.stored()
    journals["record_evidence_ids"].clear()
    journals["new_records"][0]["debit"] = "999.99"
    policy["rules"][0]["title"] = "altered"
    policy["reference_source_ids"].clear()
    policy["new_reference_passages"][0]["text"] = "altered"
    assert memory.stored() == before


def test_conflicting_evidence_identity_rejects_entire_batch_without_overwriting_first_source(scope):
    rows, policy, finding = deepcopy(scope)
    rows.append({**rows[0], "id": "different-record-id", "debit": "900.00", "description": "不同原文"})
    context = ToolContext(rows, policy, finding, RunBudget())
    memory = EvidenceMemory()
    first = memory.execute(context, "query_journal_lines", {"account": "1403", "limit": 1})
    assert first["new_records"][0]["debit"] == "100.00"
    before = memory.stored()
    with pytest.raises(ValueError, match="Conflicting record content"):
        memory.execute(context, "query_journal_lines", {"account": "1403", "limit": 2})
    assert memory.stored() == before and len(memory.queries) == 1


def test_reading_cached_model_opinion_never_grants_original_document_access(scope):
    context = make_context(scope)
    context.prior_proposals = [{"status": "supported", "claims": [{
        "text": "模型意见不能替代原文", "evidence_ids": ["lazy-evidence-1"], "rule_id": "VOUCHER_BALANCE",
        "assessment": "finding", "origin": "model_proposal", "calculation": {}}],
        "hypotheses": [], "missing_evidence": []}]
    memory = EvidenceMemory()
    identity = memory.execute(context, "read_prior_proposal", {"index": 0})["query_id"]
    full = memory.execute(context, "read_evidence_queries", {"query_ids": [identity]})["evidence"]
    assert full["query_manifest"][0]["payload_kind"] == "model_opinion"
    assert full["query_results"][0]["result"]["proposal"]["claims"][0]["text"] == "模型意见不能替代原文"
    assert full["records"] == [] and memory.content_ids({identity}) == (set(), set())
    proposal = full["query_results"][0]["result"]["proposal"]
    assert "origin" not in proposal["claims"][0]
    with pytest.raises(ProposalRejected) as rejected:
        validate_proposal(json.dumps(proposal), context, read_evidence_ids=set())
    assert rejected.value.diagnostics == [{"code": "evidence_content_not_read", "path": ["claims", 0, "evidence_ids"]}]


def test_late_evidence_is_rejected_and_request_does_not_fetch_it_or_upgrade_facts(scope):
    context, memory, identities = populated_memory(scope)
    rows, policy, finding = deepcopy(scope)
    rows.append({**rows[0], "id": "late-row", "evidence_id": "late-evidence",
                 "observed_at": "2026-10-01T00:00:00Z", "description": "迟到签收单"})
    with pytest.raises(ValueError, match="Future"):
        ToolContext(rows, policy, finding, RunBudget())
    view = memory.execute(context, "read_evidence_queries", {"query_ids": identities})["evidence"]
    assert all(row["evidence_id"] != "late-evidence" for row in view["records"])
    assert view["program_observations"][0]["result"]["retrieved"] is False
    assert context.records == scope[0] and context.policy == scope[1]


def completion(payload=None):
    return httpx.Response(200, json={"model": "offline-lazy-model", "usage": {"total_tokens": 10},
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(payload or {
            "status": "normal", "claims": [{"text": "真实引用不能证明此错误金额或自由文本结论",
                "evidence_ids": ["lazy-evidence-1"], "rule_id": "VOUCHER_BALANCE",
                "calculation": {"difference": "999.00"}}], "hypotheses": [], "missing_evidence": []})}}]})


def tool_completion(name, arguments):
    return httpx.Response(200, json={"model": "offline-lazy-model", "usage": {"total_tokens": 10},
        "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
            "id": "lazy-call", "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments)}}]}}]})


@pytest.mark.asyncio
async def test_first_role_materialization_is_progress_but_repeat_read_stops_and_keeps_fact_boundary(scope):
    before = deepcopy(scope)
    requests = []
    identity = None

    def handler(request):
        nonlocal identity
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body["messages"][1]["content"])
        evidence = brief["shared_evidence"]
        number = len(requests)
        if number == 1:
            assert evidence["query_manifest"] == [] and evidence["records"] == []
            return tool_completion("query_journal_lines", {})
        if number == 2:
            identity = evidence["query_manifest"][0]["query_id"]
            assert len(evidence["records"]) == 2
            return completion()
        if number == 3:  # New specialist sees only manifest until explicitly reading this cache entry.
            assert evidence["query_manifest"][0]["payload_in_view"] is False
            assert evidence["records"] == evidence["query_results"] == []
            return tool_completion("read_evidence_queries", {"query_ids": [identity]})
        if number == 4:  # A globally cached but first role read must retain the chance for a second tool batch.
            assert "tools" in body and body["thinking"] == {"type": "enabled"}
            assert evidence["query_manifest"][0]["payload_in_view"] is True
            assert {row["description"] for row in evidence["records"]} == {
                "原始入库单原文：第一批。", "原始应付记录原文：待复核。"}
            return tool_completion("read_evidence_queries", {"query_ids": [identity]})
        if number == 5:
            assert "tools" not in body and body["thinking"] == {"type": "disabled"}
            assert evidence["unread_query_count"] == 0
        else:
            assert evidence["records"] == evidence["query_results"] == []
            assert evidence["query_manifest"][0]["payload_in_view"] is False
        return completion()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scope, mode="multi", gateway=DeepSeekGateway("offline-key", client=client))
    assert len(requests) == 8 and result["usage"]["calls"] == 8 and result["usage"]["tools"] == 3
    stagnation = [step for step in result["steps"] if step["event"] == "evidence_stagnation"]
    assert len(stagnation) == 1 and stagnation[0]["role"] == "evidence"
    assert result["evidence_cache"]["queries"] == 1 and result["evidence_cache"]["records"] == 2
    assert result["status"] == result["fact_status"] == "supported"
    assert result["claims"][0]["calculation"]["difference"] == "10.00"
    assert all(claim["origin"] == "model_proposal" for claim in result["proposed_claims"])
    assert all(claim["verification"]["calculations_verified"] is False for claim in result["proposed_claims"])
    assert all(claim["verification"]["semantic_status"] == "unverified_model_proposal"
               for claim in result["proposed_claims"])
    assert scope == before


@pytest.mark.asyncio
@pytest.mark.parametrize("exhausted", ["calls", "tools"])
async def test_lazy_read_keeps_twenty_four_call_and_sixty_tool_limits(scope, exhausted):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        return tool_completion("read_evidence_queries", {"query_ids": ["0" * 64]})

    assert MAX_ROLE_TOOL_BATCHES == 2
    assert (RunBudget().max_calls, RunBudget().max_tools, RunBudget().max_tokens) == (24, 60, 200_000)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scope, mode="single", gateway=DeepSeekGateway("offline-key", client=client),
                                   checkpoint_config={"usage": {exhausted: 24 if exhausted == "calls" else 60}})
    assert result["status"] == "budget_exhausted" and result["error"]["code"] == "budget_exhausted"
    assert result["usage"][exhausted] == (24 if exhausted == "calls" else 60)
    assert len(attempts) == (0 if exhausted == "calls" else 1)
    assert result["evidence_cache"]["queries"] == 0
    assert result["claims"][0]["calculation"]["difference"] == "10.00"


@pytest.mark.asyncio
async def test_unknown_cached_read_consumes_tool_attempt_and_stops_without_creating_evidence(scope):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return tool_completion("read_evidence_queries", {"query_ids": ["0" * 64]})
        assert "tools" not in body
        brief = deserialize_context(body["messages"][1]["content"])
        assert brief["shared_evidence"]["query_manifest"] == []
        assert brief["recent_tool_results"][0]["output"]["error"] == "invalid_tool_or_arguments"
        return completion()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scope, mode="single", gateway=DeepSeekGateway("offline-key", client=client))
    assert result["usage"]["calls"] == 2 and result["usage"]["tools"] == 1
    assert result["evidence_cache"]["queries"] == result["evidence_cache"]["records"] == 0
    assert sum(step["event"] == "evidence_stagnation" for step in result["steps"]) == 1


@pytest.mark.asyncio
async def test_neutral_review_manifest_alone_cannot_become_a_finding_even_when_ids_exist(scope):
    rows, policy, _ = deepcopy(scope)
    review = {"task_type": "review", "record_ids": [], "evidence_ids": [], "missing_evidence": []}
    requests = []
    purported_finding = {
        "status": "supported", "claims": [{"text": "未读取原文的引用不能成为有效发现",
            "evidence_ids": ["lazy-evidence-1"], "rule_id": "VOUCHER_BALANCE", "assessment": "finding",
            "calculation": {"difference": "10.00"}}], "hypotheses": [], "missing_evidence": [],
    }

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body["messages"][1]["content"])
        if len(requests) == 1:
            return tool_completion("query_journal_lines", {})
        if len(requests) == 2:
            assert len(brief["shared_evidence"]["records"]) == 2
            return completion({"status": "insufficient", "claims": [], "hypotheses": [], "missing_evidence": []})
        assert brief["shared_evidence"]["records"] == []
        assert brief["shared_evidence"]["query_manifest"][0]["payload_in_view"] is False
        assert "lazy-evidence-1" in brief["shared_evidence"]["query_manifest"][0]["metadata"]["record_evidence_ids"]
        assert brief["verified_claims"] == []
        return completion(purported_finding)  # One repair receives the same unread scope, never forged evidence.

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(rows, policy, review, mode="multi",
                                   gateway=DeepSeekGateway("offline-key", client=client))
    assert len(requests) == 4 and result["usage"]["tools"] == 1
    assert result["status"] == "failed" and result["fact_status"] == "insufficient"
    assert result["error"]["code"] == "model_output"
    assert result["claims"] == result["proposed_claims"] == []
    errors = [step for step in result["steps"] if step["event"] == "output_validation_failed"]
    assert len(errors) == 2
    assert all({"code": "evidence_content_not_read", "path": ["claims", 0, "evidence_ids"]}
               in step["diagnostics"] for step in errors)


@pytest.mark.asyncio
async def test_neutral_review_after_source_read_allows_proposal_but_never_creates_a_program_fact(scope):
    rows, policy, _ = deepcopy(scope)
    before = deepcopy((rows, policy))
    review = {"task_type": "review", "record_ids": [], "evidence_ids": [], "missing_evidence": []}
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body["messages"][1]["content"])
        if len(requests) == 1:
            return tool_completion("query_journal_lines", {})
        assert [row["debit"] for row in brief["shared_evidence"]["records"]] == ["100.00", "0.00"]
        return completion({
            "status": "supported", "claims": [{"text": "读取原文后的候选结论仍需计算与业务复核",
                "evidence_ids": ["lazy-evidence-1", "lazy-evidence-2"], "rule_id": "VOUCHER_BALANCE",
                "assessment": "finding", "calculation": {"difference": "10.00"}}],
            "hypotheses": [], "missing_evidence": [],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(rows, policy, review, mode="single",
                                   gateway=DeepSeekGateway("offline-key", client=client))
    assert len(requests) == 2 and result["error"] is None
    assert result["status"] == result["fact_status"] == "insufficient" and result["claims"] == []
    assert result["proposals"][0]["status_is_model_proposal"] is True
    proposed = result["proposed_claims"][0]
    assert proposed["origin"] == "model_proposal" and proposed["verification"]["citations_valid"] is True
    assert proposed["verification"]["calculations_verified"] is False
    assert proposed["verification"]["semantic_status"] == "unverified_model_proposal"
    assert (rows, policy) == before


@pytest.mark.parametrize("assessment", ["finding", "normal", "insufficient", "unsupported"])
def test_unread_claims_must_preserve_judgment_limits_even_with_valid_snapshot_citations(scope, assessment):
    context = make_context(scope)
    payload = {"status": "insufficient", "claims": [{"text": "引用已存在，但没有读取原文",
        "evidence_ids": ["lazy-evidence-1"], "rule_id": "VOUCHER_BALANCE", "assessment": assessment}],
        "hypotheses": [], "missing_evidence": []}
    if assessment in {"finding", "normal"}:
        with pytest.raises(ProposalRejected) as rejected:
            validate_proposal(json.dumps(payload), context, read_evidence_ids=set(), read_reference_ids=set())
        assert rejected.value.diagnostics == [
            {"code": "evidence_content_not_read", "path": ["claims", 0, "evidence_ids"]}]
    else:
        accepted = validate_proposal(json.dumps(payload), context,
                                     read_evidence_ids=set(), read_reference_ids=set())
        assert accepted.claims[0].assessment == assessment


@pytest.mark.parametrize("hypothesis_status", ["supported", "refuted", "unresolved"])
@pytest.mark.parametrize("cited", [[], ["lazy-evidence-1"]])
def test_definitive_hypotheses_require_read_source_content_and_unresolved_hypotheses_can_wait(scope,
                                                                                              hypothesis_status, cited):
    context = make_context(scope)
    payload = {"status": "insufficient", "claims": [], "hypotheses": [{
        "explanation": "仍需结合原始资料判断", "status": hypothesis_status, "evidence_ids": cited,
        "missing_evidence": ["原始资料"]}], "missing_evidence": []}
    if hypothesis_status != "unresolved":
        with pytest.raises(ProposalRejected) as rejected:
            validate_proposal(json.dumps(payload), context, read_evidence_ids=set(), read_reference_ids=set())
        assert rejected.value.diagnostics == [
            {"code": "evidence_content_not_read", "path": ["hypotheses", 0, "evidence_ids"]}]
    else:
        accepted = validate_proposal(json.dumps(payload), context,
                                     read_evidence_ids=set(), read_reference_ids=set())
        assert accepted.hypotheses[0].status == "unresolved"


def test_unread_policy_passage_cannot_be_cited_merely_because_the_source_id_exists(scope):
    context = make_context(scope)
    payload = {"status": "insufficient", "claims": [{"text": "政策依据需先读取全文",
        "evidence_ids": ["lazy-evidence-1"], "rule_id": "VOUCHER_BALANCE", "assessment": "insufficient",
        "policy_source_ids": ["lazy-policy-0"]}], "hypotheses": [], "missing_evidence": []}
    assert "lazy-policy-0" in context.reference_ids
    with pytest.raises(ProposalRejected) as rejected:
        validate_proposal(json.dumps(payload), context, read_evidence_ids=set(), read_reference_ids=set())
    assert rejected.value.diagnostics == [
        {"code": "policy_content_not_read", "path": ["claims", 0, "policy_source_ids"]}]
    accepted = validate_proposal(json.dumps(payload), context,
                                 read_evidence_ids=set(), read_reference_ids={"lazy-policy-0"})
    assert accepted.claims[0].policy_source_ids == ["lazy-policy-0"]
