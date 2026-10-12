"""Model explanation and counter-evidence for one BPI2019 trace (Apache-2.0).

The model may only call three read-only tools scoped to ONE trace of ONE fixed
process snapshot. Program rule observations remain the facts; model statements
are unverified opinions whose citations must exist in that trace and have been
returned to the model. No accounting framework, posting, SQL or code execution.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .model_gateway import GatewayError, RunBudget, strict_json
from .process_events import ProcessSnapshot
from .process_rules import RULES, events_by_trace, review_trace

PROMPT_VERSION = "process-investigation-v1"
CONCLUSIONS = ("compatible", "needs_review", "insufficient", "out_of_scope")
PROTOCOL = """你复核一个BPI2019采购行项目的流程日志。只用中文JSON回答，只能调用给定的只读工具，范围限于本数据集快照中的这一个采购项。
事件数值是匿名事件价值，不是可记账的欧元金额；不存在会计准则、分录或异常真值。rule_review是程序对官方流程说明的观察，是事实依据；
你的任务是解释这些观察、主动寻找正常流程解释（反证），并列出缺少的信息。evidence_id只能使用工具返回或简报中出现的值，不得编造。
conclusion只能是compatible、needs_review、insufficient、out_of_scope之一，且不能比程序观察更确定。"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


EvidenceRef = Annotated[str, Field(min_length=1, max_length=200)]


class ProcessStatement(_Strict):
    text: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[EvidenceRef] = Field(min_length=1, max_length=20)
    rule_id: str = Field(max_length=100)


class ProcessProposal(_Strict):
    conclusion: Literal["compatible", "needs_review", "insufficient", "out_of_scope"]
    explanations: list[ProcessStatement] = Field(default_factory=list, max_length=10)
    counter_evidence: list[ProcessStatement] = Field(default_factory=list, max_length=10)
    missing_information: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10)


class TracePage(_Strict):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=50)


class TraceFilter(_Strict):
    event_type: str | None = Field(default=None, max_length=100)
    activity_raw: str | None = Field(default=None, max_length=200)


class NoArguments(_Strict):
    pass


TOOLS = {
    "get_process_trace": (TracePage, "按源顺序分页读取本采购项的事件及证据编号。"),
    "query_process_events": (TraceFilter, "按事件类型或原活动名筛选本采购项事件，最多返回50条。"),
    "read_rule_review": (NoArguments, "读取程序流程规则观察的完整结果。"),
}


def tool_schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": name, "description": text,
                                              "parameters": model.model_json_schema()}}
            for name, (model, text) in TOOLS.items()]


class ProcessScope:
    """Everything the tools may reveal; constructed from the verified snapshot only."""

    def __init__(self, snapshot: ProcessSnapshot, *, dataset_scope_id: str, snapshot_id: str, source_trace_id: str):
        self.review = review_trace(snapshot, dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id,
                                   source_trace_id=source_trace_id)
        self.trace = next(t for t in snapshot.traces if t.source_trace_id == source_trace_id)
        self.events = events_by_trace(snapshot).get(source_trace_id, [])
        self.evidence_ids = {self.trace.evidence_id, *(e.evidence_id for e in self.events)}
        self.rule_ids = {rule["rule_id"] for rule in RULES}
        self.read_ids: set[str] = {self.trace.evidence_id}

    @staticmethod
    def _event(event) -> dict:
        return {"evidence_id": event.evidence_id, "ordinal": event.source_event_ordinal,
                "activity": event.activity_raw, "event_type": event.event_type, "occurred_at": event.occurred_at_raw,
                "value_text": event.source_numeric_text, "same_timestamp": bool(event.same_timestamp_event_ordinals)}

    def execute(self, name: str, arguments: dict) -> dict:
        if name not in TOOLS:
            raise ValueError("Tool not allowed")
        values = TOOLS[name][0].model_validate(arguments).model_dump()
        if name == "read_rule_review":
            output = {"rule_review": self.review["results"], "conclusion": self.review["conclusion"]}
            self.read_ids.update(ref for item in self.review["results"] for ref in item["evidence_ids"])
            return output
        if name == "get_process_trace":
            page = self.events[values["offset"]:values["offset"] + values["limit"]]
        else:
            page = [e for e in self.events if (values["event_type"] is None or e.event_type == values["event_type"])
                    and (values["activity_raw"] is None or e.activity_raw == values["activity_raw"])][:50]
        self.read_ids.update(e.evidence_id for e in page)
        return {"total_events": len(self.events), "events": [self._event(e) for e in page]}


