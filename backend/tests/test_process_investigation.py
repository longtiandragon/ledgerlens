"""BPI trace investigation through a mock provider; hand-authored fixture traces only."""
import json

import httpx
import pytest

from backend.app.model_gateway import DeepSeekGateway, RunBudget
from backend.app.process_investigation import ProcessScope, investigate_trace, tool_schemas
from test_process_events import adapt, event, scope, trace

T1, T2 = "2018-01-02T00:00:00.000Z", "2018-01-03T00:00:00.000Z"


def snapshot_with_two_traces():
    return adapt([trace([event("Record Invoice Receipt", "1.0", T1), event("Record Goods Receipt", "1.0", T2)],
                        category="3-way match, invoice after GR", goods="true", gr_based="true"),
                  trace([event("Record Goods Receipt", "9.0", T1)], identity="fixture-other")])


def make_scope(snapshot):
    return ProcessScope(snapshot, **scope(snapshot), source_trace_id="fixture-only-item")


def tool_reply(name, arguments):
    return httpx.Response(200, json={"model": "offline", "usage": {"total_tokens": 10}, "choices": [{
        "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
            "id": "call-1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})


def answer(payload):
    return httpx.Response(200, json={"model": "offline", "usage": {"total_tokens": 10}, "choices": [{
        "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}}]})


async def run(snapshot, replies, budget=None):
    queue = list(replies)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return queue.pop(0)(seen[-1]) if callable(queue[0]) else queue.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate_trace(make_scope(snapshot), DeepSeekGateway("offline-key", client=client),
                                         budget=budget)
    return result, seen


@pytest.mark.asyncio
async def test_reads_trace_then_cites_only_returned_events_of_that_trace():
    snapshot = snapshot_with_two_traces()
    invoice = snapshot.events[0].evidence_id
    result, seen = await run(snapshot, [
        tool_reply("get_process_trace", {"offset": 0, "limit": 10}),
        answer({"conclusion": "needs_review", "missing_information": ["收货前收票的原因说明"],
                "explanations": [{"text": "基于收货验票的分类中先记录了收票。", "evidence_ids": [invoice],
                                  "rule_id": "BPI2019_PROCESS.GR_BASED_INVOICE_ORDER"}]})])
    assert result["status"] == "complete" and result["semantic_status"] == "unverified_model_opinion"
    assert result["program_review"]["conclusion"] == "needs_review"  # program facts stay separate
    brief = json.loads(seen[1]["messages"][1]["content"])
    returned = next(iter(brief["tool_results"].values()))["events"]
    assert {e["evidence_id"] for e in returned} == {e.evidence_id for e in snapshot.events[:2]}  # target trace only
    assert result["usage"]["calls"] == 2 and result["usage"]["tools"] == 1


@pytest.mark.asyncio
async def test_unread_or_foreign_citations_are_repaired_once_then_fail():
    snapshot = snapshot_with_two_traces()
    foreign = snapshot.events[2].evidence_id   # belongs to the other trace
    unread = snapshot.events[1].evidence_id    # in this trace, never returned
    bad = {"conclusion": "compatible", "explanations": [
        {"text": "x", "evidence_ids": [foreign], "rule_id": "BPI2019_PROCESS.POLICY_CLASSIFICATION"},
        {"text": "y", "evidence_ids": [unread], "rule_id": "BPI2019_PROCESS.POLICY_CLASSIFICATION"},
        {"text": "z", "evidence_ids": [snapshot.traces[0].evidence_id], "rule_id": "CAS_RULE"}]}
    result, seen = await run(snapshot, [answer(bad), answer(bad)])
    assert result["status"] == "failed" and result["error"]["code"] == "model_output"
    codes = [d["code"] for d in result["steps"][0]["diagnostics"]]
    assert codes == ["citation_outside_trace", "evidence_not_read", "unknown_rule"]
    assert seen[1]["messages"][-1]["content"].startswith("上次输出未通过校验") and "tools" not in seen[1]


@pytest.mark.asyncio
async def test_budget_exhaustion_is_reported_not_converted_to_success():
    snapshot = snapshot_with_two_traces()
    result, _ = await run(snapshot, [tool_reply("read_rule_review", {}), tool_reply("read_rule_review", {})],
                          budget=RunBudget(max_calls=2, max_tokens=80_000, max_tools=12))
    assert result["status"] == "budget_exhausted" and result["proposal"] is None


def test_tools_are_read_only_bounded_and_scoped():
    snapshot = snapshot_with_two_traces()
    scoped = make_scope(snapshot)
    assert {tool["function"]["name"] for tool in tool_schemas()} == {
        "get_process_trace", "query_process_events", "read_rule_review"}
    filtered = scoped.execute("query_process_events", {"event_type": "goods_receipt_recorded"})
    assert [e["ordinal"] for e in filtered["events"]] == [2]   # never the other trace's receipt
    with pytest.raises(ValueError):
        scoped.execute("run_sql", {})
    with pytest.raises(ValueError):
        scoped.execute("get_process_trace", {"limit": 500})
