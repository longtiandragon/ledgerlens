"""Bounded DeepSeek transport. Never put reasoning_content in public traces.

The caller persists the budget hook before every paid attempt. A network timeout
retains its token reservation because the provider may already have billed it.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class BudgetExceeded(GatewayError):
    def __init__(self, message: str):
        super().__init__("budget_exhausted", message)


class BeforeDispatchRejected(BudgetExceeded):
    """Trusted budget hook rejected dispatch before any transport was invoked.

    Only this explicit signal permits rolling back the current local reservation.
    Other hook failures and all transport failures remain conservatively reserved.
    """


@dataclass
class RunBudget:
    max_calls: int = 24
    max_tokens: int = 200_000
    max_tools: int = 60
    calls: int = 0
    tokens: int = 0
    tools: int = 0
    reserved_tokens: int = 0

    @classmethod
    def restore(cls, usage: dict | None = None) -> RunBudget:
        usage = usage or {}
        values = {k: usage.get(k, 0) for k in ("calls", "tokens", "tools", "reserved_tokens")}
        if any(type(v) is not int or v < 0 for v in values.values()):
            raise ValueError("Invalid persisted usage")
        return cls(**values)

    def usage(self) -> dict:
        return {k: getattr(self, k) for k in ("calls", "tokens", "tools", "reserved_tokens")}

    def reserve_request(self, token_ceiling: int) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded("模型请求次数达到上限")
        if self.tokens + self.reserved_tokens + token_ceiling > self.max_tokens:
            raise BudgetExceeded("模型 Token 预算不足以发起下一次请求")
        self.calls += 1
        self.reserved_tokens += token_ceiling

    def settle_request(self, reserved: int, actual: int | None) -> None:
        if actual is not None:
            self.reserved_tokens -= reserved
            self.tokens += actual

    def use_tool(self) -> None:
        if self.tools >= self.max_tools:
            raise BudgetExceeded("工具调用次数达到上限")
        self.tools += 1


async def notify(callback: Callable | None, value: dict) -> None:
    if callback:
        result = callback(value)
        if inspect.isawaitable(result):
            await result


def strict_json(value: str) -> Any:
    def reject_constant(constant: str):
        raise ValueError(f"Non-finite JSON value: {constant}")

    def unique_object(pairs: list[tuple]):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = item
        return result

    return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)


def provider_request_usage(value: object) -> dict:
    """Keep only documented, actually returned counters; never infer missing ones.

    Schema and identities: https://api-docs.deepseek.com/api/create-chat-completion/
    Null means unavailable, distinct from a provider-reported zero. Unknown
    fields are not retained, so provider text cannot enter the public trace.
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise GatewayError("model_protocol", "模型用量不是对象；未结算未知计费预算")

    def counter(container: dict, name: str) -> int | None:
        result = container.get(name)
        if result is not None and (type(result) is not int or result < 0):
            raise GatewayError("model_protocol", "模型用量包含非整数或负值；未结算未知计费预算")
        return result

    def detail(name: str, field: str) -> dict:
        container = value.get(name)
        if container is None:
            container = {}
        if not isinstance(container, dict):
            raise GatewayError("model_protocol", "模型用量明细不是对象；未结算未知计费预算")
        return {field: counter(container, field)}

    result = {name: counter(value, name) for name in (
        "prompt_tokens", "completion_tokens", "total_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    )}
    result["prompt_tokens_details"] = detail("prompt_tokens_details", "cached_tokens")
    result["completion_tokens_details"] = detail("completion_tokens_details", "reasoning_tokens")
    prompt, completion, total = (result[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens"))
    hit, miss = (result[name] for name in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"))
    cached = result["prompt_tokens_details"]["cached_tokens"]
    reasoning = result["completion_tokens_details"]["reasoning_tokens"]

    inconsistent = (
        (all(item is not None for item in (prompt, completion, total)) and total != prompt + completion)
        or (all(item is not None for item in (prompt, hit, miss)) and prompt != hit + miss)
        or (cached is not None and hit is not None and cached != hit)
        or (reasoning is not None and completion is not None and reasoning > completion)
        or (reasoning is not None and total is not None and reasoning > total)
        or any(item is not None and total is not None and item > total for item in (prompt, completion))
        or any(item is not None and prompt is not None and item > prompt for item in (cached, hit, miss))
        or (hit is not None and miss is not None and total is not None and hit + miss > total)
    )
    if inconsistent:
        raise GatewayError("model_protocol", "模型用量字段互相矛盾；未结算未知计费预算")
    return result


def provider_identifier(payload: dict, name: str, api_key: str) -> str | None:
    """Optional official identifier; do not turn other provider data into text."""
    value = payload.get(name)
    if value is None:
        return None
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/+-]{1,200}", value)
            or (api_key and (value == api_key or (len(api_key) >= 8 and api_key in value)))):
        raise GatewayError("model_protocol", "模型服务标识不符合响应协议")
    return value


class _Function(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    arguments: str = Field(max_length=32_000)


class _ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: str = Field(min_length=1, max_length=200)
    type: str
    function: _Function


class _AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    role: str
    content: str | None = Field(default=None, max_length=128_000)
    reasoning_content: str | None = Field(default=None, max_length=512_000)
    tool_calls: list[_ToolCall] | None = Field(default=None, max_length=20)


class DeepSeekGateway:
    """OpenAI-compatible chat transport, with no SDK's invisible retries.

    Inject an httpx.AsyncClient with MockTransport for offline tests. The caller
    owns injected clients; otherwise the gateway creates a client per request.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
        client: httpx.AsyncClient | None = None,
        timeout: float = 90.0,
        retry_delay: float = 0.5,
        max_attempts: int = 3,
    ):
        parsed = urlparse(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API base URL cannot contain credentials, query, or fragment")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("API endpoint must use HTTPS except a local development endpoint")
        if not parsed.hostname:
            raise ValueError("API endpoint needs a host")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = client
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.max_attempts = min(3, max(1, max_attempts))

    async def _bounded_request(self, client: httpx.AsyncClient, body: dict) -> httpx.Response:
        # A socket read timeout alone can be reset by every incoming chunk.
        # Bound the entire response as well as memory before buffering it.
        async with asyncio.timeout(self.timeout):
            async with client.stream(
                'POST', self.base_url + '/chat/completions', json=body,
                headers={'Authorization': f'Bearer {self.api_key}'}, timeout=self.timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    return httpx.Response(response.status_code)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise GatewayError('model_protocol', '模型响应超过允许大小')
                    chunks.append(chunk)
                return httpx.Response(response.status_code, content=b''.join(chunks))

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None,
        budget: RunBudget,
        on_attempt: Callable | None = None,
        thinking: bool = True,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> dict:
        if not self.api_key.strip():
            raise GatewayError("model_configuration", "未配置 DeepSeek API Key；无法运行模型模式")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
            raise ValueError("Response token limit must be an integer between 1 and 8192")
        if reasoning_effort not in {None, "low", "high", "max"}:
            raise ValueError("Unsupported reasoning effort")
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "enabled" if thinking else "disabled"},
            "response_format": {"type": "json_object"},
        }
        if tools:
            body["tools"] = tools
        if thinking and reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        # UTF-8 bytes deliberately overestimate normal tokenizer input size.
        encoded_size = len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if encoded_size > 128_000:
            raise GatewayError("model_context_limit", "当前上下文超过 128 KB；请缩小调查范围或查询返回行数")
        reservation = encoded_size + max_tokens
        for attempt in range(self.max_attempts):
            budget.reserve_request(reservation)
            try:
                await notify(on_attempt, {"event": "model_attempt", "attempt": attempt + 1, "usage": budget.usage(),
                                          "context_bytes": encoded_size, "reservation_tokens": reservation,
                                          "response_token_limit": max_tokens})
            except BeforeDispatchRejected:
                # No HTTP dispatch has occurred. Undo only this attempt; an
                # earlier timeout/HTTP failure may still have incurred charges.
                budget.calls -= 1
                budget.reserved_tokens -= reservation
                await notify(on_attempt, {"event": "model_not_dispatched", "attempt": attempt + 1,
                                          "reason": "request_budget_rejected_before_dispatch",
                                          "usage": budget.usage()})
                raise
            try:
                if self.client:
                    response = await self._bounded_request(self.client, body)
                else:
                    async with httpx.AsyncClient(follow_redirects=False) as client:
                        response = await self._bounded_request(client, body)
            except (TimeoutError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                await notify(on_attempt, {"event": "model_transport_error", "usage": budget.usage()})
                if attempt + 1 == self.max_attempts:
                    raise GatewayError("model_transport", "模型服务连接失败；已保留可能发生的计费预算") from None
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
                continue

            if response.status_code != 200:
                # Keep the reservation even on errors: compatible endpoints may
                # perform work before returning an upstream failure.
                await notify(on_attempt, {"event": "model_http_error", "http_status": response.status_code,
                                          "usage": budget.usage()})
                if response.status_code in {401, 402, 403}:
                    status_messages = {401: "模型认证失败，请检查服务密钥", 402: "模型服务额度不足（HTTP 402）",
                                       403: "模型服务拒绝访问，请检查账户权限"}
                    raise GatewayError("model_access", status_messages[response.status_code], http_status=response.status_code)
                if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < self.max_attempts:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise GatewayError("model_http", f"模型服务返回 HTTP {response.status_code}", http_status=response.status_code)

            try:
                if len(response.content) > 2_000_000:
                    raise GatewayError("model_protocol", "模型响应超过允许大小")
                payload = strict_json(response.text)
                request_usage = provider_request_usage(payload.get("usage"))
                provider_model = provider_identifier(payload, "model", self.api_key)
                provider_fingerprint = provider_identifier(payload, "system_fingerprint", self.api_key)
                actual = request_usage["total_tokens"]
                budget.settle_request(reservation, actual)
                await notify(on_attempt, {
                    "event": "model_usage", "usage": budget.usage(), "request_index": budget.calls,
                    "request_usage": request_usage, "provider_model": provider_model,
                    "provider_system_fingerprint": provider_fingerprint,
                    "context_bytes": encoded_size, "response_token_limit": max_tokens,
                })
                if budget.tokens + budget.reserved_tokens > budget.max_tokens:
                    raise BudgetExceeded("模型实际用量已超过本次运行预算，停止生成结论")
                choice = payload["choices"][0]
                if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                    raise GatewayError("model_truncated", "模型输出未正常结束；不能将截断内容作为结论")
                message = _AssistantMessage.model_validate(choice["message"])
                if message.role != "assistant":
                    raise ValueError("Expected assistant role")
                if any(call.type != "function" for call in message.tool_calls or []):
                    raise ValueError("Unsupported tool call type")
                if len({call.id for call in message.tool_calls or []}) != len(message.tool_calls or []):
                    raise ValueError("Duplicate tool call IDs")
                if not message.content and not message.tool_calls:
                    raise ValueError("Empty model output")
                # reasoning_content is returned only to the internal caller so
                # it can preserve the provider's thinking/tool continuation.
                return {"message": message.model_dump(exclude_none=True),
                        "model": provider_model or self.model, "usage": budget.usage(),
                        "request_usage": request_usage, "provider_model": provider_model,
                        "provider_system_fingerprint": provider_fingerprint}
            except GatewayError:
                raise
            except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError):
                raise GatewayError("model_protocol", "模型返回空内容或不符合响应协议") from None
        raise GatewayError("model_transport", "模型请求未完成")
