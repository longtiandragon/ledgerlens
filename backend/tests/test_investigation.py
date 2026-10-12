import json
from copy import deepcopy

import httpx
import pytest
from app.context_transport import deserialize_context
from app.domain import check_records
from app.investigation import ToolContext, investigate
from app.model_gateway import DeepSeekGateway, RunBudget
from langgraph.checkpoint.memory import InMemorySaver


@pytest.fixture
def ledger():
    policy = {"framework": "CAS", "version": "v1", "period": "2026-09", "book_id": "book-a",
              "accounts": ["1403", "2202"], "snapshot_cutoff": "2026-09-30T23:59:59Z"}
    common = {"currency": "CNY", "book_id": "book-a", "business_date": "2026-09-01", "period": "2026-09",
              "observed_at": "2026-09-02T00:00:00Z", "counterparty": "Sensitive company name"}
    records = [{**common, "id": "row-1", "evidence_id": "ev-1", "source_id": "s1", "kind": "journal",
                "voucher_id": "JV1", "account": "1403", "debit": "100.00", "credit": "0.00"},
               {**common, "id": "row-2", "evidence_id": "ev-2", "source_id": "s1", "kind": "journal",
                "voucher_id": "JV1", "account": "2202", "debit": "0.00", "credit": "90.00"}]
    return records, policy, check_records(records, policy)[0]


def response(content):
    return httpx.Response(200, json={"model": "mock-deepseek", "usage": {"total_tokens": 50},
           "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content)}}]})


def proposal(**overrides):
    return {"status": "normal", "claims": [{"text": "模型建议，尚未确定", "evidence_ids": ["ev-1"],
            "rule_id": "VOUCHER_BALANCE", "calculation": {}}], "hypotheses": [], "missing_evidence": [], **overrides}


@pytest.mark.asyncio
async def test_rules_recompute_and_never_mutate(ledger):
    records, policy, finding = ledger
    original = deepcopy(records)
    finding["explanation"] = "An attacker changed this explanation"
    result = await investigate(records, policy, finding)
    assert result["status"] == "supported"
    assert result["usage"]["calls"] == 0
    assert result["model"] is None
    assert result["claims"][0]["calculation"]["difference"] == "10.00"
    assert "attacker" not in result["claims"][0]["text"]
    assert records == original


@pytest.mark.asyncio
async def test_missing_key_failure_preserves_rule_facts(ledger):
    result = await investigate(*ledger, mode="single")
    assert result["status"] == "failed"
    assert result["fact_status"] == "supported"
    assert result["claims"]
    assert result["error"]["code"] == "model_configuration"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,count", [("single", 1), ("multi", 5), ("active", 3)])
async def test_modes_have_distinct_real_orchestration(ledger, mode, count):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(proposal()))) as client:
        result = await investigate(*ledger, mode=mode, gateway=DeepSeekGateway("test", client=client))
    assert result["usage"]["calls"] == count
    assert len(result["proposals"]) == count
    assert result["status"] == "supported"  # model's "normal" cannot change program fact
    assert all(c["origin"] == "model_proposal" for c in result["proposed_claims"])
    assert result["model"] == "mock-deepseek"
    if mode == "active":
        assert [p["role"] for p in result["proposals"]] == ["coordinator", "evidence", "critic"]


@pytest.mark.asyncio
async def test_fake_citations_rejected_after_only_one_repair(ledger):
    fake = proposal(claims=[{"text": "invented", "evidence_ids": ["outside-snapshot"],
                           "rule_id": "VOUCHER_BALANCE", "calculation": {}}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(fake))) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "failed"
    assert result["error"]["code"] == "model_output"
    assert result["usage"]["calls"] == 2
    assert result["proposed_claims"] == []


@pytest.mark.asyncio
async def test_invalid_shape_repair_uses_fresh_context_without_rejected_reasoning(ledger):
    messages = []
    def handler(request):
        messages.append(json.loads(request.content))
        if len(messages) == 1:
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 10}, "choices": [{
                "finish_reason": "stop", "message": {"role": "assistant", "content": '{"invalid":true}',
                                                       "reasoning_content": "PRIVATE_CHAIN"}}]})
        return response(proposal())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "supported"
    assert result["usage"]["calls"] == 2
    assert [m['role'] for m in messages[1]['messages']] == ['system', 'user', 'user']
    assert 'PRIVATE_CHAIN' not in json.dumps(messages[1])
    assert messages[1]['thinking'] == {'type': 'disabled'} and 'tools' not in messages[1]
    assert "PRIVATE_CHAIN" not in json.dumps(result)


