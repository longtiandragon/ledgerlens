"""Independent, observable fixtures for active graph safety and recovery.

No dataset generator, hidden answer directory, external model, or live backend
is used. MockTransport exercises the production gateway/tool graph itself.
"""
import json
from copy import deepcopy

import httpx
import pytest
from app.context_transport import deserialize_context
from langgraph.checkpoint.memory import InMemorySaver

from app.domain import check_records
from app.investigation import investigate, tool_schemas, ToolContext
from app.model_gateway import DeepSeekGateway, RunBudget


def scoped_fixture(name="first"):
    book = f"book-{name}"
    policy = {"framework": "CAS", "version": "v1", "period": "2026-09", "book_id": book,
              "accounts": ["1403", "2202"], "snapshot_cutoff": "2026-09-30T23:59:59Z"}
    common = {"currency": "CNY", "book_id": book, "business_date": "2026-09-01", "period": "2026-09",
              "observed_at": "2026-09-02T00:00:00Z", "voucher_id": f"JV-{name}"}
    rows = [{**common, "id": f"{name}-r1", "evidence_id": f"{name}-e1", "source_id": f"{name}-s1",
             "kind": "journal", "account": "1403", "debit": "100.00", "credit": "0.00"},
            {**common, "id": f"{name}-r2", "evidence_id": f"{name}-e2", "source_id": f"{name}-s2",
             "kind": "journal", "account": "2202", "debit": "0.00", "credit": "90.00"}]
    finding = next(item for item in check_records(rows, policy) if item["rule_id"] == "VOUCHER_BALANCE")
    return rows, policy, finding


def proposed(scope="first", **fields):
    return {"status": "supported", "claims": [{"text": "Observed voucher difference awaits cause review",
            "evidence_ids": [f"{scope}-e1", f"{scope}-e2"], "rule_id": "VOUCHER_BALANCE", "calculation": {}}],
            "hypotheses": [], "missing_evidence": [], **fields}


def plan(*, alias="candidate", tool="query_journal_lines", arguments=None, unknown=False):
    return {"hypotheses": [{"hypothesis_id": "normal", "explanation": "Normal bookkeeping"},
                            {"hypothesis_id": "wrong_amount", "explanation": "One amount was mistyped"}],
            "queries": [{"query_id": alias, "tool": tool, "arguments": arguments or {}, "predictions": {
                "normal": {"status": "predicted", "label": "balanced"},
                "wrong_amount": {"status": "unknown", "label": None} if unknown else
                                {"status": "predicted", "label": "unbalanced"}}}]}


