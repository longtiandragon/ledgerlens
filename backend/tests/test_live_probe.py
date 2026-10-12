import json

import httpx
import pytest

from app.model_gateway import DeepSeekGateway
from scripts.test_deepseek_live import main, run_live_probe


SECRET = "TEST_ONLY_API_SECRET"
REASONING = "PRIVATE_REASONING_MARKER"


def completion(message, *, finish="stop", tokens=20):
    return httpx.Response(200, json={
        "model": "deepseek-flash", "usage": {"total_tokens": tokens},
        "choices": [{"finish_reason": finish, "message": {"role": "assistant", **message}}],
    })


def tool_call():
    return completion({
        "content": None, "reasoning_content": REASONING,
        "tool_calls": [{"id": "call-smoke-1", "type": "function", "function": {
            "name": "decimal_sum", "arguments": '{"left":"0.10","right":"0.20"}',
        }}],
    }, finish="tool_calls")


@pytest.mark.asyncio
async def test_probe_executes_tool_round_trip_and_does_not_export_secrets(tmp_path):
    requests = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer " + SECRET
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body["thinking"] == {"type": "disabled"}
            assert "tools" not in body
            return completion({"content": '{"probe":"ledgerlens-smoke","ok":true}'})
        assert body["thinking"] == {"type": "enabled"}
        if len(requests) == 2:
            assert body["tools"][0]["function"]["name"] == "decimal_sum"
            return tool_call()
        assert len(requests) == 3
        # Provider continuation has actual tool execution and preserves its
        # private context; neither value is written into the public report.
        assert body["messages"][-2]["reasoning_content"] == REASONING
        assert body["messages"][-1]["role"] == "tool"
        tool = json.loads(body["messages"][-1]["content"])
        assert tool == {"amount": "0.30", "tool_call_id": "call-smoke-1"}
        return completion({"content": json.dumps(tool)})

    path = tmp_path / "probe.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_live_probe(DeepSeekGateway(SECRET, client=client), report_path=path)
    assert report["status"] == "passed"
    assert report["usage"] == {"calls": 3, "tokens": 60, "tools": 1, "reserved_tokens": 0}
    assert all(step["status"] == "passed" for step in report["steps"])
    saved = path.read_text(encoding="utf-8")
    assert SECRET not in saved and REASONING not in saved
    assert json.loads(saved) == report


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["no_tool", "wrong_arguments", "wrong_sum", "wrong_reference"])
async def test_valid_json_does_not_hide_missing_tool_or_wrong_answer(failure):
    count = 0

    def handler(request):
        nonlocal count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})
        count += 1
        if count == 1:
            return completion({"content": '{"probe":"ledgerlens-smoke","ok":true}'})
        if count == 2:
            if failure == "no_tool":
                return completion({"content": '{"amount":"0.30","tool_call_id":"invented"}'})
            if failure == "wrong_arguments":
                response = json.loads(tool_call().content)
                response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
                    '{"left":0.10,"right":"0.20"}'
                )
                return httpx.Response(200, json=response)
            return tool_call()
        result = {"amount": "0.31" if failure == "wrong_sum" else "0.30",
                  "tool_call_id": "invented" if failure == "wrong_reference" else "call-smoke-1"}
        return completion({"content": json.dumps(result)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_live_probe(DeepSeekGateway(SECRET, client=client))
    assert report["status"] == "failed"
    failed = [step for step in report["steps"] if step["status"] == "failed"]
    assert len(failed) == 1
    if failure in {"no_tool", "wrong_arguments"}:
        assert failed[0]["name"] == "thinking_tool_call"
        assert report["usage"]["tools"] == 0 and count == 2
    else:
        assert failed[0]["error_code"] == "tool_result_mismatch"
        assert report["usage"]["tools"] == 1 and count == 3


@pytest.mark.asyncio
async def test_models_access_failure_does_not_make_paid_call_or_leak_body(tmp_path):
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(401, text=SECRET + REASONING)

    path = tmp_path / "probe.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_live_probe(DeepSeekGateway(SECRET, client=client), report_path=path)
    assert methods == ["GET"]
    assert report["status"] == "failed"
    assert report["usage"]["calls"] == 0
    assert report["steps"][0]["error_code"] == "models_access"
    assert all(step["status"] == "skipped" for step in report["steps"][1:])
    assert SECRET not in path.read_text(encoding="utf-8")
    assert REASONING not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_paid_retries_stop_at_global_five_attempt_budget():
    posts = 0

    def handler(request):
        nonlocal posts
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})
        posts += 1
        if posts in {1, 3}:
            return httpx.Response(503, text=SECRET)
        if posts == 2:
            return completion({"content": '{"probe":"ledgerlens-smoke","ok":true}'})
        if posts == 4:
            return tool_call()
        return httpx.Response(503, text=SECRET)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_live_probe(DeepSeekGateway(SECRET, client=client, retry_delay=0, max_attempts=3))
    assert report["status"] == "failed"
    assert posts == 5 and report["usage"]["calls"] == 5
    assert report["usage"]["reserved_tokens"] > 0
    assert report["steps"][-1]["error_code"] == "budget_exhausted"
    assert SECRET not in json.dumps(report)


def test_cli_requires_explicit_live_opt_in_before_loading_settings():
    with pytest.raises(SystemExit) as failure:
        main([])
    assert failure.value.code == 2