def test_tools_reject_cross_book_arbitrary_parameters_and_fake_ids(ledger):
    records, policy, finding = ledger
    context = ToolContext(records, policy, finding, RunBudget())
    with pytest.raises(ValueError):
        context.execute("query_journal_lines", {"book_id": "other"})
    with pytest.raises(ValueError):
        context.execute("sql", {"query": "SELECT *"})
    output = context.execute("verify_claims", {"claims": [{"text": "bad", "evidence_ids": ["other"],
                                                          "rule_id": "VOUCHER_BALANCE"}]})
    assert output["claims"][0]["citations_valid"] is False
    safe = context.execute("query_journal_lines", {})
    assert safe["records"][0]["counterparty"].startswith("party-")
    assert "book_id" not in safe["records"][0]


def test_policy_tool_cannot_change_framework(ledger):
    records, policy, finding = ledger
    policy["framework"] = "SCAS"
    context = ToolContext(records, policy, finding, RunBudget())
    data = context.execute("retrieve_policy", {})
    assert {r["framework"] for r in data["rules"]} == {"SCAS"}
    with pytest.raises(ValueError):
        context.execute("retrieve_policy", {"framework": "CAS"})


def test_snapshot_rejects_future_and_crossbook_records(ledger):
    records, policy, finding = ledger
    future = deepcopy(records)
    future[0]["observed_at"] = "2026-10-01T00:00:00Z"
    with pytest.raises(ValueError, match="Future"):
        ToolContext(future, policy, finding, RunBudget())
    other = deepcopy(records)
    other[0]["book_id"] = "other"
    with pytest.raises(ValueError, match="Cross-book"):
        ToolContext(other, policy, finding, RunBudget())


def test_missing_evidence_requests_do_not_retrieve_or_change_snapshot(ledger):
    context = ToolContext(*ledger, budget=RunBudget())
    before = deepcopy(context.records)
    result = context.execute("request_evidence", {"description": "后续发票", "reason": "区分两种解释"})
    assert result["retrieved"] is False
    assert result["requires_new_snapshot"] is True
    assert context.records == before


@pytest.mark.asyncio
async def test_attack_tool_response_is_bounded_and_not_executed(ledger):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 12}, "choices": [{
                "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "reasoning_content": "SECRET_THOUGHT", "tool_calls": [{"id": "bad-tool", "type": "function",
                "function": {"name": "run_shell", "arguments": '{"cmd":"write secret"}'}}]}}]})
        return response(proposal())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["usage"]["tools"] == 1
    assert deserialize_context(calls[1]["messages"][1]["content"])["recent_tool_results"][0]["output"]["error"] == "invalid_tool_or_arguments"
    assert "SECRET_THOUGHT" not in json.dumps(result)
    assert "write secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_restored_exhausted_budget_does_not_restart(ledger):
    result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test"),
                               checkpoint_config={"usage": {"calls": 24}})
    assert result["status"] == "budget_exhausted"
    assert result["usage"]["calls"] == 24
    assert result["claims"]


@pytest.mark.asyncio
async def test_persistent_checkpoint_returns_completed_result_and_rejects_new_snapshot(ledger):
    saver = InMemorySaver()  # test-only; application supplies AsyncPostgresSaver
    config = {"checkpointer": saver, "config": {"configurable": {"thread_id": "case-v1"}}}
    first = await investigate(*ledger, checkpoint_config=config)
    second = await investigate(*ledger, checkpoint_config={**config, "resume": True})
    assert second == first
    changed = deepcopy(ledger)
    changed[1]["version"] = "v2"
    with pytest.raises(ValueError, match="different snapshot"):
        await investigate(*changed, checkpoint_config={**config, "resume": True})


@pytest.mark.asyncio
async def test_resume_requires_checkpointer(ledger):
    with pytest.raises(ValueError, match="persistent checkpointer"):
        await investigate(*ledger, checkpoint_config={"resume": True})