class ProposalRejected(ValueError):
    def __init__(self, diagnostics: list[dict]):
        super().__init__("Process proposal rejected")
        self.diagnostics = diagnostics


def validate(content: str, scope: ProcessScope) -> ProcessProposal:
    try:
        proposal = ProcessProposal.model_validate(strict_json(content))
    except (ValueError, TypeError, ValidationError):
        raise ProposalRejected([{"code": "invalid_structure", "path": []}]) from None
    errors = []
    for field in ("explanations", "counter_evidence"):
        for index, statement in enumerate(getattr(proposal, field)):
            refs = set(statement.evidence_ids)
            if not refs <= scope.evidence_ids:
                errors.append({"code": "citation_outside_trace", "path": [field, index, "evidence_ids"]})
            elif not refs <= scope.read_ids:
                errors.append({"code": "evidence_not_read", "path": [field, index, "evidence_ids"]})
            if statement.rule_id not in scope.rule_ids:
                errors.append({"code": "unknown_rule", "path": [field, index, "rule_id"]})
    if errors:
        raise ProposalRejected(errors[:20])
    return proposal


def _brief(scope: ProcessScope, read: dict) -> dict:
    return {"trace_alias": scope.trace.trace_alias, "classification": scope.trace.policy.classification,
            "trace_evidence_id": scope.trace.evidence_id, "event_count": len(scope.events),
            "program_conclusion": scope.review["conclusion"],
            "rule_summary": [{"rule_id": r["rule_id"], "status": r["status"], "reason": r["reason"]}
                             for r in scope.review["results"]],
            "unsupported_checks": scope.review["unsupported_checks"], "tool_results": read,
            "output_schema": ProcessProposal.model_json_schema()}


async def investigate_trace(scope: ProcessScope, gateway, *, budget: RunBudget | None = None,
                            max_tool_batches: int = 2) -> dict:
    """Bounded single-role loop; failures are returned, never converted to success."""
    budget = budget or RunBudget(max_calls=6, max_tokens=80_000, max_tools=12)
    read: dict[str, dict] = {}
    batches, repaired, steps = 0, False, []
    system = PROTOCOL
    try:
        while True:
            finish = batches >= max_tool_batches or repaired
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(_brief(scope, read), ensure_ascii=False)}]
            if repaired:
                messages.append({"role": "user", "content": "上次输出未通过校验，请按output_schema重新输出完整JSON。错误："
                                 + json.dumps(steps[-1]["diagnostics"], ensure_ascii=False)})
            response = await gateway.chat(messages, tools=None if finish else tool_schemas(), budget=budget,
                                          thinking=not repaired, max_tokens=4096)
            message = response["message"]
            calls = message.get("tool_calls") or []
            if calls and not finish:
                batches += 1
                for call in calls:
                    budget.use_tool()
                    name = call["function"]["name"]
                    try:
                        arguments = strict_json(call["function"]["arguments"])
                        output = scope.execute(name, arguments if isinstance(arguments, dict) else {})
                    except (ValueError, TypeError, ValidationError):
                        output = {"error": "invalid_tool_or_arguments", "allowed_tools": list(TOOLS)}
                    read[f"{batches}:{name}:{call['id']}"] = output
                    steps.append({"event": "tool_completed", "tool": name if name in TOOLS else "rejected"})
                continue
            if calls:
                raise GatewayError("model_no_progress", "取证已结束后模型仍请求工具")
            try:
                proposal = validate(message.get("content") or "", scope)
            except ProposalRejected as exc:
                steps.append({"event": "output_validation_failed", "diagnostics": exc.diagnostics})
                if repaired:
                    raise GatewayError("model_output", "模型结论未通过结构或引用校验") from None
                repaired = True
                continue
            return {"status": "complete", "proposal": proposal.model_dump(), "program_review": scope.review,
                    "usage": budget.usage(), "steps": steps, "prompt_version": PROMPT_VERSION,
                    "model": response.get("model"), "semantic_status": "unverified_model_opinion"}
    except GatewayError as exc:
        return {"status": "budget_exhausted" if exc.code == "budget_exhausted" else "failed",
                "error": {"code": exc.code, "message": str(exc)}, "proposal": None,
                "program_review": scope.review, "usage": budget.usage(), "steps": steps,
                "prompt_version": PROMPT_VERSION}
