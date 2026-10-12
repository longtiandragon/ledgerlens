"""Explicit, bounded live DeepSeek protocol probe; never exports private reasoning.

Run from the repository root:
    .venv\\Scripts\\python.exe scripts/test_deepseek_live.py --live

Importing this module is safe for offline tests: settings and .env are loaded
only by main(), after the user explicitly opts into real API requests.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.model_gateway import DeepSeekGateway, GatewayError, RunBudget, strict_json  # noqa: E402


class ProbeError(RuntimeError):
    """Only fixed, public diagnostic codes enter reports."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "decimal_sum",
        "description": "Add the two specified decimal strings exactly and return a decimal string.",
        "parameters": {
            "type": "object",
            "properties": {"left": {"type": "string"}, "right": {"type": "string"}},
            "required": ["left", "right"],
            "additionalProperties": False,
        },
    },
}
STEP_NAMES = ("models", "json_output", "thinking_tool_call", "thinking_tool_result")


def _public_model_id(value: object, secret: str) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value):
        return None
    if secret and secret in value:
        return None
    return value


async def _list_models(gateway: DeepSeekGateway, client: httpx.AsyncClient) -> list[str]:
    async with asyncio.timeout(gateway.timeout):
        async with client.stream(
            "GET", gateway.base_url + "/models",
            headers={"Authorization": "Bearer " + gateway.api_key},
            timeout=gateway.timeout, follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                if response.status_code in {401, 402, 403}:
                    raise ProbeError("models_access")
                raise ProbeError("models_http")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 2_000_000:
                    raise ProbeError("models_protocol")
                chunks.append(chunk)
    try:
        body = strict_json(b"".join(chunks).decode("utf-8"))
        rows = body["data"]
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError("Invalid model list")
        ids = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid model row")
            model_id = _public_model_id(row.get("id"), gateway.api_key)
            if model_id is not None:
                ids.append(model_id)
        return sorted(set(ids))
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ProbeError("models_protocol") from None


def _content_json(message: dict) -> dict:
    if message.get("tool_calls"):
        raise ProbeError("unexpected_tool_call")
    try:
        value = strict_json(message.get("content", ""))
    except (ValueError, TypeError):
        raise ProbeError("invalid_json") from None
    if not isinstance(value, dict):
        raise ProbeError("invalid_json")
    return value


async def run_live_probe(
    gateway: DeepSeekGateway, *, report_path: Path | None = None,
) -> dict:
    """GET /models and at most five paid attempts, including transport retries.

    Passing a MockTransport-backed gateway permits exactly the same validation
    without contacting a provider. No model response text is persisted.
    """
    budget = RunBudget(max_calls=5, max_tokens=40_000, max_tools=1)
    start = time.perf_counter()
    report = {
        "schema_version": "deepseek-live-smoke-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "configured_model": _public_model_id(gateway.model, gateway.api_key),
        "status": "running",
        "limits": {"paid_attempts": 5, "tokens": 40_000, "tool_executions": 1},
        "steps": [], "events": [], "response_models": [], "usage": budget.usage(),
        "cost_note": "Token 用量不是金额；超时或错误保留可能计费的预算。",
    }

    def checkpoint() -> None:
        report["usage"] = budget.usage()
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = report_path.with_suffix(report_path.suffix + ".tmp")
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(report_path)

    def on_attempt(event: dict) -> None:
        # Whitelist fields rather than logging request/response objects.
        safe = {"event": event["event"], "usage": budget.usage()}
        if "http_status" in event:
            safe["http_status"] = event["http_status"]
        if "attempt" in event:
            safe["attempt"] = event["attempt"]
        report["events"].append(safe)
        checkpoint()

    def passed(name: str, **public_details: object) -> None:
        report["steps"].append({"name": name, "status": "passed", **public_details})
        checkpoint()

    async def chat(messages: list[dict], *, tools: list[dict] | None, thinking: bool, limit: int) -> dict:
        result = await gateway.chat(
            messages, tools=tools, budget=budget, on_attempt=on_attempt,
            thinking=thinking, max_tokens=limit,
        )
        model_id = _public_model_id(result.get("model"), gateway.api_key)
        if model_id is not None and model_id not in report["response_models"]:
            report["response_models"].append(model_id)
        return result["message"]

    current_step = "models"
    checkpoint()
    try:
        if not gateway.api_key.strip():
            raise ProbeError("model_configuration")
        if gateway.client is not None:
            model_ids = await _list_models(gateway, gateway.client)
        else:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                model_ids = await _list_models(gateway, client)
        if gateway.model not in model_ids:
            raise ProbeError("configured_model_unavailable")
        passed("models", available_models=model_ids)

        current_step = "json_output"
        message = await chat([
            {"role": "system", "content": "Return only the exact JSON object requested by the user."},
            {"role": "user", "content": 'Return JSON {"probe":"ledgerlens-smoke","ok":true}.'},
        ], tools=None, thinking=False, limit=256)
        value = _content_json(message)
        if set(value) != {"probe", "ok"} or value["probe"] != "ledgerlens-smoke" or value["ok"] is not True:
            raise ProbeError("json_answer_mismatch")
        passed("json_output", thinking=False, answer_verified=True)

        current_step = "thinking_tool_call"
        messages = [
            {"role": "system", "content": (
                "You are testing a tool protocol. You MUST call decimal_sum exactly once, "
                "using the user's exact string inputs. Do not calculate the answer without the tool. "
                "After its response, return only JSON with exactly amount and tool_call_id; "
                "copy both from the tool result."
            )},
            {"role": "user", "content": (
                'Use decimal_sum with left="0.10" and right="0.20". '
                'Your final JSON must reference the actual returned tool_call_id.'
            )},
        ]
        assistant = await chat(messages, tools=[TOOL_SCHEMA], thinking=True, limit=1024)
        calls = assistant.get("tool_calls") or []
        if len(calls) != 1 or calls[0]["function"]["name"] != "decimal_sum":
            raise ProbeError("required_tool_not_called")
        try:
            arguments = strict_json(calls[0]["function"]["arguments"])
        except (ValueError, TypeError):
            raise ProbeError("invalid_tool_arguments") from None
        if arguments != {"left": "0.10", "right": "0.20"}:
            raise ProbeError("invalid_tool_arguments")
        budget.use_tool()
        amount = format(Decimal(arguments["left"]) + Decimal(arguments["right"]), ".2f")
        tool_id = calls[0]["id"]
        # Assistant reasoning is needed for provider continuation but stays in
        # this in-memory protocol context; public reports contain none of it.
        messages.append(assistant)
        messages.append({
            "role": "tool", "tool_call_id": tool_id,
            "content": json.dumps({"amount": amount, "tool_call_id": tool_id}),
        })
        passed("thinking_tool_call", thinking=True, tool="decimal_sum", tool_executions=1)

        current_step = "thinking_tool_result"
        final = await chat(messages, tools=[TOOL_SCHEMA], thinking=True, limit=1024)
        value = _content_json(final)
        if value != {"amount": "0.30", "tool_call_id": tool_id}:
            raise ProbeError("tool_result_mismatch")
        passed("thinking_tool_result", amount="0.30", tool_reference_verified=True)
        report["status"] = "passed"
    except (ProbeError, GatewayError) as error:
        report["steps"].append({"name": current_step, "status": "failed", "error_code": error.code})
        report["status"] = "failed"
    except (TimeoutError, httpx.TimeoutException, httpx.NetworkError):
        report["steps"].append({"name": current_step, "status": "failed", "error_code": "models_transport"})
        report["status"] = "failed"
    except Exception:
        # Provider text and arbitrary exception messages can contain secrets.
        report["steps"].append({"name": current_step, "status": "failed", "error_code": "unexpected_error"})
        report["status"] = "failed"
    complete = {step["name"] for step in report["steps"]}
    report["steps"].extend({"name": name, "status": "skipped"} for name in STEP_NAMES if name not in complete)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    checkpoint()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded real DeepSeek JSON/thinking/tool protocol probe")
    parser.add_argument("--live", action="store_true", help="Explicitly authorize real API requests and charges")
    parser.add_argument("--report", type=Path, default=ROOT / "runtime" / "deepseek-live-smoke.json")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required to perform real API requests")
    from app.config import settings

    gateway = DeepSeekGateway(
        api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url,
        model=settings.deepseek_model, max_attempts=2, timeout=60,
    )
    report = asyncio.run(run_live_probe(gateway, report_path=args.report))
    print(json.dumps({"status": report["status"], "steps": report["steps"],
                      "usage": report["usage"], "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