@pytest.mark.asyncio
async def test_crash_after_billed_call_preserves_usage_on_resume(ledger):
    class WorkerKilled(BaseException):
        pass

    saver = InMemorySaver()
    persisted = {}
    config = {"checkpointer": saver, "config": {"configurable": {"thread_id": "crash-case"}}}

    def crash(step):
        persisted.update(step["usage"])
        if step["event"] == "model_usage":
            raise WorkerKilled()

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(proposal()))) as client:
        gateway = DeepSeekGateway("test", client=client)
        with pytest.raises(WorkerKilled):
            await investigate(*ledger, mode="single", gateway=gateway, on_step=crash, checkpoint_config=config)
        assert persisted["calls"] == 1
        result = await investigate(*ledger, mode="single", gateway=gateway,
                                   checkpoint_config={**config, "resume": True, "usage": persisted})
    assert result["status"] == "supported"
    assert result["usage"]["calls"] == 2
    assert result["usage"]["tokens"] == 100
    assert len(result["proposals"]) == 1


@pytest.mark.asyncio
async def test_identical_tools_and_global_caps_across_modes(ledger):
    snapshots = {}
    for mode in ["single", "multi", "active"]:
        def handler(request, selected_mode=mode):
            body = json.loads(request.content)
            snapshots[selected_mode] = body.get("tools") or deserialize_context(body["messages"][1]["content"])["tool_contracts"]
            return response(proposal())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await investigate(*ledger, mode=mode, gateway=DeepSeekGateway("test", client=client),
                                       checkpoint_config={"usage": {"calls": 23}})
        assert result["usage"]["calls"] == 24
        assert result["status"] == ("supported" if mode == "single" else "budget_exhausted")
    assert snapshots["single"] == snapshots["multi"] == snapshots["active"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "multi"])
