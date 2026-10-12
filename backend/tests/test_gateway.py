import json

import httpx
import pytest
from app.model_gateway import (BeforeDispatchRejected, BudgetExceeded, DeepSeekGateway, GatewayError, RunBudget,
                              provider_request_usage, strict_json)


def envelope(content='{"ok":true}', *, reasoning=None, tool_calls=None, total=7):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"model": "test-deepseek", "choices": [{"message": message,
            "finish_reason": "tool_calls" if tool_calls else "stop"}], "usage": {"total_tokens": total}}


@pytest.mark.asyncio
async def test_missing_key_never_sends_request():
    def handler(request):
        raise AssertionError("Must not contact service without a key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayError) as exc:
            await DeepSeekGateway(client=client).chat([], tools=[], budget=RunBudget())
    assert exc.value.code == "model_configuration"


@pytest.mark.asyncio
async def test_retry_attempts_are_counted_and_checkpointed():
    requests, events = [], []
    def handler(request):
        requests.append(request)
        return httpx.Response(429) if len(requests) < 3 else httpx.Response(200, json=envelope())
    budget = RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekGateway("test", client=client, retry_delay=0).chat(
            [{"role": "user", "content": "JSON"}], tools=[], budget=budget, on_attempt=events.append)
    assert result["message"]["content"] == '{"ok":true}'
    assert budget.calls == 3
    assert budget.tokens == 7
    assert budget.reserved_tokens > 0
    assert [e["usage"]["calls"] for e in events if e["event"] == "model_attempt"] == [1, 2, 3]
    attempts = [event for event in events if event["event"] == "model_attempt"]
    # Explain the conservative budget with sizes, without exporting messages.
    body_size = len(json.dumps(json.loads(requests[0].content), ensure_ascii=False).encode())
    assert all(event["context_bytes"] == body_size for event in attempts)
    assert all(event["reservation_tokens"] == body_size + 4096 for event in attempts)
    assert budget.reserved_tokens == 2 * (body_size + 4096)
    receipt = next(event for event in events if event["event"] == "model_usage")
    assert receipt["context_bytes"] == body_size and receipt["response_token_limit"] == 4096
    assert "messages" not in receipt and "messages" not in attempts[0]


@pytest.mark.asyncio
async def test_trusted_pre_dispatch_rejection_rolls_back_only_its_local_budget():
    calls, events = [], []
    budget = RunBudget(calls=1, tokens=7, reserved_tokens=123)
    async def hook(event):
        events.append(event)
        if event["event"] == "model_attempt":
            raise BeforeDispatchRejected("Fixture budget gate")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json=envelope()))) as client:
        with pytest.raises(BeforeDispatchRejected):
            await DeepSeekGateway("fixture", client=client).chat([], tools=None, budget=budget, on_attempt=hook)
    assert calls == []
    assert budget.usage() == {"calls": 1, "tokens": 7, "tools": 0, "reserved_tokens": 123}
    assert events[-1]["event"] == "model_not_dispatched"


@pytest.mark.asyncio
async def test_unknown_hook_failure_does_not_assume_safe_to_release_reservation():
    budget = RunBudget()
    async def hook(_):
        raise RuntimeError("Fixture unknown persistence failure")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: pytest.fail("Transport cannot run after hook failure"))) as client:
        with pytest.raises(RuntimeError):
            await DeepSeekGateway("fixture", client=client).chat([], tools=None, budget=budget, on_attempt=hook)
    assert budget.calls == 1 and budget.reserved_tokens > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 402, 403])
