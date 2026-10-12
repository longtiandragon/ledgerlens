"""Bounded, evaluator-owned rule-label comparison across four real runtimes.

Examples (from the repository root):
    python -m benchmarks.run_agents --modes rules --max-cases 12
    python -m benchmarks.run_agents --modes rules single multi active --max-cases 8 \
        --repeat 3 --max-paid-requests 240

The CLI never provides a fake model. Model modes require DEEPSEEK_API_KEY.
Only this evaluator reads truth/, and only after each runtime has finished.
Metric: visible rule-label identification with citation checks, NOT root-cause
accuracy, professional correctness, or evidence that Multi-Agent is superior.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain import check_records, rule_catalog, rule_identity  # noqa: E402
from app.investigation import (ABLATIONS, MODEL_PARAMETERS, MODES, PROMPT_VERSION,  # noqa: E402
                               PUBLIC_FIELDS, TOOL_VERSION, investigate)
from app.model_gateway import (BeforeDispatchRejected, BudgetExceeded, DeepSeekGateway,  # noqa: E402
                               GatewayError, RunBudget, notify)

RUNTIME_SOURCE_HASHES = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
    "backend/app/investigation.py", "backend/app/model_gateway.py", "backend/app/context_transport.py",
    "backend/app/model_contract.py",
    "backend/app/business_chain.py", "backend/app/evidence_planning.py", "backend/app/evidence_selection.py",
    "benchmarks/run_agents.py")}

SPLITS = {"development", "validation", "sealed_test", "all"}
POLICY_FIELDS = {"framework", "version", "period", "currency", "accounts", "locked_periods", "recognition_accounts"}
OPAQUE_FIELDS = {"source_id", "reference", "order_id", "invoice_id", "voucher_id"}
TERMINAL_FAILURES = {"failed", "budget_exhausted"}
LIMITATIONS = [
    "指标为合成账套可见规则标签识别，不是根因分类或真实企业会计准确率。",
    "模型组读取中性全账复核任务；未提供生产检查结果、隐藏原因或真值标签。",
    "评分采用最后完成的模型 Proposal；fact_status 与确定性 claims 不进入模型评分。",
    "模型 claim 仅 assessment=finding 计为已检出的规则条件；正常检查、无法检查及范围外描述不计异常标签。",
    "引用存在只证明可定位，不证明自由文本解释在语义上成立。",
    "运行失败与总预算未执行案例保留在结果中，并按未识别标签计入主指标。",
    "重复运行共享场景族，不能当成相互独立的行业样本。",
    "有限样本与预算耗尽会影响方法间可比性；报告给出每组完成数及覆盖范围。",
    "active 使用模型候选预期与服务端成本选择并执行当前快照查询；预期不是校准概率。快照外补证仍只登记请求，不自动取得。",
    "逐请求用量只保留服务端实际返回计数；缺项不推算。Token不是人民币，未知计费预留不等于实际收费。",
    "Macro-F1 使用所有方法共享的固定规则目录标签集合；无正例且无预测的标签按零计分，另报告标签支持数。",
    "覆盖内 observed_rule_macro_f1 使用全部可评分样本真值的共同标签集合；未观测标签误报另计数，不能只引用覆盖内分数。",
    "区间按场景族整体重采样，族内变体和重复运行保持在一起；不能用重复次数扩大独立样本量。",
    "request_parameters记录计划的逻辑chat；request_attempts记录获派发许可的物理尝试，重试单独计数；二者都不等同服务端收费。",
    "全局请求预算在发送前明确拒绝时单列not_dispatched并回滚该次本地预留；此前未知计费预留继续保留。",
    "质量指标只使用有有效真值的样本；费用与用量汇总包含无法评分和运行失败的所有样本。",
]


@dataclass
class BenchmarkOptions:
    dataset: Path
    report: Path
    split: str = "development"
    modes: tuple[str, ...] = ("rules", "single", "multi", "active")
    repeat: int = 3
    max_cases: int = 8
    max_paid_requests: int = 240
    ablations: tuple[str, ...] = ()
    max_seconds_per_case: float = 900.0
    env_file: Path | None = None

    def validate(self):
        if self.split not in SPLITS:
            raise ValueError("Unknown dataset split")
        if not self.modes or len(set(self.modes)) != len(self.modes) or not set(self.modes) <= MODES:
            raise ValueError("Modes must be a unique nonempty selection of rules/single/multi/active")
        if not 1 <= self.repeat <= 10:
            raise ValueError("repeat must be between 1 and 10")
        if not 1 <= self.max_cases <= 480:
            raise ValueError("max_cases must be between 1 and 480")
        if not 0 <= self.max_paid_requests <= 20_000:
            raise ValueError("max_paid_requests must be between 0 and 20000")
        if not 1 <= self.max_seconds_per_case <= 3600:
            raise ValueError("max_seconds_per_case must be between 1 and 3600")
        if len(set(self.ablations)) != len(self.ablations) or not set(self.ablations) <= ABLATIONS:
            raise ValueError("Invalid or duplicate ablations")
        if self.ablations and "active" not in self.modes:
            raise ValueError("Ablation comparison requires the active baseline in --modes")


@dataclass
class PaidRequestBudget:
    maximum: int
    used: int = 0
    calls_by_sample: Counter = field(default_factory=Counter)

    async def before_attempt(self, sample_id: str, event: dict):
        if event.get("event") != "model_attempt":
            return
        if self.used >= self.maximum:
            raise BeforeDispatchRejected("整个基准的付费请求预算耗尽；未发送当前请求")
        self.used += 1
        self.calls_by_sample[sample_id] += 1


class BoundedGateway:
    """Count actual transport attempts before network dispatch, including retries."""

    def __init__(self, gateway, budget: PaidRequestBudget, sample_id: str,
                 context: Callable[[], dict] | None = None):
        self.gateway, self.budget, self.sample_id = gateway, budget, sample_id
        self.context = context
        self.request_parameters: list[dict] = []
        self.request_attempts: list[dict] = []
        self.request_not_dispatched: list[dict] = []

    @property
    def model(self):
        return getattr(self.gateway, "model", None)

    async def chat(self, messages, *, on_attempt=None, **kwargs):
        context = self.context() if self.context else {}
        thinking = kwargs.get("thinking", True)
        parameters = {"logical_chat_index": len(self.request_parameters) + 1,
                      "role": context.get("role"), "stage": context.get("stage"),
                      "model": self.model, "thinking": thinking,
                      "max_tokens": kwargs.get("max_tokens", 4096),
                      "configured_reasoning_effort": kwargs.get("reasoning_effort"),
                      "reasoning_effort": kwargs.get("reasoning_effort") if thinking else None,
                      "temperature": None, "temperature_policy": "provider_default",
                      "tools_enabled": bool(kwargs.get("tools")),
                      "tool_names": sorted(tool["function"]["name"] for tool in kwargs.get("tools") or [])}
        self.request_parameters.append(parameters)
        async def guarded(event):
            if event.get("event") == "model_attempt":
                attempt = {**parameters, "request_index": event.get("usage", {}).get("calls"),
                           "retry_attempt": event.get("attempt"), "context_bytes": event.get("context_bytes"),
                           "reservation_tokens": event.get("reservation_tokens")}
                try:
                    await self.budget.before_attempt(self.sample_id, event)
                except BeforeDispatchRejected:
                    self.request_not_dispatched.append({**attempt, "status": "not_dispatched",
                                                        "reason": "global_paid_request_budget_exhausted"})
                    raise
                self.request_attempts.append({**attempt, "status": "dispatch_authorized_before_transport"})
            await notify(on_attempt, event)

        return await self.gateway.chat(messages, on_attempt=guarded, **kwargs)


def _model_configuration(env_file: Path | None) -> dict[str, str]:
    """Read only an explicitly requested file, without modifying process env.

    Environment variables take precedence. Disable interpolation to avoid
    accidentally expanding unrelated environment values into model settings.
    Never include file content or keys in report/error output.
    """
    values = {}
    if env_file is not None:
        if not env_file.is_file():
            raise ValueError("显式指定的模型环境文件不存在或不是文件")
        try:
            values = dotenv_values(env_file, encoding="utf-8", interpolate=False)
        except (OSError, UnicodeError):
            raise ValueError("无法读取显式指定的模型环境文件") from None
    names = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
    return {name: os.environ[name] if name in os.environ else (values.get(name) or "") for name in names}


def _safe_case_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
        raise ValueError("Unsafe case identifier")
    return value


def select_cases(manifest: dict, split: str, maximum: int) -> list[dict]:
    """Spread small runs across families without reading faults, names, or truth."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for raw in manifest.get("cases", []):
        if split != "all" and raw.get("split") != split:
            continue
        case_id = _safe_case_id(raw["case_id"])
        # Current dataset identifiers encode family position, not the fault type.
        # Explicit family_id is used for compatible future dataset versions.
        family_id = raw.get("family_id") or "-".join(case_id.split("-")[:-1])
        selected = {k: raw[k] for k in ("split", "framework", "sha256") if k in raw}
        selected.update(case_id=case_id, family_id=family_id)
        groups[str(family_id)].append(selected)
    for group in groups.values():
        group.sort(key=lambda x: x["case_id"])
    keys = sorted(groups)
    if maximum < len(keys):
        # The first families happen to be normal cases; evenly spread a small
        # sample over family positions rather than silently testing only those.
        keys = [keys[index * len(keys) // maximum] for index in range(maximum)]
    result = []
    for index in range(max((len(g) for g in groups.values()), default=0)):
        for position, key in enumerate(keys):
            if index < len(groups[key]):
                variant = (index + position % 2) % len(groups[key])
                result.append(groups[key][variant])
                if len(result) == maximum:
                    return result
    return result


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def _public_records(records: list[dict]) -> list[dict]:
    """Remove answer-like extra fields; hide generator source-name shortcuts."""
    result = []
    for row in records:
        output = {k: deepcopy(row[k]) for k in PUBLIC_FIELDS | {"direction"} if k in row}
        for key in OPAQUE_FIELDS:
            if output.get(key):
                # Same mapping across identifier fields preserves cross-document
                # references while hiding names such as JV-COPY/JV-REVERSE.
                output[key] = "ref-" + hashlib.sha256(str(output[key]).encode()).hexdigest()[:20]
        result.append(output)
    return result


def load_observable(dataset: Path, entry: dict) -> dict:
    """Read observed/ only. Never pass manifest family definitions to runtime."""
    filename = dataset / "observed" / f"{_safe_case_id(entry['case_id'])}.json"
    payload = filename.read_bytes()
    if entry.get("sha256") and hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise ValueError("Observed dataset checksum mismatch")
    document = json.loads(payload)
    if document.get("synthetic") is not True:
        raise ValueError("This evaluator accepts explicitly synthetic cases only")
    if document.get("case_id") != entry["case_id"]:
        raise ValueError("Observed case ID mismatch")
    records = document.get("records")
    if not isinstance(records, list) or not records or len(records) > 50_000:
        raise ValueError("Invalid observed record list")
    if not all(isinstance(r, dict) and r.get("id") and r.get("evidence_id") for r in records):
        raise ValueError("Observed records need stable record and evidence IDs")
    if any(not isinstance(r.get("observed_at"), str) for r in records):
        raise ValueError("Observed records require observation times")
    policy = {k: deepcopy(v) for k, v in document["policy"].items() if k in POLICY_FIELDS}
    latest = max(datetime.fromisoformat(r["observed_at"]).astimezone(UTC) for r in records)
    policy["snapshot_cutoff"] = latest.isoformat()
    return {"records": _public_records(records), "policy": policy,
            "family_id": str(document.get("family_id", entry["family_id"])),
            "split": str(document.get("split", entry.get("split", "unknown")))}


def neutral_finding(records: list[dict]) -> dict:
    """A neutral review task for normal AND abnormal cases; no precomputed label."""
    indexes = {key: sorted({str(r[key]) for r in records if r.get(key)})[:30]
               for key in ("order_id", "invoice_id", "voucher_id", "reference")}
    return {"task_type": "review", "rule_id": "BENCHMARK_SCOPE", "title": "合成账套完整复核", "status": "unsupported",
            "record_ids": [r["id"] for r in records], "evidence_ids": [r["evidence_id"] for r in records],
            "explanation": "独立检查可见账套，先使用 retrieve_policy 阅读规则目录，再查询数据。"
                           "claims.rule_id 填写真实目录规则，并用 assessment 区分已观察到条件(finding)、"
                           "检查正常(normal)、无法检查(insufficient)与范围外(unsupported)。没有问题时 status=normal。"
                           "资料不足时 status=insufficient 并说明所需资料，不猜测隐藏原因。",
            "missing_evidence": [], "metrics": {"visible_business_index": indexes}}


def _prediction(result: dict, mode: str, records: list[dict], policy: dict) -> dict:
    allowed = {r["rule_id"] for r in rule_catalog() if r["framework"] == policy["framework"]}
    available = {r["evidence_id"] for r in records}
    if mode == "rules":
        findings = result.get("findings", [])
        return {"predicted_rules": sorted({f["rule_id"] for f in findings}), "predicted_status": result["status"],
                "invalid_claims": 0, "cited_claims": len(findings), "valid_cited_claims": len(findings)}
    proposals = result.get("proposals", [])
    if not proposals:
        return {"predicted_rules": [], "predicted_status": None,
                "invalid_claims": 0, "cited_claims": 0, "valid_cited_claims": 0}
    final = proposals[-1]
    predicted, valid, invalid = set(), 0, 0
    for claim in final.get("claims", []):
        citations = claim.get("evidence_ids", [])
        assessment = claim.get("assessment", "finding")  # compatible with pre-assessment structured proposals
        if (claim.get("rule_id") in allowed and isinstance(citations, list) and citations
                and set(citations) <= available and assessment in {"finding", "normal", "insufficient", "unsupported"}):
            if assessment == "finding":
                predicted.add(claim["rule_id"])
            valid += 1
        else:
            invalid += 1
    return {"predicted_rules": sorted(predicted), "predicted_status": final.get("status"),
            "invalid_claims": invalid, "cited_claims": valid + invalid, "valid_cited_claims": valid}


def _score_truth(dataset: Path, case_id: str, row: dict) -> dict:
    """Evaluator-only. Called after execution; hidden causes never enter output."""
    truth = _read_object(dataset / "truth" / f"{_safe_case_id(case_id)}.json")
    if truth.get("case_id") != case_id:
        raise ValueError("Truth case ID mismatch")
    expected = truth.get("expected_rules")
    if not isinstance(expected, list) or not all(isinstance(x, str) for x in expected):
        raise ValueError("Missing evaluator rule labels")
    failed = row.get("error") is not None
    predicted = [] if failed else row["predicted_rules"]
    return {"expected_rules": sorted(set(expected)), "expected_status": truth.get("determinability"),
            "scored_predicted_rules": predicted,
            "exact_rule_match": not failed and set(expected) == set(predicted),
            "status_match": not failed and row.get("predicted_status") == truth.get("determinability")}


def family_bootstrap(rows: list[dict], *, iterations: int = 2000, seed: int = 1729) -> dict:
    """Percentile intervals from cluster resampling, not independent repeats."""
    families: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        families[row["family_id"]].append(row)
    result = {"method": "family_cluster_percentile_bootstrap", "confidence": 0.95,
              "iterations": iterations, "seed": seed, "families": len(families),
              "exact_rule_match_rate": None, "status_match_rate": None}
    if len(families) < 2:
        result["limitation"] = "至少需要两个场景族；不以族内重复运行伪造置信区间"
        return result
    if type(iterations) is not int or iterations < 100:
        raise ValueError("Bootstrap requires at least 100 iterations")
    clusters = [families[key] for key in sorted(families)]
    totals = [len(cluster) for cluster in clusters]
    successes = {metric: [sum(row.get(metric, False) and row.get("error") is None for row in cluster)
                          for cluster in clusters] for metric in ("exact_rule_match", "status_match")}
    draws = {metric: [] for metric in successes}
    rng = random.Random(seed)
    for _ in range(iterations):
        sample = rng.choices(range(len(clusters)), k=len(clusters))
        total = sum(totals[index] for index in sample)
        for metric, counts in successes.items():
            draws[metric].append(sum(counts[index] for index in sample) / total)

    def percentile(values, fraction):
        ordered = sorted(values)
        position = (len(ordered) - 1) * fraction
        index = int(position)
        upper = min(index + 1, len(ordered) - 1)
        return ordered[index] + (ordered[upper] - ordered[index]) * (position - index)

    for metric, values in draws.items():
        result[f"{metric}_rate"] = {"lower": percentile(values, 0.025), "upper": percentile(values, 0.975)}
    return result


def metrics(rows: list[dict], *, labels: list[str] | None = None, observed_labels: list[str] | None = None) -> dict:
    counts = defaultdict(Counter)
    for row in rows:
        expected = set(row.get("expected_rules", []))
        predicted = set(row.get("scored_predicted_rules", []))
        for label in expected | predicted:
            counts[label]["tp" if label in expected & predicted else "fp" if label in predicted else "fn"] += 1
    label_set = sorted(set(labels)) if labels is not None else sorted(counts)
    if not set(counts) <= set(label_set):
        raise ValueError("Scored labels outside the fixed benchmark label universe")
    observed = sorted(set(observed_labels)) if observed_labels is not None else sorted({
        label for row in rows for label in row.get("expected_rules", [])})
    if not set(observed) <= set(label_set):
        raise ValueError("Observed labels outside the fixed benchmark label universe")
    per_rule = {}
    for label in label_set:
        count = counts[label]
        tp, fp, fn = count["tp"], count["fp"], count["fn"]
        denominator = 2 * tp + fp + fn
        per_rule[label] = {"tp": tp, "fp": fp, "fn": fn, "support": tp + fn,
                           "f1": 2 * tp / denominator if denominator else 0.0}
    n = len(rows)
    normal = [r for r in rows if not r.get("expected_rules")]
    claim_count = sum(r.get("cited_claims", 0) for r in rows)
    return {"samples": n, "completed": sum(r.get("error") is None for r in rows),
            "failures": sum(r.get("error") is not None for r in rows),
            "unique_cases": len({r["case_id"] for r in rows}),
            "families": len({r["family_id"] for r in rows}),
            "rule_labels": label_set, "supported_rule_labels": sum(c["support"] > 0 for c in per_rule.values()),
            "observed_rule_labels": observed,
            "observed_rule_macro_f1": sum(per_rule[label]["f1"] for label in observed) / len(observed) if observed else None,
            "unexpected_predicted_rule_count": sum(len(set(row.get("scored_predicted_rules", [])) - set(observed)) for row in rows),
            "macro_rule_f1_label_policy": "fixed_labels_zero_division_0" if labels is not None else "observed_union_zero_division_0",
            "macro_rule_f1": sum(c["f1"] for c in per_rule.values()) / len(per_rule) if per_rule else None,
            "exact_rule_match_rate": sum(r.get("exact_rule_match", False) for r in rows) / n if n else None,
            "status_match_rate": sum(r.get("status_match", False) for r in rows) / n if n else None,
            "uncertainty_rate": sum(r.get("predicted_status") == "insufficient" for r in rows) / n if n else None,
            "normal_false_positive_rate": sum(bool(r.get("predicted_rules")) for r in normal) / len(normal) if normal else None,
            "normal_completion_rate": sum(r.get("error") is None for r in normal) / len(normal) if normal else None,
            "citation_locatability_rate": sum(r.get("valid_cited_claims", 0) for r in rows) / claim_count if claim_count else None,
            "paid_requests": sum(r.get("paid_requests", 0) for r in rows),
            "tokens": sum(r.get("usage", {}).get("tokens", 0) for r in rows),
            "reserved_tokens": sum(r.get("usage", {}).get("reserved_tokens", 0) for r in rows),
            "duration_seconds": sum(r.get("duration_seconds", 0) for r in rows), "per_rule": per_rule,
            "family_bootstrap": family_bootstrap(rows)}


def request_usage_totals(rows: list[dict]) -> dict:
    """Count returned provider fields with explicit coverage, never fill gaps."""
    requests = [request for row in rows for request in row.get("model_requests", [])]
    result = {"reported_responses": len(requests), "fields": {}}
    for counter_name in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        values = [request["request_usage"][counter_name] for request in requests
                  if isinstance(request.get("request_usage"), dict)
                  and type(request["request_usage"].get(counter_name)) is int]
        result["fields"][counter_name] = {"reported_responses": len(values), "sum": sum(values) if values else None}
    for parent, counter_name in (("prompt_tokens_details", "cached_tokens"), ("completion_tokens_details", "reasoning_tokens")):
        values = [request["request_usage"][parent][counter_name] for request in requests
                  if isinstance(request.get("request_usage"), dict)
                  and isinstance(request["request_usage"].get(parent), dict)
                  and type(request["request_usage"][parent].get(counter_name)) is int]
        result["fields"][f"{parent}.{counter_name}"] = {"reported_responses": len(values), "sum": sum(values) if values else None}
    return result


def _atomic_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    # Windows readers (e.g. a cost monitor) can briefly block the replacement.
    # Retry only this file operation; never repeat a paid dispatch or drop data.
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.02 * 2 ** attempt)


def _new_json(path: Path, payload: dict):
    """Reserve a fresh report exclusively before any paid request is allowed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
    except FileExistsError:
        raise ValueError("评测报告已存在；请使用新的报告路径，禁止覆盖已有运行并重复付费") from None


def _group_summary(rows: list[dict], *, labels: list[str], observed_labels: list[str]) -> dict:
    scored = [row for row in rows if row["scorable"]]
    group = metrics(scored, labels=labels, observed_labels=observed_labels)
    # Quality has no invented truth; financial observations remain reportable
    # even when the evaluator's truth file is missing or invalid.
    group.update(cost_scope="all_attempted_samples", cost_sample_rows=len(rows),
                 paid_requests=sum(row.get("paid_requests", 0) for row in rows),
                 tokens=sum(row.get("usage", {}).get("tokens", 0) for row in rows),
                 reserved_tokens=sum(row.get("usage", {}).get("reserved_tokens", 0) for row in rows),
                 duration_seconds=sum(row.get("duration_seconds", 0) for row in rows),
                 request_usage_totals=request_usage_totals(rows),
                 unscorable=sum(not row["scorable"] for row in rows))
    return group


def _csv_report(path: Path, rows: list[dict]):
    columns = ["case_id", "family_id", "split", "framework", "method", "repeat", "expected_rules",
               "predicted_rules", "expected_status", "predicted_status", "exact_rule_match", "status_match",
               "paid_requests", "tokens", "reserved_tokens", "duration_seconds", "model", "configured_model",
               "prompt_version", "tool_version", "model_parameters", "request_parameters", "request_attempts",
               "request_not_dispatched", "model_requests", "error"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        flat = {key: row.get(key) for key in columns}
        for key in ("expected_rules", "predicted_rules", "model_parameters", "request_parameters", "request_attempts",
                    "request_not_dispatched", "model_requests", "error"):
            flat[key] = json.dumps(row.get(key), ensure_ascii=False)
        flat["tokens"] = row.get("usage", {}).get("tokens", 0)
        flat["reserved_tokens"] = row.get("usage", {}).get("reserved_tokens", 0)
        writer.writerow(flat)
    path.write_text(output.getvalue(), encoding="utf-8-sig")


async def run_benchmark(options: BenchmarkOptions, *, gateway=None, runner=investigate) -> dict:
    """Injected gateway/runner are for tests; CLI only builds DeepSeekGateway."""
    options.validate()
    if options.report.exists() or options.report.with_suffix(".csv").exists():
        raise ValueError("评测报告或CSV已存在；请使用新的报告路径，禁止覆盖已有运行并重复付费")
    if options.report == options.report.with_suffix(".csv"):
        raise ValueError("JSON评测报告路径不能与CSV导出路径相同")
    if any(mode != "rules" for mode in options.modes) and gateway is None:
        configuration = _model_configuration(options.env_file)
        key = configuration["DEEPSEEK_API_KEY"].strip()
        if not key:
            raise ValueError("模型基准需要 DEEPSEEK_API_KEY；未调用模型，也不会生成模拟性能结果")
        gateway = DeepSeekGateway(key, base_url=configuration["DEEPSEEK_BASE_URL"] or "https://api.deepseek.com",
                                  model=configuration["DEEPSEEK_MODEL"] or "deepseek-flash")
    manifest = _read_object(options.dataset / "manifest.json")
    if manifest.get("synthetic") is not True:
        raise ValueError("Dataset manifest must be explicitly synthetic")
    selected = select_cases(manifest, options.split, options.max_cases)
    if not selected:
        raise ValueError("No cases available in the selected split")
    methods = [(mode, None) for mode in options.modes]
    methods.extend(("active", ablation) for ablation in options.ablations)
    budget = PaidRequestBudget(options.max_paid_requests)
    started = time.perf_counter()
    rows = []
    provider_failure = None
    labels = sorted({rule["rule_id"] for rule in rule_catalog()})
    report = {"schema_version": "agent-benchmark-v5", "created_at": datetime.now(UTC).isoformat(),
              "process": {"pid": os.getpid(), "module": "benchmarks.run_agents"},
              "dataset_version": manifest.get("version"), "split": options.split, "repeat": options.repeat,
              "case_limit": options.max_cases, "selected_case_ids": [c["case_id"] for c in selected],
              "metric_scope": "visible_synthetic_rule_labels_and_status_not_root_cause",
              "prediction_policy": "cited_claim_assessment_finding_only_default_finding_for_legacy",
              "fixed_rule_labels": labels, "macro_rule_f1_label_policy": "fixed_catalog_zero_division_0",
              "active_mechanism": "known_hypothesis_pairs_per_server_cost_with_snapshot_query_execution",
              "paid_request_limit": budget.maximum, "limitations": LIMITATIONS,
              "paid_requests": 0,
              "paid_request_accounting": "attempts_reserved_before_dispatch_not_provider_billing; interrupted_current_sample_is_incomplete",
              "logical_request_accounting": "request_parameters_are_planned_chats_not_actual_dispatches",
              "physical_request_accounting": "request_attempts_are_dispatch_authorized_attempts_not_provider_billing",
              "runtime_configuration": {"prompt_version": PROMPT_VERSION, "tool_version": TOOL_VERSION,
                                        "model_parameters": deepcopy(MODEL_PARAMETERS),
                                        "budget_limits": {"calls": RunBudget().max_calls,
                                                          "tokens": RunBudget().max_tokens,
                                                          "tools": RunBudget().max_tools},
                                        "source_hashes": RUNTIME_SOURCE_HASHES},
              "status": "running", "cases": rows, "groups": {}}
    _new_json(options.report, report)
    for repeat in range(options.repeat):
        for case_index, entry in enumerate(selected):
            # Rotate execution order to reduce systematic starvation of the last
            # method if the global spending limit is reached.
            offset = (repeat + case_index) % len(methods)
            order = methods[offset:] + methods[:offset]
            for mode, ablation in order:
                method = mode if ablation is None else f"{mode}:{ablation}"
                sample_id = f"{entry['case_id']}:{repeat + 1}:{method}"
                row = {"case_id": entry["case_id"], "family_id": entry["family_id"], "split": entry.get("split"),
                       "framework": entry.get("framework"), "method": method, "repeat": repeat + 1,
                       "usage": {"calls": 0, "tokens": 0, "tools": 0, "reserved_tokens": 0},
                       "model": None, "configured_model": getattr(gateway, "model", None) if mode != "rules" else None,
                       "prompt_version": PROMPT_VERSION if mode != "rules" else None,
                       "tool_version": TOOL_VERSION if mode != "rules" else None,
                       "model_parameters": deepcopy(MODEL_PARAMETERS) if mode != "rules" else None,
                       "request_parameters": [], "request_attempts": [], "request_not_dispatched": [],
                       "model_requests": [], "evidence_selections": [], "execution_rules": rule_identity(),
                       "error": None, "predicted_rules": [], "predicted_status": None}
                sample_started = time.perf_counter()

                def persist_current(event):
                    report["current_sample"] = {"sample_id": sample_id, "case_id": row["case_id"],
                                                "method": method, "repeat": repeat + 1,
                                                "usage": dict(row["usage"]), "current_event": event,
                                                "configured_model": row["configured_model"],
                                                "prompt_version": row["prompt_version"], "tool_version": row["tool_version"],
                                                "model_parameters": row["model_parameters"],
                                                "current_role": row.get("current_role"), "current_stage": row.get("current_stage"),
                                                "request_parameters": row["request_parameters"],
                                                "request_attempts": row["request_attempts"],
                                                "request_not_dispatched": row["request_not_dispatched"],
                                                "model_requests": row["model_requests"],
                                                "validation_failures": row.get('validation_failures', []),
                                                "paid_requests": budget.calls_by_sample[sample_id]}
                    report["paid_requests"] = budget.used
                    report["duration_seconds"] = time.perf_counter() - started
                    _atomic_json(options.report, report)

                persist_current("sample_started")
                try:
                    observable = load_observable(options.dataset, entry)
                    records, policy = observable["records"], observable["policy"]
                    scoring_records = records
                    row.update(family_id=observable["family_id"], split=observable["split"], framework=policy["framework"])
                    if mode == "rules":
                        findings = check_records(records, policy)
                        status = "insufficient" if any(f["status"] == "insufficient" for f in findings) else "supported" if findings else "normal"
                        result = {"findings": findings, "status": status, "usage": row["usage"]}
                    else:
                        if provider_failure is not None:
                            raise GatewayError("provider_unavailable_not_attempted",
                                               "同一配置的服务已拒绝访问；保留未执行样本，未再调用模型",
                                               http_status=provider_failure["http_status"])
                        if budget.used >= budget.maximum:
                            raise BudgetExceeded("整个基准的付费请求预算耗尽；保留此未执行样本")
                        config: dict = {}
                        if ablation:
                            config = {"ablation": ablation, "synthetic_evaluation": True}
                            if ablation == "no_time_view":
                                future = json.loads((options.dataset / "on_demand" / f"{entry['case_id']}.json").read_text(encoding="utf-8"))
                                if not isinstance(future, list):
                                    raise ValueError("Invalid on-demand evidence list")
                                config["comparison_records"] = [*records, *_public_records(future)]
                                scoring_records = config["comparison_records"]

                        async def trace(step):
                            if "usage" in step:
                                row["usage"] = dict(step["usage"])
                            if step.get("event") == "agent_started":
                                row["current_role"] = step.get("role")
                                row["current_stage"] = "role_initial"
                            elif step.get("event") in {"evidence_context_refreshed", "model_truncation_recovery",
                                                      "schema_repair", "evidence_stagnation"}:
                                row["current_stage"] = step["event"]
                            elif step.get("event") == "stage_handoff":
                                row["current_stage"] = "tool_stage_finalization"
                            if step.get("event") == "model_usage":
                                row["model_requests"].append({k: step.get(k) for k in (
                                    "request_index", "request_usage", "provider_model", "provider_system_fingerprint",
                                    "context_bytes", "response_token_limit")})
                            if step.get('event') == 'output_validation_failed':
                                # The runtime emits bounded static field paths, never rejected values.
                                row.setdefault('validation_failures', []).append({
                                    'role': row.get('current_role'), 'stage': row.get('current_stage'),
                                    'request_count': row['usage'].get('calls'),
                                    'diagnostics': [{k: item[k] for k in ('code', 'path', 'argument_error') if k in item}
                                                    for item in step.get('diagnostics', [])[:20]],
                                })
                            # Progress files contain no private model reasoning.
                            row["last_event"] = step.get("event")
                            persist_current(row["last_event"])

                        bounded = BoundedGateway(gateway, budget, sample_id,
                                                 context=lambda: {"role": row.get("current_role"),
                                                                  "stage": row.get("current_stage")})
                        row["request_parameters"] = bounded.request_parameters
                        row["request_attempts"] = bounded.request_attempts
                        row["request_not_dispatched"] = bounded.request_not_dispatched
                        result = await asyncio.wait_for(runner(records, policy, neutral_finding(records), mode=mode,
                                                               gateway=bounded, on_step=trace, checkpoint_config=config),
                                                        timeout=options.max_seconds_per_case)
                    row.update(_prediction(result, mode, scoring_records, policy))
                    row["usage"] = result.get("usage", row["usage"])
                    row["model"] = result.get("model")
                    row["prompt_version"] = result.get("prompt_version", row["prompt_version"])
                    row["tool_version"] = result.get("tool_version", row["tool_version"])
                    row["model_parameters"] = result.get("model_parameters", row["model_parameters"])
                    row["configured_model"] = result.get("configured_model", row["configured_model"])
                    row["execution_rules"] = result.get("execution_rules", row["execution_rules"])
                    row["fingerprint"] = result.get("fingerprint")
                    row["model_proposals"] = result.get("proposals", [])
                    row["model_requests"] = result.get("model_requests", row["model_requests"])
                    row["evidence_selections"] = result.get("evidence_selections", [])
                    if result.get("status") in TERMINAL_FAILURES:
                        row["error"] = result.get("error") or {"code": result["status"], "message": "运行未完成"}
                except TimeoutError:
                    row["error"] = {"code": "case_timeout", "message": "当前样本运行超时；已用预算保留"}
                except GatewayError as exc:
                    row["error"] = {"code": exc.code, "message": str(exc)}
                    if exc.http_status is not None:
                        row["error"]["http_status"] = exc.http_status
                except Exception as exc:
                    # Do not copy source document text, hidden labels, endpoint
                    # responses, or configuration secrets into an error field.
                    row["error"] = {"code": "evaluation_error", "message": type(exc).__name__}
                if (row.get("error") or {}).get("code") == "model_access" and row["error"].get("http_status") in {401, 402, 403}:
                    provider_failure = deepcopy(row["error"])
                    report["provider_failure"] = provider_failure
                row["duration_seconds"] = time.perf_counter() - sample_started
                row["paid_requests"] = budget.calls_by_sample[sample_id]
                try:
                    row.update(_score_truth(options.dataset, entry["case_id"], row))
                except (OSError, ValueError, KeyError):
                    # A missing truth file is a broken evaluation, not a normal
                    # case. Never substitute an empty answer label.
                    row["runtime_error"] = row["error"]
                    row["error"] = {"code": "truth_unavailable", "message": "评测真值缺失或无效，无法评分"}
                    row["scorable"] = False
                else:
                    row["scorable"] = True
                rows.append(row)
                report.pop("current_sample", None)
                report["paid_requests"] = budget.used
                report["duration_seconds"] = time.perf_counter() - started
                _atomic_json(options.report, report)
    groups = {}
    # Truth-derived coverage is computed only after every runtime has finished.
    # The same evaluator-owned label set is then used by every comparison group.
    observed_labels = sorted({label for row in rows if row["scorable"] for label in row.get("expected_rules", [])})
    report["observed_rule_labels"] = observed_labels
    for method in sorted({r["method"] for r in rows}):
        method_rows = [r for r in rows if r["method"] == method]
        group = _group_summary(method_rows, labels=labels, observed_labels=observed_labels)
        group["by_framework"] = {framework: _group_summary([r for r in method_rows if r["framework"] == framework], labels=labels,
                                                          observed_labels=observed_labels)
                                   for framework in sorted({r["framework"] for r in method_rows})}
        group["by_split"] = {split: _group_summary([r for r in method_rows if r["split"] == split], labels=labels,
                                                  observed_labels=observed_labels)
                               for split in sorted({r["split"] for r in method_rows})}
        group["by_family"] = {family: _group_summary([r for r in method_rows if r["family_id"] == family], labels=labels,
                                                     observed_labels=observed_labels)
                                for family in sorted({r["family_id"] for r in method_rows})}
        groups[method] = group
    report.update(groups=groups, status="completed_with_failures" if any(r["error"] for r in rows) else "completed",
                  paid_requests=budget.used, duration_seconds=time.perf_counter() - started,
                  request_usage_totals=request_usage_totals(rows))
    _atomic_json(options.report, report)
    _csv_report(options.report.with_suffix(".csv"), rows)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=Path(".local/datasets"))
    parser.add_argument("--report", type=Path, default=Path(".local/reports/agent-comparison.json"))
    parser.add_argument("--split", choices=sorted(SPLITS), default="development")
    parser.add_argument("--modes", choices=sorted(MODES), nargs="+", default=["rules", "single", "multi", "active"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--max-cases", type=int, default=8)
    parser.add_argument("--max-paid-requests", type=int, default=240)
    parser.add_argument("--max-seconds-per-case", type=float, default=900)
    parser.add_argument("--ablations", choices=sorted(ABLATIONS), nargs="*", default=[])
    parser.add_argument("--env-file", type=Path, default=None,
                        help="仅显式指定时读取本地环境文件；环境变量优先，内容不写入报告")
    args = parser.parse_args(argv)
    options = BenchmarkOptions(**{**vars(args), "modes": tuple(args.modes), "ablations": tuple(args.ablations)})
    try:
        result = asyncio.run(run_benchmark(options))
    except (ValueError, OSError) as exc:
        print(f"基准未开始：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "report": str(options.report.resolve()),
                      "samples": len(result["cases"]), "paid_requests": result["paid_requests"],
                      "metric_scope": result["metric_scope"]}, ensure_ascii=False))
    return 1 if result["status"] == "completed_with_failures" else 0


if __name__ == "__main__":
    raise SystemExit(main())