async def test_first_role_cannot_keep_retrieving_after_two_batches(ledger, mode):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls <= 7:
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 10}, "choices": [{
                "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
                    "id": f"query-{calls}", "type": "function", "function": {
                        "name": "query_journal_lines", "arguments": json.dumps({"account": str(calls)})}}]}}]})
        return response(proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode=mode, gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "failed" and result["error"]["code"] == "model_no_progress"
    assert result["usage"]["calls"] == 3 and result["usage"]["tools"] == 2
    assert result["claims"] and result["proposed_claims"] == []
    assert result["evidence_cache"]["queries"] == 2


@pytest.mark.asyncio
async def test_final_report_receives_all_specialist_opinions_and_full_history_index(ledger):
    briefs = []

    def handler(request):
        payload = json.loads(request.content)
        briefs.append(deserialize_context(payload["messages"][1]["content"]))
        assert "Specialists" in payload["messages"][0]["content"]
        return response(proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="multi", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "supported"
    assert [len(brief["prior_proposals"]) for brief in briefs] == [0, 1, 1, 1, 4]
    assert [len(brief["prior_proposal_index"]) for brief in briefs] == [0, 1, 2, 3, 4]
    assert all("role" not in p for p in briefs[-1]["prior_proposals"])
    assert all("verification" not in c and "origin" not in c
               for p in briefs[-1]["prior_proposals"] for c in p["claims"])


def test_empty_calculation_does_not_claim_program_verification(ledger):
    context = ToolContext(*ledger, budget=RunBudget())
    check = context.verify(proposal()["claims"])["claims"][0]
    assert check["citations_valid"] is True
    assert check["calculations_verified"] is None
    assert check["calculation_status"] == "not_requested"


@pytest.mark.asyncio
async def test_repair_diagnostics_identify_wrong_id_field_without_exposing_values(ledger):
    calls = 0
    requests = []

    def handler(request):
        nonlocal calls
        calls += 1
        requests.append(json.loads(request.content))
        # A record id is not an evidence id, even though it exists in this book.
        return response(proposal(claims=[{"text": "wrong identifier", "evidence_ids": ["row-1"],
                                         "rule_id": "VOUCHER_BALANCE", "calculation": {}}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert calls == 2 and result["status"] == "failed"
    errors = [s for s in result["steps"] if s["event"] == "output_validation_failed"]
    assert errors[0]["diagnostics"] == [{"code": "citation_outside_snapshot", "path": ["claims", 0, "evidence_ids"]}]
    assert "row-1" not in json.dumps(errors)
    assert "citation_outside_snapshot" in requests[1]["messages"][-1]["content"]


def test_schema_diagnostics_strip_untrusted_extra_keys_and_calculation_values(ledger):
    from app.investigation import ProposalRejected, validate_proposal
    context = ToolContext(*ledger, budget=RunBudget())
    payload = proposal(claims=[{"text": "test", "evidence_ids": ["ev-1"], "rule_id": "VOUCHER_BALANCE",
                               "calculation": {"UNTRUSTED_KEY": ["UNTRUSTED_VALUE"]},
                               "UNTRUSTED_FIELD": "PRIVATE_VALUE"}])
    with pytest.raises(ProposalRejected) as error:
        validate_proposal(json.dumps(payload), context)
    diagnostics = json.dumps(error.value.diagnostics)
    assert "UNTRUSTED" not in diagnostics and "PRIVATE_VALUE" not in diagnostics
    assert "string_type" in diagnostics and "extra_forbidden" in diagnostics


def test_snapshot_memory_deduplicates_query_and_overlapping_records(ledger):
    from app.investigation import EvidenceMemory
    context = ToolContext(*ledger, budget=RunBudget())
    memory = EvidenceMemory()
    first = memory.execute(context, "query_journal_lines", {})
    assert [r["evidence_id"] for r in first["new_records"]] == ["ev-1", "ev-2"]
    assert first["record_evidence_ids"] == ["ev-1", "ev-2"]
    # Omitted vs explicit default arguments identify the same query.
    repeated = memory.execute(context, "query_journal_lines", {"limit": 50, "account": None})
    assert repeated["cache_hit"] is True
    assert repeated["record_evidence_ids"] == ["ev-1", "ev-2"]
    assert "new_records" not in repeated
    overlap = memory.execute(context, "query_journal_lines", {"account": "1403"})
    assert overlap["new_records"] == [] and overlap["record_evidence_ids"] == ["ev-1"]
    restored = EvidenceMemory(memory.stored())
    assert len(restored.brief()["records"]) == 2
    assert restored.execute(context, "query_journal_lines", {})["cache_hit"] is True


@pytest.mark.asyncio
async def test_roles_share_only_returned_snapshot_evidence_and_cache_repeated_query(ledger):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if not deserialize_context(body["messages"][1]["content"]).get("current_role_tool_turns"):
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 10}, "choices": [{
                "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
                    "id": "q1", "type": "function", "function": {"name": "query_journal_lines", "arguments": "{}"}}]}}]})
        return response(proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="multi", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "supported" and result["usage"]["calls"] == 10
    first_brief = deserialize_context(requests[0]["messages"][1]["content"])
    second_role_brief = deserialize_context(requests[2]["messages"][1]["content"])
    assert first_brief["shared_evidence"]["records"] == []
    assert second_role_brief["shared_evidence"]["records"] == []
    manifest = second_role_brief["shared_evidence"]["query_manifest"]
    assert manifest[0]["metadata"]["record_evidence_ids"] == ["ev-1", "ev-2"]
    assert manifest[0]["payload_in_view"] is False
    assert {r["evidence_id"] for r in deserialize_context(requests[3]["messages"][1]["content"])
            ["shared_evidence"]["records"]} == {"ev-1", "ev-2"}
    assert deserialize_context(requests[3]["messages"][1]["content"])["shared_evidence"]["query_results"][0]["result"]["record_evidence_ids"] == ["ev-1", "ev-2"]
    assert result["evidence_cache"] == {"records": 2, "queries": 1, "hits": 4}


@pytest.mark.asyncio
async def test_model_configuration_changes_checkpoint_fingerprint(ledger):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(proposal()))) as client:
        first = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", model="model-a", client=client))
        second = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", model="model-b", client=client))
    assert first["fingerprint"] != second["fingerprint"]
    assert first["prompt_version"] == "investigation-v26"
    assert first["model_parameters"] == {"thinking": True, "max_tokens": 8192,
                                         "reasoning_effort": "low", "temperature": None,
                                         "truncation_recovery": {"maximum": 1, "thinking": False, "tools": False},
                                         "active_coordinator": "planning_only", "prior_context": "specialist_latest_synthesis_all_readable_history",
                                         "conversation_state": "role_selected_queries_with_complete_manifest",
                                         "program_observation_context": "cumulative_only",
                                         "role_tool_batch_limit": 2,
                                         "tool_stage_finalization": {"thinking": False, "tools": False},
                                         "policy_reference_memory": "source_id_deduplicated",
                                         "policy_reference_retrieval": "explicit_query",
                                         "policy_reference_provenance": "frozen_source_binding_v2_runtime_gated",
                                         "policy_check_context": "run_frozen_saved_checks_v1",
                                         "rule_context": "lossless_definition_pool",
                                         "context_encoding": "ledger-context-v1",
                                         "review_task_initialization": "neutral_without_presumed_anomaly",
                                         "proposal_rule_ids": "current_framework_enum",
                                         "schema_repair_conversation": "fresh_scoped_program_context",
                                         "schema_encoding": "constraints_without_annotations",
                                         "root_cause_output": "narrow_assessment_v1",
                                         "role_outputs": "specialist_opinions_final_synthesis",
                                         "evidence_retrieval": "snapshot_bound_cached_queries_max_6_per_read",
                                         "plan_repair_context": "validated_tool_contracts_and_field_diagnostics",
                                         "program_observations": {"maximum": 60, "replayed": True}}


