"""Independent BPI2019 source-process observation rules (Apache-2.0).

Pure functions over a verified ``ProcessSnapshot``; no ledger, database, model
or network access. Rules live in their own ``BPI2019_PROCESS`` namespace and
describe compatibility with the dataset's documented process policy. They are
not CAS/SCAS accounting rules, anomaly labels or business-completion findings.

Statuses:
- ``compatible``: recorded events are consistent with the documented constraint.
- ``needs_review``: an observable deviation from that constraint, to be verified.
- ``insufficient``: the recorded events cannot decide the constraint.
- ``out_of_scope``: the constraint does not apply or the policy is unsupported.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path

from .process_events import POLICY_SOURCE, BusinessEvent, ProcessSnapshot, ProcessTrace, _check_scope

RULE_PACK_VERSION = "bpi2019-process-rules-v1"
STATUSES = ("compatible", "needs_review", "insufficient", "out_of_scope")
MAX_CITED_EVENTS = 20
# The fingerprint binds the loaded implementation, as policy_consistency does.
_SOURCE_SHA256 = hashlib.sha256(b"\0".join(
    name.encode() + b"\0" + Path(__file__).with_name(name).read_text(encoding="utf-8").encode()
    for name in ("process_events.py", "process_rules.py")
)).hexdigest()

THREE_WAY = ("three_way_invoice_before_gr_allowed", "three_way_gr_based_invoice_verification")
CLASSIFIED = (*THREE_WAY, "two_way_no_goods_receipt_required", "consignment_invoice_outside_item_process")
UNSUPPORTED_CHECKS = (
    "goods_receipt_to_invoice_value_matching", "stage_value_summation_and_rounding_tolerance",
    "per_batch_receipt_invoice_clearing_pairing", "cancellation_target_identification_and_netting",
    "payment_block_state_reconstruction", "clearing_as_bank_payment", "duplicate_posting_diagnosis",
    "anomaly_root_cause", "business_completion",
)

# Each passage is a short verbatim quote from the TU/e dataset description,
# re-read on 2026-10-11; the URL is the dataset landing page.
_PASSAGES = {
    "gr_after": "For these items, the value of the goods receipt message should be matched against "
                "the value of an invoice receipt message.",
    "gr_before": "invoices can be entered before the goods are receipt, but they are blocked until goods "
                 "are received.",
    "two_way": "there is no separate goods receipt message required.",
    "consignment": "there are no invoices on PO level as this is handled fully in a separate process.",
    "batches": "For logistical services, there may even be hundreds of goods receipt messages for one line item.",
}

RULES = (
    {"rule_id": "BPI2019_PROCESS.POLICY_CLASSIFICATION", "title": "来源流程策略可识别",
     "applies_to": "all", "passages": ("gr_after", "gr_before", "two_way", "consignment"),
     "required_evidence": ("trace: Item Category, Item Type, Goods Receipt, GR-Based Inv. Verif.",),
     "not_applicable": ("未知或冲突的标志不按非空字符串真值推断。",)},
    {"rule_id": "BPI2019_PROCESS.ACTIVITY_VOCABULARY", "title": "活动均在已审阅目录内",
     "applies_to": "all", "passages": (),
     "required_evidence": ("events: concept:name",),
     "not_applicable": ("未知活动保留为证据，不丢弃也不推断含义。",)},
    {"rule_id": "BPI2019_PROCESS.EVENT_FIELD_QUALITY", "title": "事件时间与数值字面量可读",
     "applies_to": "all", "passages": (),
     "required_evidence": ("events: time:timestamp, Cumulative net worth (EUR)",),
     "not_applicable": ("0 是明确观察值，与缺失／非法分开计数。",)},
    {"rule_id": "BPI2019_PROCESS.GOODS_RECEIPT_EXPECTATION", "title": "收货记录与来源策略相容",
     "applies_to": CLASSIFIED, "passages": ("gr_after", "gr_before", "two_way"),
     "required_evidence": ("events: Record Goods Receipt", "events: Record Service Entry Sheet"),
     "not_applicable": ("完整日志片段不表示业务已完成，未记录收货不判定为缺失。",
                        "服务验收单不视为实物收货。")},
    {"rule_id": "BPI2019_PROCESS.ITEM_INVOICE_EXPECTATION", "title": "采购项级收票记录与来源策略相容",
     "applies_to": CLASSIFIED, "passages": ("consignment", "gr_after"),
     "required_evidence": ("events: Record Invoice Receipt, Record Subsequent Invoice, Clear Invoice",),
     "not_applicable": ("寄售流程不期待采购项级发票，不报缺票。", "供应商开票与系统收票分开观察。")},
    {"rule_id": "BPI2019_PROCESS.GR_BASED_INVOICE_ORDER", "title": "基于收货验票的首次收货与首次收票先后",
     "applies_to": THREE_WAY, "passages": ("gr_after", "gr_before"),
     "required_evidence": ("events: first Record Goods Receipt, first Record Invoice Receipt",),
     "not_applicable": ("允许先收票的分类不要求实际先收票，任一先后均相容。",
                        "同刻事件只保留源顺序，不确定因果先后。")},
    {"rule_id": "BPI2019_PROCESS.CLEARING_AFTER_GOODS_RECEIPT", "title": "三方匹配的首次清账不早于首次收货",
     "applies_to": THREE_WAY, "passages": ("gr_before",),
     "required_evidence": ("events: first Record Goods Receipt, first Clear Invoice",),
     "not_applicable": ("只比较首次事件；逐批配对和金额匹配未实现。", "清账不等于银行付款。")},
    {"rule_id": "BPI2019_PROCESS.REPEATED_STAGE_EVENTS", "title": "多批收货、收票或清账",
     "applies_to": "all", "passages": ("batches",),
     "required_evidence": ("events: stage counts",),
     "not_applicable": ("重复活动或相同数值不判定为重复记账。",)},
    {"rule_id": "BPI2019_PROCESS.CANCELLATION_EVENTS", "title": "取消活动的对象与冲销",
     "applies_to": "all", "passages": (),
     "required_evidence": ("events: Cancel Goods Receipt, Cancel Invoice Receipt, Cancel Subsequent Invoice",),
     "not_applicable": ("取消活动的正数源值不作为新增采购；没有原生编号时不确定冲销对象。",)},
)
_RULES_BY_ID = {rule["rule_id"]: rule for rule in RULES}


def rule_pack_identity() -> dict[str, str]:
    return {"version": RULE_PACK_VERSION, "source_sha256": _SOURCE_SHA256}


def rule_catalog() -> list[dict]:
    """Each rule with source passages, conditions and its validation state."""
    return [{
        "rule_id": rule["rule_id"], "title": rule["title"], "namespace": "BPI2019_PROCESS",
        "applies_to": list(rule["applies_to"]) if rule["applies_to"] != "all" else "all",
        "required_evidence": list(rule["required_evidence"]), "not_applicable": list(rule["not_applicable"]),
        "source": {"url": POLICY_SOURCE, "passages": [_PASSAGES[key] for key in rule["passages"]],
                   "retrieved_on": "2026-10-11"},
        "validation_status": "experimental", "status": "experimental",
        "regression": "backend/tests/test_process_rules.py (independent hand-authored fixtures)",
        "accounting_rule": False, "rule_pack": rule_pack_identity(),
    } for rule in RULES]


def _timed(events: list[BusinessEvent]) -> tuple[datetime | None, bool]:
    """Earliest timestamp of a stage; the flag is False if any is unreadable."""
    times = [event.occurred_at for event in events]
    if not times or any(value is None for value in times):
        return None, False
    return min(times), True


def _first_order(first: list[BusinessEvent], second: list[BusinessEvent]) -> str:
    a, a_ok = _timed(first)
    b, b_ok = _timed(second)
    if not first or not second:
        return "missing"
    if not a_ok or not b_ok:
        return "unreadable"
    return "same" if a == b else "before" if a < b else "after"


def _result(trace: ProcessTrace, rule_id: str, status: str, reason: str, events=(), observed=None) -> dict:
    assert status in STATUSES
    cited = list(dict.fromkeys(event.evidence_id for event in events))
    return {"rule_id": rule_id, "title": _RULES_BY_ID[rule_id]["title"], "status": status, "reason": reason,
            "evidence_ids": [trace.evidence_id, *cited[:MAX_CITED_EVENTS]],
            "cited_event_total": len(cited), "cited_events_truncated": len(cited) > MAX_CITED_EVENTS,
            "observed": observed or {}}


def events_by_trace(snapshot: ProcessSnapshot) -> dict[str, list[BusinessEvent]]:
    index: dict[str, list[BusinessEvent]] = {}
    for event in snapshot.events:
        index.setdefault(event.source_trace_id, []).append(event)
    return index


def evaluate_trace(snapshot: ProcessSnapshot, trace: ProcessTrace, *, index=None) -> dict:
    """Evaluate one trace of this snapshot; never mutates or aggregates values."""
    if not any(item is trace for item in snapshot.traces):
        raise ValueError("Trace is not part of this process snapshot")
    events = (index if index is not None else events_by_trace(snapshot)).get(trace.source_trace_id, [])
    if tuple(event.source_event_id for event in events) != trace.event_ids:
        raise ValueError("Trace events disagree with the snapshot's source order")
    by_type: dict[str, list[BusinessEvent]] = {}
    for event in events:
        by_type.setdefault(event.event_type, []).append(event)
    goods, invoices = by_type.get("goods_receipt_recorded", []), by_type.get("invoice_receipt_recorded", [])
    clearing, services = by_type.get("invoice_clearing_recorded", []), by_type.get("service_entry_recorded", [])
    subsequent = by_type.get("subsequent_invoice_recorded", [])
    vendor_invoices = by_type.get("vendor_invoice_created", [])
    policy = trace.policy
    kind = policy.classification if policy.status == "classified" else None
    item_type = trace.trace_attributes.get("Item Type")
    results = []

    # 1. Policy classification
    if policy.status == "classified":
        results.append(_result(trace, "BPI2019_PROCESS.POLICY_CLASSIFICATION", "compatible",
                               "四项源字段与官方说明的流程分类一致。", observed={"classification": kind}))
    elif policy.status == "needs_review":
        results.append(_result(trace, "BPI2019_PROCESS.POLICY_CLASSIFICATION", "needs_review",
                               "源标志缺失、非精确布尔字面量或与分类冲突；流程策略待核实。",
                               observed={"issues": list(policy.issues)}))
    else:
        results.append(_result(trace, "BPI2019_PROCESS.POLICY_CLASSIFICATION", "out_of_scope",
                               "采购项分类不在已审阅的四类流程内。", observed={"issues": list(policy.issues)}))

    # 2. Vocabulary
    unknown = [event for event in events if event.mapping_status == "unsupported"]
    results.append(_result(trace, "BPI2019_PROCESS.ACTIVITY_VOCABULARY",
                           "needs_review" if unknown else "compatible",
                           "存在未审阅活动，已保留原值，含义未推断。" if unknown else "全部活动在已审阅目录内。",
                           unknown, {"unsupported_activities": sorted({e.activity_raw or "<missing>" for e in unknown})}))

    # 3. Field quality: zero is an explicit value, never missing.
    unreadable = [event for event in events if event.quality_issues and event.mapping_status == "mapped"]
    zero = [e for e in events if e.source_numeric_status == "valid" and Decimal(e.source_numeric_text) == 0]
    results.append(_result(trace, "BPI2019_PROCESS.EVENT_FIELD_QUALITY",
                           "insufficient" if unreadable else "compatible",
                           "部分事件时间或数值不可读，相关先后与数值观察不能判断。" if unreadable
                           else "全部事件时间带时区且数值字面量有效。", unreadable,
                           {"unreadable_event_count": len(unreadable), "zero_value_event_count": len(zero),
                            "missing_numeric_event_count": sum(e.source_numeric_status == "missing" for e in events),
                            "invalid_numeric_event_count": sum(e.source_numeric_status == "invalid" for e in events)}))

    # 4. Goods receipt expectation; service entries never count as receipts.
    rule = "BPI2019_PROCESS.GOODS_RECEIPT_EXPECTATION"
    counts = {"goods_receipt_events": len(goods), "service_entry_events": len(services)}
    if kind is None:
        results.append(_result(trace, rule, "out_of_scope", "流程分类未确定，不套用收货预期。", observed=counts))
    elif kind == "two_way_no_goods_receipt_required":
        results.append(_result(trace, rule, "needs_review" if goods else "compatible",
                               "两方匹配不要求独立收货，但记录了收货事件；待核实分类或流程变更。" if goods
                               else "两方匹配不要求独立收货消息；未记录收货与策略相容。", goods, counts))
    elif goods:
        results.append(_result(trace, rule, "compatible", "来源策略期待收货，已记录收货事件。", goods, counts))
    elif services and item_type == "Service":
        results.append(_result(trace, rule, "insufficient",
                               "只有服务验收单，未记录收货；服务验收不等同于实物收货，无法判断。", services, counts))
    else:
        results.append(_result(trace, rule, "insufficient",
                               "来源策略期待收货但日志未记录；完整日志片段不表示业务已完成，不判定缺失。",
                               observed=counts))

    # 5. Item-level invoice expectation (vendor invoices observed separately).
    rule = "BPI2019_PROCESS.ITEM_INVOICE_EXPECTATION"
    item_invoices = invoices + subsequent
    counts = {"invoice_receipt_events": len(invoices), "subsequent_invoice_events": len(subsequent),
              "clearing_events": len(clearing), "vendor_invoice_events": len(vendor_invoices)}
    if kind is None:
        results.append(_result(trace, rule, "out_of_scope", "流程分类未确定，不套用收票预期。", observed=counts))
    elif kind == "consignment_invoice_outside_item_process":
        handled = item_invoices + clearing
        results.append(_result(trace, rule, "needs_review" if handled else "compatible",
                               "寄售流程在采购项级记录了收票或清账；待核实分类。" if handled
                               else "寄售发票在独立流程处理；采购项级无收票不报缺票。", handled, counts))
    elif item_invoices:
        results.append(_result(trace, rule, "compatible", "已记录采购项级收票事件。", item_invoices, counts))
    else:
        results.append(_result(trace, rule, "insufficient",
                               "未记录系统收票；供应商开票事件不替代收票，也不判定缺票。", vendor_invoices, counts))

    # 6. First goods receipt vs first invoice receipt.
    rule = "BPI2019_PROCESS.GR_BASED_INVOICE_ORDER"
    order = _first_order(invoices, goods)
    observed = {"first_invoice_relative_to_first_goods_receipt": order}
    cited = goods[:1] + invoices[:1]
    if kind not in THREE_WAY:
        results.append(_result(trace, rule, "out_of_scope", "仅适用于三方匹配分类。", observed=observed))
    elif kind == "three_way_invoice_before_gr_allowed":
        results.append(_result(trace, rule, "compatible",
                               "该分类允许先收票，但不要求实际先收票；任一先后均相容。", cited, observed))
    elif order == "missing":
        results.append(_result(trace, rule, "insufficient", "缺少收货或收票事件，无法比较先后。", cited, observed))
    elif order == "unreadable":
        results.append(_result(trace, rule, "insufficient", "相关事件时间不可读，无法比较先后。", cited, observed))
    elif order == "same":
        results.append(_result(trace, rule, "insufficient", "首次收票与首次收货同刻，因果先后未定。", cited, observed))
    elif order == "before":
        results.append(_result(trace, rule, "needs_review",
                               "基于收货验票的分类中首次收票早于首次收货；过程差异待核实，不是错账结论。", cited, observed))
    else:
        results.append(_result(trace, rule, "compatible", "首次收货早于首次收票，与基于收货验票相容。", cited, observed))

    # 7. First clearing must not precede first goods receipt in three-way flows.
    rule = "BPI2019_PROCESS.CLEARING_AFTER_GOODS_RECEIPT"
    order = _first_order(clearing, goods)
    observed = {"first_clearing_relative_to_first_goods_receipt": order,
                "clearing_events": len(clearing), "goods_receipt_events": len(goods)}
    cited = goods[:1] + clearing[:1]
    if kind not in THREE_WAY:
        results.append(_result(trace, rule, "out_of_scope", "仅适用于三方匹配分类。", observed=observed))
    elif not clearing:
        results.append(_result(trace, rule, "insufficient",
                               "未记录清账；不判定漏付款，清账本身也不证明银行付款。", cited, observed))
    elif not goods:
        results.append(_result(trace, rule, "needs_review",
                               "记录了清账但日志未见收货；按说明发票在收货前被冻结，过程差异待核实。", cited, observed))
    elif order == "unreadable":
        results.append(_result(trace, rule, "insufficient", "相关事件时间不可读，无法比较先后。", cited, observed))
    elif order == "same":
        results.append(_result(trace, rule, "insufficient", "首次清账与首次收货同刻，因果先后未定。", cited, observed))
    elif order == "before":
        results.append(_result(trace, rule, "needs_review",
                               "首次清账早于首次收货；按说明发票在收货前被冻结，过程差异待核实。", cited, observed))
    else:
        results.append(_result(trace, rule, "compatible",
                               "首次清账晚于首次收货；逐批配对与金额匹配未检查。", cited, observed))

    # 8. Repeated stage events are partial fulfilment candidates, never duplicates.
    rule = "BPI2019_PROCESS.REPEATED_STAGE_EVENTS"
    repeated = {name: len(items) for name, items in (("goods_receipt", goods), ("invoice_receipt", invoices),
                                                      ("clearing", clearing)) if len(items) > 1}
    if repeated:
        results.append(_result(trace, rule, "compatible",
                               "官方说明允许一个采购项多次收货、收票和清账；不判定重复记账。",
                               [e for e in goods + invoices + clearing], {"repeated_stage_counts": repeated}))
    else:
        results.append(_result(trace, rule, "out_of_scope", "没有多次记录的收货、收票或清账。",
                               observed={"repeated_stage_counts": {}}))

    # 9. Cancellations: targets and netting are not identifiable.
    rule = "BPI2019_PROCESS.CANCELLATION_EVENTS"
    cancels = [event for event in events if event.phase == "cancellation"]
    if cancels:
        results.append(_result(trace, rule, "insufficient",
                               "存在取消活动；没有原生单据编号，不能确定取消对象或冲销金额，正数源值不作为新增采购。",
                               cancels, {"cancellation_events": len(cancels),
                                         "cancellation_values_treated_as_new_purchase": False}))
    else:
        results.append(_result(trace, rule, "out_of_scope", "没有取消活动。", observed={"cancellation_events": 0}))

    statuses = {item["status"] for item in results}
    conclusion = ("out_of_scope" if kind is None and policy.status == "unsupported" else
                  "needs_review" if "needs_review" in statuses else
                  "insufficient" if "insufficient" in statuses else "compatible")
    return {
        "rule_pack": rule_pack_identity(), "dataset_scope_id": trace.dataset_scope_id,
        "snapshot_id": trace.snapshot_id, "source_trace_id": trace.source_trace_id,
        "trace_alias": trace.trace_alias, "trace_evidence_id": trace.evidence_id,
        "classification": policy.classification, "conclusion": conclusion, "results": results,
        "unsupported_checks": list(UNSUPPORTED_CHECKS),
        "result_kind": "source_process_policy_observations_not_anomaly_findings",
        "accounting_rules_applied": False, "journal_entries_created": 0,
        "business_completion": "not_determined", "anomaly_truth_available": False,
    }


def review_trace(snapshot: ProcessSnapshot, *, dataset_scope_id: str, snapshot_id: str,
                 source_trace_id: str) -> dict:
    _check_scope(snapshot, dataset_scope_id, snapshot_id)
    trace = next((item for item in snapshot.traces if item.source_trace_id == source_trace_id), None)
    if trace is None:
        raise KeyError("Trace is not present in this process snapshot")
    return evaluate_trace(snapshot, trace)


def summarize_snapshot(snapshot: ProcessSnapshot, *, dataset_scope_id: str, snapshot_id: str) -> dict:
    """Counts of rule outcomes; these are observations, not accuracy."""
    _check_scope(snapshot, dataset_scope_id, snapshot_id)
    by_rule = {rule["rule_id"]: dict.fromkeys(STATUSES, 0) for rule in RULES}
    conclusions = dict.fromkeys(STATUSES, 0)
    review_traces: dict[str, list[str]] = {rule["rule_id"]: [] for rule in RULES}
    index = events_by_trace(snapshot)
    for trace in snapshot.traces:
        review = evaluate_trace(snapshot, trace, index=index)
        conclusions[review["conclusion"]] += 1
        for item in review["results"]:
            by_rule[item["rule_id"]][item["status"]] += 1
            if item["status"] == "needs_review" and len(review_traces[item["rule_id"]]) < 50:
                review_traces[item["rule_id"]].append(trace.source_trace_id)
    return {"rule_pack": rule_pack_identity(), "dataset_scope_id": dataset_scope_id, "snapshot_id": snapshot_id,
            "trace_count": len(snapshot.traces), "conclusion_counts": conclusions, "rule_status_counts": by_rule,
            "needs_review_trace_samples": review_traces,
            "result_kind": "observation_counts_not_accuracy_or_anomaly_rate",
            "unsupported_checks": list(UNSUPPORTED_CHECKS)}


_STATUS_TEXT = {"compatible": "源策略相容", "needs_review": "存在待核实过程差异",
                "insufficient": "证据不足", "out_of_scope": "范围外／不适用"}


def trace_workpaper(snapshot: ProcessSnapshot, *, dataset_scope_id: str, snapshot_id: str,
                    source_trace_id: str) -> dict:
    """Review plus every cited evidence locator and raw source field value."""
    review = review_trace(snapshot, dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id,
                          source_trace_id=source_trace_id)
    items = {item.evidence_id: item for item in (*snapshot.traces, *snapshot.events)}
    cited = list(dict.fromkeys(ref for result in review["results"] for ref in result["evidence_ids"]))
    evidence = []
    for ref in cited:
        item = items.get(ref)
        if item is None:  # Never export an unresolvable citation.
            raise ValueError("Work paper citation is not present in this process snapshot")
        raw = getattr(item, "event_attributes", None)
        evidence.append({"evidence_id": ref, "kind": "event" if raw is not None else "trace",
                         "locator": item.locator.model_dump(mode="json"),
                         "source_fields": dict(raw if raw is not None else item.trace_attributes)})
    return {"schema_version": "bpi2019-process-workpaper-v1", "review": review, "evidence": evidence,
            "dataset": {"dataset_scope_id": snapshot.dataset.dataset_scope_id,
                        "snapshot_id": snapshot.dataset.snapshot_id,
                        "source_raw_sha256": snapshot.dataset.source_raw_sha256,
                        "source_subset_sha256": snapshot.dataset.source_subset_sha256,
                        "source_license": snapshot.dataset.source_license, "source_doi": snapshot.dataset.source_doi},
            "limitations": list(snapshot.dataset.limitations),
            "notice": "流程观察依据数据集公开流程说明，不是会计准则判断、异常真值或业务完成结论；未生成任何分录。"}


def _flat(value: str) -> str:
    # Source text stays data: no line breaks or code spans can restructure the paper.
    return " ".join(str(value).split()).replace("`", "'")


def workpaper_markdown(paper: dict) -> str:
    review, dataset = paper["review"], paper["dataset"]
    lines = [f"# 采购流程观察底稿 · {review['trace_alias']}", "",
             f"- 结论：**{_STATUS_TEXT[review['conclusion']]}**（{review['conclusion']}）",
             f"- 来源流程分类：`{review['classification']}`",
             f"- 数据集快照：`{dataset['snapshot_id']}`",
             f"- 原 XES SHA256：`{dataset['source_raw_sha256']}`；子集 SHA256：`{dataset['source_subset_sha256']}`",
             f"- 规则包：`{review['rule_pack']['version']}` / `{review['rule_pack']['source_sha256']}`",
             f"- 来源许可：{dataset['source_license']}（DOI {dataset['source_doi']}）", "",
             "> " + paper["notice"], "", "## 规则观察", "",
             "|规则|状态|说明|引用|", "|---|---|---|---|"]
    for result in review["results"]:
        refs = str(len(result["evidence_ids"])) + (" (已截断)" if result["cited_events_truncated"] else "")
        lines.append(f"|`{result['rule_id']}`|{_STATUS_TEXT[result['status']]}|{result['reason']}|{refs}|")
    lines += ["", "## 未实现的检查", "", *[f"- `{name}`" for name in review["unsupported_checks"]],
              "", "## 证据定位", ""]
    for item in paper["evidence"]:
        locator = item["locator"]
        position = f"JSONL 第 {locator['jsonl_line']} 行"
        if locator.get("source_event_ordinal"):
            position += f"，事件序号 {locator['source_event_ordinal']}"
        fields = "；".join(f"{_flat(key)}={_flat(value)}" for key, value in item["source_fields"].items())
        lines.append(f"- `{item['evidence_id']}`（{position}）：{fields}")
    lines += ["", "## 限制", "", *[f"- {text}" for text in paper["limitations"]], ""]
    return "\n".join(lines)