async def test_access_failures_do_not_retry_or_leak_secret(code):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(code, text="secret-provider-error")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayError) as exc:
            await DeepSeekGateway("secret-key", client=client, retry_delay=0).chat([], tools=[], budget=RunBudget())
    assert count == 1
    assert exc.value.code == "model_access"
    assert exc.value.http_status == code
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"choices": []}, envelope(content=""), {"choices": "bad"}])
async def test_malformed_envelope_is_explicit_error(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(GatewayError) as exc:
            await DeepSeekGateway("test", client=client).chat([], tools=[], budget=RunBudget())
    assert exc.value.code == "model_protocol"


@pytest.mark.asyncio
async def test_raw_invalid_json_is_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not-json"))) as client:
        with pytest.raises(GatewayError) as exc:
            await DeepSeekGateway("test", client=client).chat([], tools=[], budget=RunBudget())
    assert exc.value.code == "model_protocol"


@pytest.mark.asyncio
async def test_timeout_keeps_reserved_tokens_and_stops_at_three():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private URL", request=request)
    budget = RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayError) as exc:
            await DeepSeekGateway("test", client=client, retry_delay=0).chat([], tools=[], budget=budget)
    assert calls == budget.calls == 3
    assert budget.reserved_tokens > 0
    assert exc.value.code == "model_transport"
    assert "private" not in str(exc.value)


@pytest.mark.asyncio
async def test_budget_reservation_prevents_request():
    budget = RunBudget(max_tokens=10)
    with pytest.raises(BudgetExceeded):
        await DeepSeekGateway("test").chat([], tools=[], budget=budget)
    assert budget.calls == 0


@pytest.mark.parametrize("value", ['{"x":NaN}', '{"x":Infinity}', '{"x":1,"x":2}'])
def test_nonfinite_and_duplicate_json_rejected(value):
    with pytest.raises(ValueError):
        strict_json(value)


def test_insecure_remote_url_rejected():
    with pytest.raises(ValueError):
        DeepSeekGateway("key", base_url="http://example.com")
    with pytest.raises(ValueError):
        DeepSeekGateway("key", base_url="https://user:key@example.com")
    DeepSeekGateway("key", base_url="http://localhost:8999")


@pytest.mark.asyncio
async def test_tool_thinking_protocol_retains_internal_message():
    tool = {"id": "t1", "type": "function", "function": {"name": "retrieve_policy", "arguments": "{}"}}
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(200, json=envelope(None, reasoning="PRIVATE REASONING", tool_calls=[tool]))
        return httpx.Response(200, json=envelope())
    events = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway, budget = DeepSeekGateway("test", client=client), RunBudget()
        messages = [{"role": "user", "content": "JSON"}]
        first = await gateway.chat(messages, tools=[{"type": "function"}], budget=budget, on_attempt=events.append)
        messages += [first["message"], {"role": "tool", "tool_call_id": "t1", "content": "{}"}]
        await gateway.chat(messages, tools=[{"type": "function"}], budget=budget, on_attempt=events.append)
    assert seen[1]["messages"][1]["reasoning_content"] == "PRIVATE REASONING"
    assert "PRIVATE REASONING" not in json.dumps(events)


def test_restore_rejects_negative_or_coerced_usage():
    for usage in [{"calls": -1}, {"tokens": "10"}, {"tools": True}]:
        with pytest.raises(ValueError):
            RunBudget.restore(usage)


@pytest.mark.asyncio
async def test_oversized_context_fails_before_paid_attempt():
    budget = RunBudget()
    with pytest.raises(GatewayError) as exc:
        await DeepSeekGateway("test").chat([{"role": "user", "content": "x" * 130_000}],
                                            tools=[], budget=budget)
    assert exc.value.code == "model_context_limit"
    assert budget.calls == 0


@pytest.mark.asyncio
async def test_actual_over_budget_is_not_a_success():
    budget = RunBudget(max_tokens=20_000)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=envelope(total=20_001))
    )) as client:
        with pytest.raises(BudgetExceeded):
            await DeepSeekGateway("test", client=client).chat([], tools=[], budget=budget)
    assert budget.calls == 1
    assert budget.tokens == 20_001