@pytest.mark.asyncio
async def test_invalid_tool_json_still_consumes_budget(ledger):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 3}, "choices": [{
                "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
                    "id": "t1", "type": "function", "function": {
                        "name": "query_journal_lines", "arguments": "NaN"}}]}}]})
        return response(proposal())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["usage"]["tools"] == 1
    assert result["status"] == "supported"


@pytest.mark.asyncio
async def test_unknown_model_fields_do_not_sneak_into_proposals(ledger):
    invalid = proposal()
    invalid["execute_shell"] = "injected command"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(invalid))) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["error"]["code"] == "model_output"
    assert "injected command" not in json.dumps(result)


@pytest.mark.asyncio
async def test_ablation_requires_synthetic_gate_and_is_fingerprinted(ledger):
    with pytest.raises(ValueError, match="synthetic evaluation"):
        await investigate(*ledger, mode="active", checkpoint_config={"ablation": "no_counterproof"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(proposal()))) as client:
        gateway = DeepSeekGateway("test", client=client)
        normal = await investigate(*ledger, mode="active", gateway=gateway)
        ablated = await investigate(*ledger, mode="active", gateway=gateway,
                                    checkpoint_config={"ablation": "no_counterproof", "synthetic_evaluation": True})
    assert [p["role"] for p in normal["proposals"]] == ["coordinator", "evidence", "critic"]
    assert [p["role"] for p in ablated["proposals"]] == ["coordinator", "evidence", "report"]
    assert normal["fingerprint"] != ablated["fingerprint"]
    assert ablated["evaluation_only"] is True


@pytest.mark.asyncio
async def test_no_time_view_only_adds_explicit_simulated_future_records(ledger):
    records, policy, finding = ledger
    cfg = {"ablation": "no_time_view", "synthetic_evaluation": True}
    with pytest.raises(ValueError, match="comparison_records"):
        await investigate(*ledger, checkpoint_config=cfg)
    later = {**records[0], "id": "later", "evidence_id": "later-ev", "observed_at": "2026-10-01T00:00:00Z",
             "voucher_id": "JV2"}
    result = await investigate(*ledger, checkpoint_config={**cfg, "comparison_records": [*records, later]})
    assert result["evaluation_only"] is True
    assert result["ablation"] == "no_time_view"
    assert records[0]["observed_at"] == "2026-09-02T00:00:00Z"
    changed = deepcopy(records)
    changed[0]["debit"] = "999.00"
    with pytest.raises(ValueError, match="rewrite"):
        await investigate(records, policy, finding, checkpoint_config={**cfg, "comparison_records": [*changed, later]})


@pytest.mark.asyncio
async def test_no_program_verification_is_explicit_and_does_not_upgrade_claims(ledger):
    candidate = proposal(claims=[{"text": "wrong calculation", "evidence_ids": ["ev-1"],
                                  "rule_id": "VOUCHER_BALANCE", "calculation": {"difference": "999.00"}}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(candidate))) as client:
        gateway = DeepSeekGateway("test", client=client)
        normal = await investigate(*ledger, mode="single", gateway=gateway)
        ablated = await investigate(*ledger, mode="single", gateway=gateway,
                                    checkpoint_config={"ablation": "no_program_verification", "synthetic_evaluation": True})
    assert normal["proposed_claims"][0]["verification"]["calculations_verified"] is False
    assert ablated["proposed_claims"][0]["verification"]["calculations_verified"] is None
    assert ablated["claims"][0]["calculation"]["difference"] == "10.00"


