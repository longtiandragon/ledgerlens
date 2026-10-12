"""Read-only, dataset-scoped BPI2019 process evidence (Apache-2.0).

This module has no ledger/database/model/network dependencies. Mapping and
observations are source-process descriptions, never accounting rules or labels.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ADAPTER_VERSION = "bpi2019-events-v1"
OBSERVATION_VERSION = "bpi2019-observations-v1"
POLICY_SOURCE = "https://research.tue.nl/en/datasets/bpi-challenge-2019/"
NUMERIC_FIELD = "Cumulative net worth (EUR)"
NUMERIC_SEMANTICS = "anonymised_event_value_with_EUR_field_label_not_bookable_currency_or_running_balance"
UNSUPPORTED_RULES = (
    "accounting_framework_and_ledger_postings", "currency_conversion", "cross_stage_amount_summation",
    "stage_balance_and_rounding_reconciliation", "cancellation_target_and_signed_netting",
    "invoice_to_clearing_identity_matching", "bank_payment_confirmation", "payment_block_state_reconstruction",
    "missing_document_or_duplicate_posting_diagnosis", "anomaly_root_cause_and_business_completion",
    "source_system_observation_time_and_future_information_leakage_validation",
)
LIMITATIONS = (
    "Complete XML trace means a complete recorded trace, not a completed business process.",
    "Repeated activities can represent partial fulfilment; no duplicate-posting rule is applied.",
    "Clear Invoice is a source clearing activity, not independent evidence of bank payment.",
    "Equal timestamps preserve source order but do not establish a unique causal order.",
    "No native event ID, invoice ID, bank transaction ID or voucher ID is supplied by this adapter.",
    "No accounting framework, ledger currency, account, debit or credit is inferred.",
    "No original system knowledge timestamp or independently verified anomaly truth is available.",
)

# The complete vocabulary inspected in the current subset. Unsupported activities
# remain evidence and are explicitly marked, not silently discarded.
ACTIVITIES = {
    "Create Purchase Order Item": ("purchase_item_created", "purchase"),
    "Vendor creates invoice": ("vendor_invoice_created", "vendor_invoice"),
    "Record Goods Receipt": ("goods_receipt_recorded", "goods_receipt"),
    "Record Invoice Receipt": ("invoice_receipt_recorded", "invoice_receipt"),
    "Clear Invoice": ("invoice_clearing_recorded", "clearing"),
    "Record Service Entry Sheet": ("service_entry_recorded", "service_acceptance"),
    "Cancel Goods Receipt": ("goods_receipt_cancelled", "cancellation"),
    "Cancel Invoice Receipt": ("invoice_receipt_cancelled", "cancellation"),
    "Cancel Subsequent Invoice": ("subsequent_invoice_cancelled", "cancellation"),
    "Record Subsequent Invoice": ("subsequent_invoice_recorded", "subsequent_invoice"),
    "Vendor creates debit memo": ("vendor_debit_memo_created", "vendor_debit_memo"),
    "Change Delivery Indicator": ("delivery_indicator_changed", "control"),
    "Remove Payment Block": ("payment_block_removed", "control"),
    "Change Price": ("price_changed", "change"),
    "Delete Purchase Order Item": ("purchase_item_deleted", "control"),
    "Change Quantity": ("quantity_changed", "change"),
    "Change Final Invoice Indicator": ("final_invoice_indicator_changed", "control"),
    "Receive Order Confirmation": ("order_confirmation_received", "confirmation"),
    "Reactivate Purchase Order Item": ("purchase_item_reactivated", "control"),
    "Update Order Confirmation": ("order_confirmation_updated", "confirmation"),
    "Block Purchase Order Item": ("purchase_item_blocked", "control"),
    "Change Approval for Purchase Order": ("purchase_order_approval_changed", "control"),
    "Release Purchase Order": ("purchase_order_released", "control"),
    "Set Payment Block": ("payment_block_set", "control"),
    **{name: ("srm_" + slug, "srm") for name, slug in (
        ("SRM: Created", "created"), ("SRM: Complete", "complete"),
        ("SRM: Awaiting Approval", "awaiting_approval"),
        ("SRM: Document Completed", "document_completed"),
        ("SRM: In Transfer to Execution Syst.", "in_transfer"), ("SRM: Ordered", "ordered"),
        ("SRM: Change was Transmitted", "change_transmitted"),
        ("SRM: Transfer Failed (E.Sys.)", "transfer_failed"), ("SRM: Deleted", "deleted"),
        ("SRM: Transaction Completed", "transaction_completed"),
        ("SRM: Incomplete", "incomplete"), ("SRM: Held", "held"),
    )},
}


class ProcessModel(BaseModel):
    # Unlike ledger input, raw strings MUST NOT be whitespace-stripped.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessDataset(ProcessModel):
    dataset_scope_id: str
    snapshot_id: str
    source_document_id: str
    source_raw_sha256: str
    source_subset_sha256: str
    adapter_version: str = ADAPTER_VERSION
    observation_version: str = OBSERVATION_VERSION
    title: str = "BPI Challenge 2019 process events"
    source_doi: str = "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
    source_license: str = "CC BY 4.0"
    observed_at: datetime
    trace_count: int
    event_count: int
    selection: str = "caller_supplied_complete_traces"
    source_verification: str = "not_verified_by_pure_converter"
    trace_completeness_semantics: str = "all_records_in_selected_xml_traces_not_business_completion"
    unsupported_rules: tuple[str, ...] = UNSUPPORTED_RULES
    limitations: tuple[str, ...] = LIMITATIONS


class SourceLocator(ProcessModel):
    source_document_id: str
    source_subset_sha256: str
    jsonl_line: int = Field(ge=1)
    source_trace_ordinal: int = Field(ge=1)
    source_trace_id: str
    source_event_ordinal: int | None = Field(default=None, ge=1)
    # JSON pointers point to real keys in the original JSONL record.
    field_pointers: dict[str, str]


class SourceRelation(ProcessModel):
    subject_id: str
    predicate: Literal["event_belongs_to_purchase_item", "purchase_item_belongs_to_purchase_document"]
    object_id: str
    basis: Literal["source_declared"] = "source_declared"
    source_fields: tuple[str, ...]


class ProcessPolicy(ProcessModel):
    classification: str
    status: Literal["classified", "needs_review", "unsupported"]
    goods_receipt_flag: bool | None
    gr_based_invoice_verification_flag: bool | None
    item_category_raw: str | None
    item_type_raw: str | None
    issues: tuple[str, ...]
    source: str = POLICY_SOURCE
    observation_version: str = OBSERVATION_VERSION


class BusinessEvent(ProcessModel):
    dataset_scope_id: str
    snapshot_id: str
    source_document_id: str
    source_trace_id: str
    source_trace_ordinal: int
    source_event_ordinal: int
    source_event_id: str
    event_id_kind: Literal["adapter_generated_not_source_native"] = "adapter_generated_not_source_native"
    trace_alias: str
    activity_raw: str | None
    event_type: str
    phase: str
    mapping_status: Literal["mapped", "unsupported"]
    occurred_at_raw: str | None
    occurred_at: datetime | None
    observed_at: datetime
    source_numeric_text: str | None
    source_numeric_status: Literal["valid", "missing", "invalid"]
    source_numeric_semantics: str = NUMERIC_SEMANTICS
    event_attributes: dict[str, str]
    evidence_id: str
    locator: SourceLocator
    relations: tuple[SourceRelation, ...]
    quality_issues: tuple[str, ...]
    same_timestamp_event_ordinals: tuple[int, ...]
    causal_order_status: Literal["same_timestamp_undetermined", "not_established"]


class ProcessTrace(ProcessModel):
    dataset_scope_id: str
    snapshot_id: str
    source_trace_id: str
    source_trace_ordinal: int
    trace_alias: str
    purchase_item_id: str
    trace_attributes: dict[str, str]
    complete_recorded_trace: Literal[True] = True
    business_completion: Literal["not_determined"] = "not_determined"
    policy: ProcessPolicy
    evidence_id: str
    locator: SourceLocator
    relations: tuple[SourceRelation, ...]
    event_ids: tuple[str, ...]
    observations: dict


class ProcessSnapshot(ProcessModel):
    dataset: ProcessDataset
    traces: tuple[ProcessTrace, ...]
    events: tuple[BusinessEvent, ...]


def _identity(prefix: str, *parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_identity(raw_sha256: str, subset_sha256: str) -> tuple[str, str]:
    for value in (raw_sha256, subset_sha256):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Source hashes must be lowercase SHA256 values")
    scope = _identity("process_dataset", "BPI2019", raw_sha256)
    return scope, _identity("process_snapshot", scope, subset_sha256, ADAPTER_VERSION, OBSERVATION_VERSION)


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone-aware observation time is required")
    return value.astimezone(UTC)


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
        return _aware(value)
    except (ValueError, TypeError):
        return None


def _numeric_status(raw: str | None) -> str:
    if raw is None:
        return "missing"
    # Decimal accepts NaN, Infinity, underscores and whitespace; those are not
    # accepted as a valid event numeric literal here, but the text is retained.
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw):
        return "invalid"
    try:
        return "valid" if Decimal(raw).is_finite() else "invalid"
    except InvalidOperation:
        return "invalid"


def _raw_attributes(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           for k, v in value.items()):
        raise ValueError("Source attribute names and values must be literal strings")
    return dict(value)


def _pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def classify_process(attributes: dict[str, str]) -> ProcessPolicy:
    """Classify only the explicitly documented source policy combinations."""
    goods = {"true": True, "false": False}.get(attributes.get("Goods Receipt"))
    gr_based = {"true": True, "false": False}.get(attributes.get("GR-Based Inv. Verif."))
    category, item_type = attributes.get("Item Category"), attributes.get("Item Type")
    supported = {
        "3-way match, invoice before GR": (False, True, "three_way_invoice_before_gr_allowed"),
        "3-way match, invoice after GR": (True, True, "three_way_gr_based_invoice_verification"),
        "2-way match": (False, False, "two_way_no_goods_receipt_required"),
        "Consignment": (False, True, "consignment_invoice_outside_item_process"),
    }
    issues = []
    if goods is None:
        issues.append("goods_receipt_flag_missing_or_not_exact_boolean_literal")
    if gr_based is None:
        issues.append("gr_based_invoice_flag_missing_or_not_exact_boolean_literal")
    expected = supported.get(category)
    if expected is None:
        issues.append("unsupported_item_category")
    elif (gr_based, goods) != expected[:2]:
        issues.append("source_policy_flags_do_not_match_category")
    if item_type not in {"Standard", "Service", "Consignment"}:
        issues.append("unsupported_or_missing_item_type")
    elif (category == "Consignment") != (item_type == "Consignment"):
        issues.append("item_type_and_category_disagree_on_consignment")
    return ProcessPolicy(classification=expected[2] if expected and not issues else "undetermined",
                         status="unsupported" if expected is None else "needs_review" if issues else "classified",
                         goods_receipt_flag=goods, gr_based_invoice_verification_flag=gr_based,
                         item_category_raw=category, item_type_raw=item_type, issues=tuple(issues))


def _trace_observations(events: list[BusinessEvent]) -> dict:
    """Recorded counts and timelines; deliberately no balances or diagnoses."""
    times = [event.occurred_at for event in events if event.occurred_at is not None]
    phases = sorted({event.phase for event in events})
    groups: dict[datetime, list[int]] = {}
    for event in events:
        if event.occurred_at is not None:
            groups.setdefault(event.occurred_at, []).append(event.source_event_ordinal)
    first_goods = next((event for event in events if event.event_type == "goods_receipt_recorded"), None)
    first_invoice = next((event for event in events if event.event_type == "invoice_receipt_recorded"), None)
    relation = "insufficient_events_or_timestamps"
    if first_goods and first_invoice and first_goods.occurred_at and first_invoice.occurred_at:
        relation = ("same_timestamp_undetermined" if first_goods.occurred_at == first_invoice.occurred_at
                    else "first_recorded_invoice_before_first_recorded_goods"
                    if first_invoice.occurred_at < first_goods.occurred_at
                    else "first_recorded_goods_before_first_recorded_invoice")
    return {
        "observation_version": OBSERVATION_VERSION,
        "result_kind": "recorded_process_observations_not_anomaly_findings",
        "activity_counts": dict(sorted(Counter(e.activity_raw or "<missing>" for e in events).items())),
        "phase_counts": dict(sorted(Counter(e.phase for e in events).items())),
        "event_count": len(events),
        "occurred_at_min": min(times).isoformat() if times else None,
        "occurred_at_max": max(times).isoformat() if times else None,
        "valid_timestamp_count": len(times),
        "source_timestamp_order": ("not_fully_observed" if len(times) != len(events) else
                                   "nondecreasing" if times == sorted(times) else "nonmonotonic"),
        "same_timestamp_groups": [ordinals for _, ordinals in sorted(groups.items()) if len(ordinals) > 1],
        "timeline_order": "source_event_ordinal_not_inferred_causal_order",
        "phase_timelines": {phase: [e.evidence_id for e in events if e.phase == phase] for phase in phases},
        "first_recorded_goods_invoice_time_relation": relation,
        "absent_recorded_stages": [phase for phase in ("goods_receipt", "invoice_receipt", "clearing")
                                   if phase not in phases],
        "absence_interpretation": "not_a_missing_document_or_payment_diagnosis",
        "amount_aggregation": "not_performed_semantics_not_sufficiently_established",
        "business_completion": "not_determined",
    }


def adapt_traces(traces: list[dict], *, raw_sha256: str, subset_sha256: str,
                 observed_at: datetime, selection: str = "caller_supplied_complete_traces",
                 source_verification: str = "not_verified_by_pure_converter") -> ProcessSnapshot:
    """Pure conversion; caller must verify hashes against the actual files.

    Ordinals are one-based and trace ordinal equals the nonempty JSONL line.
    Caller may not remove, reorder or filter lines before invoking this method.
    """
    scope, snapshot_id = snapshot_identity(raw_sha256, subset_sha256)
    observed_at = _aware(observed_at)
    document_id = "sha256:" + raw_sha256
    converted_traces, converted_events, identities = [], [], set()
    if not isinstance(traces, list) or not traces:
        raise ValueError("At least one complete trace is required")
    for trace_ordinal, trace in enumerate(traces, 1):
        if not isinstance(trace, dict) or trace.get("complete_trace") is not True or trace.get("source") != "BPI2019":
            raise ValueError("Only complete BPI2019 source traces are accepted")
        if trace.get("accounting_framework") is not None:
            raise ValueError("A process trace must not have an invented accounting framework")
        attributes = _raw_attributes(trace.get("attributes"))
        trace_id = trace.get("case_id")
        if not isinstance(trace_id, str) or not trace_id or trace_id in identities or (
                attributes.get("concept:name") != trace_id):
            raise ValueError("Source trace identity must be present, unique and agree with concept:name")
        identities.add(trace_id)
        if not isinstance(trace.get("events"), list):
            raise ValueError("Source events must be a list")
        raw_events = [_raw_attributes(event) for event in trace["events"]]
        item_id = _identity("purchase_item", scope, trace_id)
        alias = "item-" + hashlib.sha256((scope + trace_id).encode("utf-8")).hexdigest()[:16]
        base_locator = dict(source_document_id=document_id, source_subset_sha256=subset_sha256,
                            jsonl_line=trace_ordinal, source_trace_ordinal=trace_ordinal, source_trace_id=trace_id)
        trace_relations = ()
        if attributes.get("Purchasing Document"):
            document_entity = _identity("purchase_document", scope, attributes.get("Source"),
                                        attributes.get("Company"), attributes["Purchasing Document"])
            trace_relations = (SourceRelation(subject_id=item_id,
                predicate="purchase_item_belongs_to_purchase_document", object_id=document_entity,
                source_fields=("Purchasing Document",)),)
        times = [_parse_timestamp(event.get("time:timestamp")) for event in raw_events]
        time_groups: dict[datetime, list[int]] = {}
        for ordinal, timestamp in enumerate(times, 1):
            if timestamp is not None:
                time_groups.setdefault(timestamp, []).append(ordinal)
        events = []
        for event_ordinal, raw_event in enumerate(raw_events, 1):
            activity = raw_event.get("concept:name")
            event_type, phase = ACTIVITIES.get(activity, ("unsupported_activity", "unsupported"))
            event_id = _identity("process_event", raw_sha256, trace_id, event_ordinal)
            numeric_text = raw_event.get(NUMERIC_FIELD)
            numeric_status = _numeric_status(numeric_text)
            timestamp = times[event_ordinal - 1]
            issues = []
            if activity not in ACTIVITIES:
                issues.append("activity_missing_or_unsupported")
            if timestamp is None:
                issues.append("timestamp_missing_invalid_or_timezone_absent")
            if numeric_status != "valid":
                issues.append("source_numeric_" + numeric_status)
            same_time = time_groups.get(timestamp, []) if timestamp else []
            events.append(BusinessEvent(dataset_scope_id=scope, snapshot_id=snapshot_id,
                source_document_id=document_id, source_trace_id=trace_id, source_trace_ordinal=trace_ordinal,
                source_event_ordinal=event_ordinal, source_event_id=event_id, trace_alias=alias,
                activity_raw=activity, event_type=event_type, phase=phase,
                mapping_status="mapped" if activity in ACTIVITIES else "unsupported",
                occurred_at_raw=raw_event.get("time:timestamp"), occurred_at=timestamp, observed_at=observed_at,
                source_numeric_text=numeric_text, source_numeric_status=numeric_status, event_attributes=raw_event,
                evidence_id=_identity("process_evidence", snapshot_id, event_id),
                locator=SourceLocator(**base_locator, source_event_ordinal=event_ordinal,
                    field_pointers={key: f"/events/{event_ordinal - 1}/{_pointer(key)}" for key in raw_event}),
                relations=(SourceRelation(subject_id=event_id, predicate="event_belongs_to_purchase_item",
                                          object_id=item_id, source_fields=("trace_membership", "concept:name")),),
                quality_issues=tuple(issues), same_timestamp_event_ordinals=tuple(same_time) if len(same_time) > 1 else (),
                causal_order_status="same_timestamp_undetermined" if len(same_time) > 1 else "not_established"))
        converted_events.extend(events)
        converted_traces.append(ProcessTrace(dataset_scope_id=scope, snapshot_id=snapshot_id,
            source_trace_id=trace_id, source_trace_ordinal=trace_ordinal, trace_alias=alias, purchase_item_id=item_id,
            trace_attributes=attributes, policy=classify_process(attributes),
            evidence_id=_identity("process_trace_evidence", snapshot_id, trace_id),
            locator=SourceLocator(**base_locator, field_pointers={key: "/attributes/" + _pointer(key)
                                                                 for key in attributes}),
            relations=trace_relations, event_ids=tuple(event.source_event_id for event in events),
            observations=_trace_observations(events)))
    dataset = ProcessDataset(dataset_scope_id=scope, snapshot_id=snapshot_id, source_document_id=document_id,
        source_raw_sha256=raw_sha256, source_subset_sha256=subset_sha256, observed_at=observed_at,
        trace_count=len(converted_traces), event_count=len(converted_events), selection=selection,
        source_verification=source_verification)
    return ProcessSnapshot(dataset=dataset, traces=tuple(converted_traces), events=tuple(converted_events))


def quality_report(snapshot: ProcessSnapshot) -> dict:
    events, traces = snapshot.events, snapshot.traces
    times = [event.occurred_at for event in events if event.occurred_at is not None]
    event_issues = Counter(issue for event in events for issue in event.quality_issues)
    policy_issues = Counter(issue for trace in traces for issue in trace.policy.issues)
    return {
        "status": "adapted_with_quality_issues" if event_issues or policy_issues else "adapted",
        "dataset_scope_id": snapshot.dataset.dataset_scope_id, "snapshot_id": snapshot.dataset.snapshot_id,
        "source_raw_sha256": snapshot.dataset.source_raw_sha256,
        "source_subset_sha256": snapshot.dataset.source_subset_sha256,
        "adapter_version": ADAPTER_VERSION, "observation_version": OBSERVATION_VERSION,
        "source_verification": snapshot.dataset.source_verification,
        "trace_count": len(traces), "event_count": len(events),
        "source_trace_ordinals": {"first": 1, "last": len(traces)},
        "selection": snapshot.dataset.selection,
        "activity_counts": dict(sorted(Counter(event.activity_raw or "<missing>" for event in events).items())),
        "phase_counts": dict(sorted(Counter(event.phase for event in events).items())),
        "policy_counts": dict(sorted(Counter(trace.policy.classification for trace in traces).items())),
        "policy_status_counts": dict(sorted(Counter(trace.policy.status for trace in traces).items())),
        "item_type_counts": dict(sorted(Counter(t.trace_attributes.get("Item Type", "<missing>") for t in traces).items())),
        "event_quality_issue_counts": dict(sorted(event_issues.items())),
        "policy_quality_issue_counts": dict(sorted(policy_issues.items())),
        "timestamp_year_counts": dict(sorted(Counter(str(t.year) for t in times).items())),
        "occurred_at_min": min(times).isoformat() if times else None,
        "occurred_at_max": max(times).isoformat() if times else None,
        "numeric_literal_zero_count": sum(e.source_numeric_status == "valid" and Decimal(e.source_numeric_text) == 0
                                           for e in events),
        "traces_with_same_timestamp_events": sum(bool(t.observations["same_timestamp_groups"]) for t in traces),
        "traces_with_nonmonotonic_timestamps": sum(t.observations["source_timestamp_order"] == "nonmonotonic"
                                                  for t in traces),
        "traces_without_recorded_clearing": sum("clearing" in t.observations["absent_recorded_stages"] for t in traces),
        "traces_with_multiple_stage_events": {phase: sum(t.observations["phase_counts"].get(phase, 0) > 1
                                                        for t in traces)
                                               for phase in ("goods_receipt", "invoice_receipt", "clearing")},
        "first_recorded_goods_invoice_time_relations": dict(sorted(Counter(
            t.observations["first_recorded_goods_invoice_time_relation"] for t in traces).items())),
        "missing_business_identifiers": ["native_event_id", "invoice_id", "bank_transaction_id", "voucher_id"],
        "accounting_framework_inferred": False, "currency_inferred": False,
        "journal_entries_created": 0, "amount_aggregation_performed": False,
        "business_completion_determined": False, "anomaly_truth_available": False,
        "unsupported_rules": list(UNSUPPORTED_RULES), "limitations": list(LIMITATIONS),
    }


def _check_scope(snapshot: ProcessSnapshot, dataset_scope_id: str, snapshot_id: str) -> None:
    if dataset_scope_id != snapshot.dataset.dataset_scope_id or snapshot_id != snapshot.dataset.snapshot_id:
        raise ValueError("Process evidence dataset/snapshot scope mismatch; ledger scopes are not accepted")


def query_process_events(snapshot: ProcessSnapshot, *, dataset_scope_id: str, snapshot_id: str,
                         source_trace_id: str | None = None, activity_raw: str | None = None,
                         event_type: str | None = None, occurred_from: datetime | None = None,
                         occurred_to: datetime | None = None, offset: int = 0, limit: int = 100) -> dict:
    """Allowlisted, bounded query; times are inclusive and require timezone."""
    _check_scope(snapshot, dataset_scope_id, snapshot_id)
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise ValueError("Limit must be 1..200 and offset must be a nonnegative integer")
    start = _aware(occurred_from) if occurred_from is not None else None
    end = _aware(occurred_to) if occurred_to is not None else None
    if start and end and start > end:
        raise ValueError("Query time range is reversed")
    selected = [event for event in snapshot.events
                if (source_trace_id is None or event.source_trace_id == source_trace_id)
                and (activity_raw is None or event.activity_raw == activity_raw)
                and (event_type is None or event.event_type == event_type)
                and (start is None or event.occurred_at is not None and event.occurred_at >= start)
                and (end is None or event.occurred_at is not None and event.occurred_at <= end)]
    page = selected[offset:offset + limit]
    return {"dataset_scope_id": dataset_scope_id, "snapshot_id": snapshot_id,
            "total": len(selected), "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < len(selected) else None,
            "items": [event.model_dump(mode="json") for event in page]}


def get_process_trace(snapshot: ProcessSnapshot, *, dataset_scope_id: str, snapshot_id: str,
                      source_trace_id: str, offset: int = 0, limit: int = 100) -> dict:
    _check_scope(snapshot, dataset_scope_id, snapshot_id)
    trace = next((trace for trace in snapshot.traces if trace.source_trace_id == source_trace_id), None)
    if trace is None:
        raise KeyError("Trace is not present in this process snapshot")
    page = query_process_events(snapshot, dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id,
                                source_trace_id=source_trace_id, offset=offset, limit=limit)
    return {"trace": trace.model_dump(mode="json"), "events": page}


def resolve_process_evidence(snapshot: ProcessSnapshot, *, dataset_scope_id: str, snapshot_id: str,
                              evidence_id: str) -> dict:
    """Resolve only evidence actually present in the caller's fixed snapshot."""
    _check_scope(snapshot, dataset_scope_id, snapshot_id)
    for item in (*snapshot.traces, *snapshot.events):
        if item.evidence_id == evidence_id:
            return item.model_dump(mode="json")
    raise KeyError("Evidence is not present in this process snapshot")