@pytest.mark.asyncio
async def test_reasoning_effort_and_output_budget_are_explicit_transport_parameters():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=envelope())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DeepSeekGateway("test", client=client)
        await gateway.chat([], tools=[], budget=RunBudget(), max_tokens=8192, reasoning_effort="low")
        await gateway.chat([], tools=[], budget=RunBudget(), thinking=False, reasoning_effort="low")
    assert bodies[0]["reasoning_effort"] == "low"
    assert bodies[0]["max_tokens"] == 8192
    assert "reasoning_effort" not in bodies[1]
    with pytest.raises(ValueError, match="reasoning effort"):
        await DeepSeekGateway("test").chat([], tools=[], budget=RunBudget(), reasoning_effort="automatic")


@pytest.mark.asyncio
async def test_each_request_keeps_official_usage_and_provider_configuration_separate_from_total_budget():
    rows = [{"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17,
             "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6,
             "prompt_tokens_details": {"cached_tokens": 4},
             "completion_tokens_details": {"reasoning_tokens": 3}},
            {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25,
             "prompt_cache_hit_tokens": 12, "prompt_cache_miss_tokens": 8,
             "prompt_tokens_details": {"cached_tokens": 12},
             "completion_tokens_details": {"reasoning_tokens": 0}}]
    events, responses = [], []

    def handler(request):
        index = len(responses)
        payload = envelope(reasoning="PRIVATE_REASONING_TEXT")
        payload.update(model=f"deepseek-flash-version-{index + 1}", system_fingerprint=f"fp_config_{index + 1}",
                       usage={**rows[index], "undocumented_field": "SECRET_IGNORED_PROVIDER_TEXT"})
        responses.append(payload)
        return httpx.Response(200, json=payload)

    budget = RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DeepSeekGateway("test-key", client=client)
        first = await gateway.chat([], tools=[], budget=budget, on_attempt=events.append)
        second = await gateway.chat([], tools=[], budget=budget, on_attempt=events.append)
    receipts = [event for event in events if event["event"] == "model_usage"]
    assert [event["request_usage"] for event in receipts] == rows
    assert [event["request_index"] for event in receipts] == [1, 2]
    assert [event["provider_model"] for event in receipts] == ["deepseek-flash-version-1", "deepseek-flash-version-2"]
    assert [event["provider_system_fingerprint"] for event in receipts] == ["fp_config_1", "fp_config_2"]
    assert [event["usage"]["tokens"] for event in receipts] == [17, 42]
    assert first["request_usage"] == rows[0] and second["request_usage"] == rows[1]
    assert budget.tokens == 42 and budget.reserved_tokens == 0
    public = json.dumps(events)
    assert "PRIVATE_REASONING_TEXT" not in public and "SECRET_IGNORED_PROVIDER_TEXT" not in public
    assert "price" not in public and "CNY" not in public


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 10, "completion_tokens": 3},
                                    {"total_tokens": 7}])
async def test_missing_provider_fields_stay_null_without_deriving_counts_or_version(usage):
    payload = envelope()
    payload.pop("model")
    payload["usage"] = usage
    events, budget = [], RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        result = await DeepSeekGateway("test", client=client).chat([], tools=[], budget=budget, on_attempt=events.append)
    receipt = next(event for event in events if event["event"] == "model_usage")
    observed = usage or {}
    assert receipt["request_usage"]["total_tokens"] == observed.get("total_tokens")
    assert receipt["request_usage"]["prompt_cache_hit_tokens"] is None
    assert receipt["request_usage"]["prompt_tokens_details"]["cached_tokens"] is None
    assert receipt["request_usage"]["completion_tokens_details"]["reasoning_tokens"] is None
    assert receipt["provider_model"] is None and receipt["provider_system_fingerprint"] is None
    assert result["provider_model"] is None and result["model"] == "deepseek-flash"
    if "total_tokens" in observed:
        assert budget.tokens == 7 and budget.reserved_tokens == 0
    else:
        assert budget.tokens == 0 and budget.reserved_tokens > 0