def test_policy_passages_are_separate_applicable_and_non_executable(ledger):
    records, policy, finding = ledger
    base = {"source_id": "p1", "text": "政策示例", "url": "https://example.org/policy", "framework": "CAS"}
    policy["reference_passages"] = [base, {**base, "source_id": "other", "framework": "SCAS"},
                                     {**base, "source_id": "future", "effective_from": "2027-01-01"}]
    context = ToolContext(records, policy, finding, RunBudget())
    result = context.execute("retrieve_policy", {"query": "政策"})
    assert [p["source_id"] for p in result["reference_passages"]] == ["p1"]
    assert "p1" not in context.evidence_ids
    assert "p1" not in context.rule_ids
    with pytest.raises(ValueError):
        context.execute("retrieve_policy", {"sql": "SELECT *"})


@pytest.mark.asyncio
async def test_inapplicable_policy_citation_is_rejected(ledger):
    candidate = proposal(claims=[{"text": "claim", "evidence_ids": ["ev-1"],
                                 "rule_id": "VOUCHER_BALANCE", "policy_source_ids": ["made-up-policy"]}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(candidate))) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "failed"
    assert result["error"]["code"] == "model_output"


@pytest.mark.asyncio
async def test_disk_checkpointer_survives_connection_restart(ledger, tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    filename = str(tmp_path / "checkpoints.sqlite")
    config = {"configurable": {"thread_id": "disk-case"}}
    async with AsyncSqliteSaver.from_conn_string(filename) as saver:
        await saver.setup()
        first = await investigate(*ledger, checkpoint_config={"checkpointer": saver, "config": config})
    async with AsyncSqliteSaver.from_conn_string(filename) as reopened:
        second = await investigate(*ledger, checkpoint_config={"checkpointer": reopened, "config": config, "resume": True})
    assert first == second


def test_model_cannot_disable_verification_via_tool_args(ledger):
    context = ToolContext(*ledger, budget=RunBudget())
    with pytest.raises(ValueError):
        context.execute("verify_claims", {"claims": [], "ablation": "no_program_verification"})
    with pytest.raises(ValueError):
        context.execute("match_transactions", {"flow": "anything"})


@pytest.mark.asyncio
async def test_repeated_query_stops_tools_and_requests_a_conclusion(ledger):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) <= 2:
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 10}, "choices": [{
                "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
                    "id": f"query-{len(requests)}", "type": "function", "function": {
                        "name": "query_journal_lines", "arguments": "{}"}}]}}]})
        return response(proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "supported"
    assert result["usage"]["calls"] == 3 and result["usage"]["tools"] == 2
    assert "tools" not in requests[-1]
    assert result["evidence_cache"]["queries"] == 1
    assert sum(s["event"] == "evidence_stagnation" for s in result["steps"]) == 1


@pytest.mark.asyncio
async def test_no_tools_turn_rejects_more_tool_requests_without_execution(ledger):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 10}, "choices": [{
            "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
                "id": f"query-{len(requests)}", "type": "function", "function": {
                    "name": "query_journal_lines", "arguments": "{}"}}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "failed" and result["error"]["code"] == "model_no_progress"
    assert result["usage"]["tools"] == 2
    assert result["claims"]  # deterministic findings survive failure


def test_policy_directory_is_compact_but_targeted_rule_retains_sources(ledger):
    context = ToolContext(*ledger, budget=RunBudget())
    directory = context.execute("retrieve_policy", {})
    details = context.execute("retrieve_policy", {"rule_id": "VOUCHER_BALANCE"})
    assert directory["rules_are_summaries"] is True
    assert details["rules_are_summaries"] is False
    assert all("source_sha256" not in r and "source_url" not in r for r in directory["rules"])
    assert details["rules"][0]["source_url"] and details["rules"][0]["source_sha256"]
    assert {r["rule_id"] for r in directory["rules"]} == context.rule_ids


@pytest.mark.asyncio
async def test_full_prior_report_can_be_read_without_republishing_it_in_default_cache(ledger):
    from app.investigation import EvidenceMemory
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        brief = deserialize_context(body['messages'][1]['content'])
        if len(brief['prior_proposal_index']) == 4 and not brief.get('current_role_tool_turns'):
            return httpx.Response(200, json={'model': 'mock', 'usage': {'total_tokens': 10}, 'choices': [{
                'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'tool_calls': [{
                    'id': 'history', 'type': 'function', 'function': {
                        'name': 'read_prior_proposal', 'arguments': '{"index":0}'}}]}}]})
        return response(proposal(claims=[{'text': f'role-{len(brief["prior_proposal_index"])}',
                                         'evidence_ids': ['ev-1'], 'rule_id': 'VOUCHER_BALANCE'}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode='multi', gateway=DeepSeekGateway('test', client=client))
    assert result['status'] == 'supported' and len(result['proposals']) == 5
    history = deserialize_context(requests[-1]['messages'][1]['content'])['recent_tool_results'][0]['output']
    assert history['proposal']['claims'][0]['text'] == 'role-0'
    assert 'origin' not in history['proposal']['claims'][0] and 'verification' not in history['proposal']['claims'][0]
    context = ToolContext(*ledger, budget=RunBudget())
    context.prior_proposals = result['proposals']
    memory = EvidenceMemory()
    memory.execute(context, 'read_prior_proposal', {'index': 0})
    assert 'role-0' not in json.dumps(memory.brief())
    assert memory.execute(context, 'read_prior_proposal', {'index': 0})['proposal']['claims'][0]['text'] == 'role-0'
    with pytest.raises(ValueError):
        context.execute('read_prior_proposal', {'index': 0, 'book_id': 'foreign'})


@pytest.mark.asyncio
async def test_active_selector_executes_scoped_query_and_shares_actual_evidence(ledger):
    requests = []
    plan = {"hypotheses": [{"hypothesis_id": "normal", "explanation": "正常记账"},
                            {"hypothesis_id": "amount_error", "explanation": "金额录错"}],
            "queries": [{"query_id": "check-voucher", "tool": "query_journal_lines",
                         "arguments": {"voucher_id": "JV1"}, "predictions": {
                             "normal": {"status": "predicted", "label": "借贷相等"},
                             "amount_error": {"status": "predicted", "label": "借贷不等"}}}]}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return response(proposal(evidence_plan=plan, hypotheses=[{
                "explanation": "原因需查凭证", "status": "unresolved", "evidence_ids": [], "missing_evidence": []}]))
        return response(proposal())

    before = deepcopy(ledger[0])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="active", gateway=DeepSeekGateway("test", client=client))
    assert ledger[0] == before
    assert result["status"] == "supported" and result["usage"]["tools"] == 1
    selection = result["evidence_selections"][0]
    assert selection["selected_query"]["pair_count"] == 1
    assert selection["selected_query"]["cost_units"] == 1
    assert selection["execution"]["performed"] is True
    assert selection["execution"]["record_evidence_ids"] == ["ev-1", "ev-2"]
    actual_brief = deserialize_context(requests[1]["messages"][1]["content"])
    assert {r["evidence_id"] for r in actual_brief["shared_evidence"]["records"]} == {"ev-1", "ev-2"}
    assert result["evidence_cache"]["queries"] == 1
    assert result["proposed_claims"][0]["origin"] == "model_proposal"


@pytest.mark.asyncio
async def test_active_unknown_predictions_register_request_without_obtaining_future_data(ledger):
    plan = {"hypotheses": [{"hypothesis_id": "a", "explanation": "解释A"},
                            {"hypothesis_id": "b", "explanation": "解释B"}],
            "queries": [{"query_id": "query", "tool": "query_journal_lines", "arguments": {},
                         "predictions": {"a": {"status": "unknown", "label": None},
                                         "b": {"status": "unknown", "label": None}}}]}
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return response(proposal(evidence_plan=plan) if calls == 1 else proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="active", gateway=DeepSeekGateway("test", client=client))
    selection = result["evidence_selections"][0]
    assert selection["selection"]["status"] == "insufficient"
    assert selection["execution"]["retrieved"] is False
    assert selection["execution"]["requires_new_snapshot"] is True
    assert result["evidence_cache"]["records"] == 0


@pytest.mark.asyncio
async def test_active_invalid_executable_plan_is_rejected_before_query(ledger):
    plan = {"hypotheses": [], "queries": [{"query_id": "bad", "tool": "run_shell",
                                           "arguments": {"secret": "DO_NOT_ECHO"}, "predictions": {}}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(proposal(evidence_plan=plan)))) as client:
        result = await investigate(*ledger, mode="active", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "failed" and result["error"]["code"] == "model_output"
    assert result["usage"]["tools"] == 0
    assert "DO_NOT_ECHO" not in json.dumps(result)
    assert any(e.get("diagnostics", [{}])[0].get("code") == "tool_not_whitelisted" for e in result["steps"])


@pytest.mark.asyncio
async def test_active_plan_repair_has_executable_contracts_and_precise_safe_diagnostics(ledger):
    requests = []
    malformed = {'hypotheses': [], 'queries': [{'query_id': 'bad', 'tool': 'query_journal_lines',
                  'arguments': {'voucher_id': ['PRIVATE_ARGUMENT']}, 'predictions': {}}]}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return response(proposal(evidence_plan=malformed) if len(requests) == 1 else proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode='active', gateway=DeepSeekGateway('test', client=client))
    assert result['status'] == 'supported' and result['usage']['calls'] == 4
    assert result['usage']['tools'] == 0
    failure = next(step for step in result['steps'] if step['event'] == 'output_validation_failed')
    assert failure['diagnostics'] == [{'code': 'invalid_tool_arguments',
          'path': ['evidence_plan', 'queries', 0, 'arguments', 'voucher_id'], 'argument_error': 'string_type'}]
    repair = requests[1]['messages'][-1]['content']
    assert 'tool_contracts=' in repair and 'query_journal_lines' in repair and 'voucher_id' in repair
    assert 'PRIVATE_ARGUMENT' not in repair and 'PRIVATE_ARGUMENT' not in json.dumps(result)


@pytest.mark.asyncio
async def test_active_invalid_arguments_remain_failure_after_one_repair(ledger):
    requests = []
    malformed = {'hypotheses': [], 'queries': [{'query_id': 'bad', 'tool': 'query_journal_lines',
                                              'arguments': {'limit': 0}, 'predictions': {}}]}

    def handler(request):
        requests.append(request)
        return response(proposal(evidence_plan=malformed))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode='active', gateway=DeepSeekGateway('test', client=client))
    assert len(requests) == 2 and result['error']['code'] == 'model_output'
    assert result['usage']['tools'] == 0 and not result['proposals']


@pytest.mark.asyncio
async def test_truncated_thinking_is_discarded_and_one_bounded_json_pass_is_verified(ledger):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 50}, "choices": [{
                "finish_reason": "length", "message": {"role": "assistant", "content": "UNACCEPTED_PARTIAL",
                                                        "reasoning_content": "PRIVATE_TRUNCATED_THOUGHT"}}]})
        return response(proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["status"] == "supported"
    assert result["usage"]["calls"] == 2 and result["usage"]["tokens"] == 100
    assert requests[-1]["thinking"] == {"type": "disabled"}
    assert "tools" not in requests[-1] and "reasoning_effort" not in requests[-1]
    assert "UNACCEPTED_PARTIAL" not in json.dumps(result)
    assert "PRIVATE_TRUNCATED_THOUGHT" not in json.dumps(requests)
    assert result["proposals"][0]["truncation_recovery_used"] is True


@pytest.mark.asyncio
async def test_truncation_recovery_does_not_retry_twice_or_accept_invalid_citations(ledger):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 50}, "choices": [{
            "finish_reason": "length", "message": {"role": "assistant", "content": "PARTIAL"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert len(attempts) == 2 and result["error"]["code"] == "model_truncated"
    assert result["proposed_claims"] == []

    def invalid_handler(request):
        if json.loads(request.content)["thinking"]["type"] == "enabled":
            return httpx.Response(200, json={"model": "mock", "usage": {"total_tokens": 50}, "choices": [{
                "finish_reason": "length", "message": {"role": "assistant", "content": "PARTIAL"}}]})
        return response(proposal(claims=[{"text": "fake", "evidence_ids": ["other-book"], "rule_id": "VOUCHER_BALANCE"}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_handler)) as client:
        result = await investigate(*ledger, mode="single", gateway=DeepSeekGateway("test", client=client))
    assert result["error"]["code"] == "model_output" and result["usage"]["calls"] == 3
    assert result["proposed_claims"] == []