def completion(payload):
    return httpx.Response(200, json={"model": "mock-deepseek", "system_fingerprint": "fp_offline",
           "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}, "choices": [{
               "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(payload)}}]})


def tool_completion(name, arguments, *, identity="mock-tool", reasoning=None):
    return httpx.Response(200, json={"model": "mock-deepseek", "usage": {"total_tokens": 10}, "choices": [{
        "finish_reason": "tool_calls", "message": {"role": "assistant", "reasoning_content": reasoning, "tool_calls": [{
            "id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})


def planning_proposal(evidence_plan, *, unresolved=False):
    return {"status": "insufficient", "claims": [], "hypotheses": [{
        "explanation": "The cause remains unresolved pending source evidence", "status": "unresolved",
        "evidence_ids": [], "missing_evidence": ["Original source documentation"],
    }] if unresolved else [], "missing_evidence": ["Original source documentation"] if unresolved else [],
        "evidence_plan": evidence_plan}


@pytest.mark.asyncio
async def test_relabeling_cached_plan_query_cannot_repeat_acquisition():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        number = len(requests)
        evidence_plan = plan(alias="first-alias") if number == 1 else (
            plan(alias="fresh-alias", arguments={"limit": 50, "account": None}) if number == 2 else None
        )
        return completion(proposed(evidence_plan=evidence_plan))

    before = scoped_fixture()
    untouched = deepcopy(before)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*before, mode="active", gateway=DeepSeekGateway("offline-key", client=client))
    assert before == untouched
    assert result["status"] == "supported"
    assert result["usage"]["calls"] == 3 and result["usage"]["tools"] == 2
    first, second = result["evidence_selections"]
    assert first["execution"]["performed"] is True
    assert second["selected_query"] is None and second["execution"]["retrieved"] is False
    assert second["candidates"][0]["exclusion_reason"] == "already_executed"
    assert result["evidence_cache"]["queries"] == 1 and result["evidence_cache"]["records"] == 2
    critic_view = deserialize_context(requests[-1]["messages"][1]["content"])["shared_evidence"]
    assert critic_view["records"] == []  # No newly selected query; existing payload remains readable.
    assert critic_view["query_manifest"][0]["metadata"]["record_evidence_ids"] == ["first-e1", "first-e2"]
    assert critic_view["query_manifest"][0]["payload_in_view"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["model_cost", "model_availability", "illegal_args", "unknown_prediction"])
async def test_invalid_plan_repairs_once_then_fails_without_running_selector_tool(failure):
    candidate = plan()
    if failure == "model_cost":
        candidate["queries"][0]["cost_units"] = -100
    elif failure == "model_availability":
        candidate["queries"][0]["availability"] = "available"
    elif failure == "illegal_args":
        candidate["queries"][0]["arguments"] = {"book_id": "PRIVATE_OTHER_BOOK"}
    else:
        candidate["queries"][0]["predictions"]["PRIVATE_UNKNOWN_HYPOTHESIS"] = {"status": "predicted", "label": "x"}
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert "tools" not in requests[-1]  # planning repair does not gain execution rights
        return completion(proposed(evidence_plan=candidate))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client))
    assert result["status"] == "failed" and result["error"]["code"] == "model_output"
    assert result["usage"]["calls"] == 2 and result["usage"]["tools"] == 0
    assert result["evidence_selections"] == [] and result["evidence_cache"]["records"] == 0
    assert "PRIVATE" not in json.dumps(result)
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", [plan(unknown=True), plan(tool="get_business_chain"),
                                       plan(tool="match_transactions", arguments={"left_kind": "bank", "right_kind": "bank"})])
async def test_no_distinguishing_or_executable_plan_only_requests_evidence(candidate):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return completion(proposed(evidence_plan=candidate) if calls == 1 else proposed())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client))
    selection = result["evidence_selections"][0]
    assert selection["selection"]["status"] == "insufficient"
    assert selection["selected_query"] is None
    assert selection["execution"] == {"performed": False, "status": "evidence_requested",
                                      "requires_new_snapshot": True, "retrieved": False}
    assert result["usage"]["tools"] == 1 and result["evidence_cache"]["records"] == 0
    assert result["missing_evidence"]


@pytest.mark.asyncio
async def test_active_selected_query_cannot_bypass_exhausted_tool_budget():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return completion(proposed(evidence_plan=plan()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client),
                                   checkpoint_config={"usage": {"tools": 60}})
    assert calls == 1 and result["status"] == "budget_exhausted"
    assert result["error"]["code"] == "budget_exhausted"
    assert result["usage"]["tools"] == 60 and result["evidence_cache"]["queries"] == 0
    assert result["claims"] and result["evidence_selections"] == []


@pytest.mark.asyncio
async def test_no_active_selection_ablation_does_not_compile_or_execute_plan():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert "evidence_plan" not in payload["messages"][0]["content"]
        assert payload["tools"] == tool_schemas(ToolContext(*scoped_fixture(), budget=RunBudget()))
        assert "tool_contracts" not in deserialize_context(payload["messages"][1]["content"])
        return completion(proposed())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client),
                                   checkpoint_config={"ablation": "no_active_selection", "synthetic_evaluation": True})
    assert [item["role"] for item in result["proposals"]] == ["coordinator", "evidence", "critic"]
    assert result["usage"]["calls"] == 3 and result["usage"]["tools"] == 0
    assert result["evidence_selections"] == []
    assert result["evidence_cache"] == {"records": 0, "queries": 0, "hits": 0}
    assert result["ablation"] == "no_active_selection" and result["evaluation_only"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("second_scope", ["first", "second"])
async def test_existing_checkpoint_without_resume_never_starts_new_calls_or_transmits_old_cache(second_scope):
    saver = InMemorySaver()
    config = {"checkpointer": saver, "config": {"configurable": {"thread_id": "reused-caller-thread"}}}
    scopes, requests = ["first"], []
    per_run = {"first": 0, "second": 0}

    def handler(request):
        scope = scopes[-1]
        payload = json.loads(request.content)
        requests.append((scope, payload))
        per_run[scope] += 1
        return completion(proposed(scope, evidence_plan=plan() if per_run[scope] == 1 else None))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DeepSeekGateway("offline-key", client=client)
        first = await investigate(*scoped_fixture("first"), mode="active", gateway=gateway, checkpoint_config=config)
        assert first["evidence_cache"]["records"] == 2
        scopes.append(second_scope)
        billed_calls_before = len(requests)
        with pytest.raises(ValueError, match="already exists"):
            await investigate(*scoped_fixture(second_scope), mode="active", gateway=gateway, checkpoint_config=config)
        assert len(requests) == billed_calls_before
        resumed = await investigate(*scoped_fixture("first"), mode="active", gateway=gateway,
                                    checkpoint_config={**config, "resume": True})
    assert resumed == first
    assert resumed["evidence_cache"]["records"] == 2 and resumed["usage"]["tools"] == 1
    assert len(requests) == billed_calls_before


@pytest.mark.asyncio
async def test_changed_snapshot_resume_is_rejected_before_cache_is_shared_or_model_is_called():
    saver = InMemorySaver()
    config = {"checkpointer": saver, "config": {"configurable": {"thread_id": "fixed-snapshot-thread"}}}
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return completion(proposed(evidence_plan=plan()) if calls == 1 else proposed())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DeepSeekGateway("offline-key", client=client)
        first = await investigate(*scoped_fixture("first"), mode="active", gateway=gateway, checkpoint_config=config)
        assert first["evidence_cache"]["records"] == 2
        with pytest.raises(ValueError, match="different snapshot"):
            await investigate(*scoped_fixture("second"), mode="active", gateway=gateway,
                               checkpoint_config={**config, "resume": True})
    assert calls == 3


@pytest.mark.asyncio
async def test_distinct_empty_queries_stop_at_role_limit_and_forced_further_tool_is_rejected():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        number = len(requests)
        if number == 1:
            assert "tools" not in requests[-1]
            return completion(planning_proposal(plan()))
        if number < 4:
            assert requests[-1]["tools"] == tool_schemas(ToolContext(*scoped_fixture(), budget=RunBudget()))
        else:
            assert number == 4 and "tools" not in requests[-1]
            assert requests[-1]["thinking"] == {"type": "disabled"}
        return tool_completion("query_journal_lines", {"reference": f"absent-reference-{number}"},
                               identity=f"empty-{number}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client))
    assert len(requests) == result["usage"]["calls"] == 4
    assert result["status"] == "failed" and result["error"]["code"] == "model_no_progress"
    assert result["usage"]["tools"] == 3 and result["evidence_cache"]["queries"] == 3
    # The selected read and two distinct empty reads still consume budget.
    # The next request is explicitly no-tools; a forced tool call is rejected
    # before execution, preserving the negative results instead of looping.
    assert result["evidence_cache"]["records"] == 2 and result["claims"]
    assert result["proposed_claims"] == []
    assert [step["role"] for step in result["steps"] if step["event"] == "agent_started"] == ["coordinator", "evidence"]
    assert sum(step["event"] == "tool_completed" and step["role"] == "selector" for step in result["steps"]) == 1
    assert sum(step["event"] == "tool_completed" and step["role"] == "evidence" for step in result["steps"]) == 2
    assert not any(step["event"] == "evidence_stagnation" for step in result["steps"])
    assert sum(step["event"] == "role_tool_limit" for step in result["steps"]) == 1


@pytest.mark.asyncio
async def test_coordinator_contracts_have_no_execution_right_and_selector_runs_only_one_candidate():
    requests = []
    candidate = plan(alias="read-one-account", arguments={"account": "1403"})
    expensive = plan(alias="match-all", tool="match_transactions",
                     arguments={"left_kind": "journal", "right_kind": "bank"})["queries"][0]
    candidate["queries"].append(expensive)

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        number = len(requests)
        if number == 1:
            assert "tools" not in body
            brief = deserialize_context(body["messages"][1]["content"])
            assert brief["tool_contracts"] == tool_schemas(ToolContext(*scoped_fixture(), budget=RunBudget()))
            assert brief["shared_evidence"]["records"] == []
            return completion(planning_proposal(candidate))
        assert body["tools"] == tool_schemas(ToolContext(*scoped_fixture(), budget=RunBudget()))
        if number == 2:
            brief = deserialize_context(body["messages"][1]["content"])
            assert {row["evidence_id"] for row in brief["shared_evidence"]["records"]} == {"first-e1"}
            assert brief["evidence_selections"][0]["execution"]["performed"] is True
            return tool_completion("query_journal_lines", {}, reasoning="PRIVATE_TOOL_CHAIN_FIRST")
        if number == 3:
            # A new conversation carries authoritative results, not the prior
            # private reasoning/tool transcript. Both selected and later read
            # evidence remain present, without duplicating the full rows.
            assert [message["role"] for message in body["messages"]] == ["system", "user"]
            assert "PRIVATE_TOOL_CHAIN_FIRST" not in json.dumps(body)
            brief = deserialize_context(body["messages"][1]["content"])
            assert brief["current_role_tool_turns"] == 1
            assert brief["recent_tool_results"] == []
            assert {row["evidence_id"] for row in brief["shared_evidence"]["records"]} == {"first-e1", "first-e2"}
            reads = brief["shared_evidence"]["query_results"]
            assert reads[-1]["result"]["record_evidence_ids"] == ["first-e1", "first-e2"]
            assert len(reads) == 2
            return completion(proposed(evidence_plan=None))
        if number == 4:
            brief = deserialize_context(body["messages"][1]["content"])
            assert {row["evidence_id"] for row in brief["shared_evidence"]["records"]} == {"first-e1"}
            unread = [q for q in brief["shared_evidence"]["query_manifest"] if not q["payload_in_view"]]
            assert len(unread) == 1 and unread[0]["metadata"]["record_evidence_ids"] == ["first-e1", "first-e2"]
            return tool_completion("retrieve_policy", {"rule_id": "VOUCHER_BALANCE"}, reasoning="PRIVATE_TOOL_CHAIN_SECOND")
        assert number == 5
        assert "PRIVATE_TOOL_CHAIN" not in json.dumps(body)
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        brief = deserialize_context(body["messages"][1]["content"])
        assert brief["current_role_tool_turns"] == 1
        assert {row["evidence_id"] for row in brief["shared_evidence"]["records"]} == {"first-e1"}
        policy_read = brief["shared_evidence"]["query_results"][-1]
        assert policy_read["tool"] == "retrieve_policy"
        assert policy_read["parameters"]["rule_id"] == "VOUCHER_BALANCE"
        return completion(proposed(evidence_plan=None))

    fixture = scoped_fixture()
    untouched = deepcopy(fixture)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*fixture, mode="active", gateway=DeepSeekGateway("offline-key", client=client))
    assert fixture == untouched and result["status"] == "supported"
    assert result["usage"]["calls"] == 5 and result["usage"]["tools"] == 3
    assert len(result["evidence_selections"]) == 1
    selection = result["evidence_selections"][0]
    assert selection["selected_query"]["tool"] == "query_journal_lines"
    assert selection["selected_query"]["arguments"]["account"] == "1403"
    assert selection["execution"]["performed"] is True and len(selection["candidates"]) == 2
    performed = [(step["role"], step["tool"]) for step in result["steps"] if step["event"] == "tool_completed"]
    assert performed == [("selector", "query_journal_lines"), ("evidence", "query_journal_lines"), ("critic", "retrieve_policy")]
    assert not any(step.get("tool") == "match_transactions" for step in result["steps"])
    assert result["evidence_cache"]["records"] == 2


@pytest.mark.asyncio
async def test_coordinator_forced_tool_call_is_rejected_before_any_execution():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert "tools" not in body
        return tool_completion("query_journal_lines", {})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client))
    assert len(requests) == result["usage"]["calls"] == 1
    assert result["status"] == "failed" and result["error"]["code"] == "model_tool_policy"
    assert result["usage"]["tools"] == 0 and result["evidence_cache"]["queries"] == 0
    assert result["evidence_selections"] == [] and result["claims"]
    assert not any(step["event"] == "tool_attempt" for step in result["steps"])