def test_provider_zero_is_distinct_from_missing_fields():
    parsed = provider_request_usage({"total_tokens": 0, "completion_tokens_details": {"reasoning_tokens": 0}})
    assert parsed["total_tokens"] == 0
    assert parsed["completion_tokens_details"]["reasoning_tokens"] == 0
    assert parsed["prompt_tokens"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [
    [], "usage-text", {"total_tokens": True}, {"prompt_tokens": "10"}, {"completion_tokens": -1},
    {"prompt_cache_hit_tokens": 1.5}, {"prompt_cache_miss_tokens": False},
    {"prompt_tokens_details": []}, {"completion_tokens_details": "private"},
    {"prompt_tokens_details": {"cached_tokens": "3"}},
    {"completion_tokens_details": {"reasoning_tokens": -2}},
    {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 16},
    {"prompt_tokens": 10, "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 5},
    {"prompt_cache_hit_tokens": 4, "prompt_tokens_details": {"cached_tokens": 3}},
    {"completion_tokens": 2, "completion_tokens_details": {"reasoning_tokens": 3}},
    {"prompt_tokens": 10, "total_tokens": 9},
    {"prompt_tokens": 10, "prompt_cache_hit_tokens": 11},
])
async def test_invalid_or_inconsistent_usage_fails_before_settlement_without_retry(usage):
    payload, events, calls, budget = envelope(), [], 0, RunBudget()
    payload["usage"] = usage

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayError) as error:
            await DeepSeekGateway("test", client=client, retry_delay=0).chat([], tools=[], budget=budget,
                                                                           on_attempt=events.append)
    assert error.value.code == "model_protocol"
    assert "用量" in str(error.value)
    assert calls == budget.calls == 1
    assert budget.tokens == 0 and budget.reserved_tokens > 0
    assert not any(event["event"] == "model_usage" for event in events)
    assert "private" not in json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("model", {}), ("system_fingerprint", True),
                                         ("model", "private reasoning text"),
                                         ("system_fingerprint", "sk-SECRET_KEY_TEST_123")])
async def test_provider_identifiers_cannot_leak_arbitrary_text_or_reflected_key(field, value):
    payload, events = envelope(), []
    payload[field] = value
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(GatewayError) as error:
            await DeepSeekGateway("sk-SECRET_KEY_TEST_123", client=client).chat([], tools=[], budget=RunBudget(),
                                                                               on_attempt=events.append)
    assert error.value.code == "model_protocol"
    assert "sk-SECRET_KEY_TEST_123" not in json.dumps(events) + str(error.value)


@pytest.mark.asyncio
async def test_truncated_output_still_records_returned_usage_as_paid_request():
    payload, events, budget = envelope(total=17), [], RunBudget()
    payload["choices"][0]["finish_reason"] = "length"
    payload["usage"] = {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(GatewayError) as error:
            await DeepSeekGateway("test", client=client).chat([], tools=[], budget=budget, on_attempt=events.append)
    assert error.value.code == "model_truncated"
    receipt = next(event for event in events if event["event"] == "model_usage")
    assert receipt["request_usage"]["completion_tokens"] == 7
    assert budget.tokens == 17 and budget.reserved_tokens == 0
@pytest.mark.asyncio
async def test_remote_protocol_reset_is_bounded_transport_failure_and_keeps_unknown_usage():
    attempts = []
    events = []

    def handler(request):
        attempts.append(request)
        raise httpx.RemoteProtocolError('untrusted upstream diagnostic', request=request)

    budget = RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DeepSeekGateway('test-key', client=client, max_attempts=2, retry_delay=0)
        with pytest.raises(GatewayError) as error:
            await gateway.chat([{'role': 'user', 'content': 'JSON please'}], tools=None, budget=budget,
                               on_attempt=lambda event: events.append(event))
    assert error.value.code == 'model_transport'
    assert 'untrusted upstream diagnostic' not in str(error.value)
    assert len(attempts) == budget.calls == 2
    assert budget.tokens == 0 and budget.reserved_tokens > 0
    assert sum(event['event'] == 'model_transport_error' for event in events) == 2