@pytest.mark.asyncio
async def test_coordinator_null_plan_with_unresolved_issue_only_registers_missing_evidence():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            assert "tools" not in requests[-1]
            return completion(planning_proposal(None, unresolved=True))
        assert requests[-1]["tools"] == tool_schemas(ToolContext(*scoped_fixture(), budget=RunBudget()))
        return completion(proposed(evidence_plan=None))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client))
    assert result["usage"]["calls"] == 3 and result["usage"]["tools"] == 1
    assert result["evidence_cache"]["records"] == 0 and result["evidence_cache"]["queries"] == 0
    selection = result["evidence_selections"][0]
    assert selection["selected_query"] is None and selection["execution"]["status"] == "evidence_requested"
    assert selection["execution"]["performed"] is False and selection["execution"]["retrieved"] is False
    assert "Original source documentation" in result["missing_evidence"]
    assert any(item["status"] == "unresolved" and not item["evidence_ids"] for item in result["hypotheses"])
    performed = [(step["role"], step["tool"]) for step in result["steps"] if step["event"] == "tool_completed"]
    assert performed == [("selector", "request_evidence")]


@pytest.mark.asyncio
async def test_no_active_selection_rejects_injected_plan_without_running_it():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return completion(proposed(evidence_plan=plan()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*scoped_fixture(), mode="active", gateway=DeepSeekGateway("offline-key", client=client),
                                   checkpoint_config={"ablation": "no_active_selection", "synthetic_evaluation": True})
    assert calls == 2 and result["status"] == "failed" and result["error"]["code"] == "model_output"
    assert result["usage"]["tools"] == 0 and result["evidence_selections"] == []


@pytest.mark.asyncio
async def test_selector_checkpoint_restart_retains_spent_budget_and_one_effective_selection(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    class WorkerKilled(BaseException):
        pass

    stored_usage, requests = {}, []
    filename = str(tmp_path / "active-checkpoints.sqlite")
    graph_config = {"configurable": {"thread_id": "active-restart"}}

    def persist_then_crash(step):
        stored_usage.update(step["usage"])
        if step["event"] == "tool_completed" and step.get("role") == "selector":
            raise WorkerKilled()

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return completion(proposed(evidence_plan=plan()) if len(requests) == 1 else proposed())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DeepSeekGateway("offline-key", client=client)
        async with AsyncSqliteSaver.from_conn_string(filename) as saver:
            await saver.setup()
            with pytest.raises(WorkerKilled):
                await investigate(*scoped_fixture(), mode="active", gateway=gateway, on_step=persist_then_crash,
                                   checkpoint_config={"checkpointer": saver, "config": graph_config})
        assert stored_usage["calls"] == 1 and stored_usage["tools"] == 1
        async with AsyncSqliteSaver.from_conn_string(filename) as reopened:
            result = await investigate(*scoped_fixture(), mode="active", gateway=gateway,
                                       checkpoint_config={"checkpointer": reopened, "config": graph_config,
                                                          "resume": True, "usage": stored_usage})
    assert result["status"] == "supported" and result["usage"]["calls"] == 3
    # A read may repeat after a crash before checkpoint commit; both attempts
    # count, while only one selection becomes an effective persisted result.
    assert result["usage"]["tools"] == 2 and len(result["evidence_selections"]) == 1
    assert result["evidence_cache"]["records"] == 2 and result["evidence_cache"]["queries"] == 1
    assert len(result["proposals"]) == 3 and len(requests) == 3
    assert result["evidence_selections"][0]["execution"]["performed"] is True
