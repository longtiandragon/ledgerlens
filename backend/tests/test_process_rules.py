"""Independent hand-authored fixtures for the BPI2019 process rule package.

Every fixture is synthetic and marked fixture-only; none is claimed to be a real
BPI trace. Expected statuses are written by hand from the TU/e description, not
produced by the rule implementation.
"""
from copy import deepcopy

import pytest

from backend.app.process_rules import (
    RULES, evaluate_trace, review_trace, rule_catalog, rule_pack_identity, summarize_snapshot,
)
from backend.app.process_events import NUMERIC_FIELD
from test_process_events import adapt, event, scope, trace

T0, T1, T2, T3 = ("2018-01-01T00:00:00.000Z", "2018-01-02T00:00:00.000Z",
                  "2018-01-03T00:00:00.000Z", "2018-01-04T00:00:00.000Z")
AFTER = dict(category="3-way match, invoice after GR", goods="true", gr_based="true")
TWO_WAY = dict(category="2-way match", goods="false", gr_based="false")
CONSIGNMENT = dict(category="Consignment", goods="true", gr_based="false", item_type="Consignment")


def review(*events, **policy):
    snapshot = adapt([trace(list(events), **policy)])
    return evaluate_trace(snapshot, snapshot.traces[0])


def status(result, rule):
    return next(item for item in result["results"] if item["rule_id"] == "BPI2019_PROCESS." + rule)


def test_consignment_without_item_invoice_is_not_a_missing_invoice():
    result = review(event("Create Purchase Order Item", "0.0", T0), event("Record Goods Receipt", "0.0", T1),
                    **CONSIGNMENT)
    assert status(result, "ITEM_INVOICE_EXPECTATION")["status"] == "compatible"
    assert result["conclusion"] == "compatible"
    assert status(result, "EVENT_FIELD_QUALITY")["observed"]["zero_value_event_count"] == 2


def test_consignment_with_item_level_invoice_receipt_needs_review():
    result = review(event("Create Purchase Order Item", "0.0", T0), event("Record Goods Receipt", "0.0", T1),
                    event("Record Invoice Receipt", "0.0", T2), **CONSIGNMENT)
    item = status(result, "ITEM_INVOICE_EXPECTATION")
    assert item["status"] == "needs_review" and result["conclusion"] == "needs_review"
    assert len(item["evidence_ids"]) == 2  # trace + the invoice receipt event


def test_two_way_fixture_requires_no_goods_receipt():
    result = review(event("Create Purchase Order Item", "50.0", T0), event("Record Invoice Receipt", "50.0", T1),
                    event("Clear Invoice", "50.0", T2), **TWO_WAY)
    assert status(result, "GOODS_RECEIPT_EXPECTATION")["status"] == "compatible"
    assert status(result, "GR_BASED_INVOICE_ORDER")["status"] == "out_of_scope"
    assert status(result, "CLEARING_AFTER_GOODS_RECEIPT")["status"] == "out_of_scope"
    assert result["conclusion"] == "compatible"


def test_two_way_with_recorded_goods_receipt_needs_review():
    result = review(event("Create Purchase Order Item", "50.0", T0), event("Record Goods Receipt", "50.0", T1),
                    **TWO_WAY)
    assert status(result, "GOODS_RECEIPT_EXPECTATION")["status"] == "needs_review"


@pytest.mark.parametrize("first,second", [("Record Invoice Receipt", "Record Goods Receipt"),
                                          ("Record Goods Receipt", "Record Invoice Receipt")])
def test_invoice_before_gr_allowed_does_not_require_either_order(first, second):
    result = review(event("Create Purchase Order Item", "10.0", T0), event(first, "10.0", T1),
                    event(second, "10.0", T2), event("Clear Invoice", "10.0", T3))
    assert status(result, "GR_BASED_INVOICE_ORDER")["status"] == "compatible"
    assert status(result, "CLEARING_AFTER_GOODS_RECEIPT")["status"] == "compatible"


def test_gr_based_invoice_recorded_before_goods_needs_review_not_error():
    result = review(event("Create Purchase Order Item", "10.0", T0), event("Record Invoice Receipt", "10.0", T1),
                    event("Record Goods Receipt", "10.0", T2), **AFTER)
    item = status(result, "GR_BASED_INVOICE_ORDER")
    assert item["status"] == "needs_review" and "不是错账结论" in item["reason"]
    assert item["observed"]["first_invoice_relative_to_first_goods_receipt"] == "before"


def test_gr_based_goods_first_is_compatible_and_same_time_is_insufficient():
    ordered = review(event("Record Goods Receipt", "10.0", T1), event("Record Invoice Receipt", "10.0", T2), **AFTER)
    tied = review(event("Record Goods Receipt", "10.0", T1), event("Record Invoice Receipt", "10.0", T1), **AFTER)
    assert status(ordered, "GR_BASED_INVOICE_ORDER")["status"] == "compatible"
    assert status(tied, "GR_BASED_INVOICE_ORDER")["status"] == "insufficient"


def test_same_timestamp_keeps_source_order_and_does_not_decide_causality():
    snapshot = adapt([trace([event("Record Goods Receipt", "1.0", T1), event("Clear Invoice", "1.0", T1)])])
    result = evaluate_trace(snapshot, snapshot.traces[0])
    assert status(result, "CLEARING_AFTER_GOODS_RECEIPT")["status"] == "insufficient"
    assert [e.source_event_ordinal for e in snapshot.events] == [1, 2]
    assert all(e.causal_order_status == "same_timestamp_undetermined" for e in snapshot.events)


@pytest.mark.parametrize("events,expected", [
    ([event("Record Goods Receipt", "1.0", T2), event("Clear Invoice", "1.0", T1)], "needs_review"),
    ([event("Record Invoice Receipt", "1.0", T1), event("Clear Invoice", "1.0", T2)], "needs_review"),
    ([event("Record Goods Receipt", "1.0", T1)], "insufficient"),
])
def test_clearing_before_or_without_goods_receipt_and_absent_clearing(events, expected):
    assert status(review(*events), "CLEARING_AFTER_GOODS_RECEIPT")["status"] == expected


def test_absent_clearing_is_not_a_missing_payment_and_clearing_is_not_bank_payment():
    item = status(review(event("Record Goods Receipt", "1.0", T1)), "CLEARING_AFTER_GOODS_RECEIPT")
    assert "不判定漏付款" in item["reason"] and "不证明银行付款" in item["reason"]


def test_partial_batches_with_equal_values_are_not_duplicate_postings():
    result = review(event("Create Purchase Order Item", "120.0", T0),
                    *[event(name, "10.0", T1) for name in ("Record Goods Receipt", "Record Goods Receipt")],
                    *[event(name, "10.0", T2) for name in ("Record Invoice Receipt", "Record Invoice Receipt")],
                    event("Clear Invoice", "10.0", T3), event("Clear Invoice", "10.0", T3))
    item = status(result, "REPEATED_STAGE_EVENTS")
    assert item["status"] == "compatible" and "不判定重复记账" in item["reason"]
    assert item["observed"]["repeated_stage_counts"] == {"goods_receipt": 2, "invoice_receipt": 2, "clearing": 2}
    assert result["conclusion"] == "compatible"


def test_vendor_invoice_is_not_a_system_invoice_receipt():
    result = review(event("Vendor creates invoice", "5.0", T0), event("Record Goods Receipt", "5.0", T1))
    item = status(result, "ITEM_INVOICE_EXPECTATION")
    assert item["status"] == "insufficient"
    assert item["observed"]["vendor_invoice_events"] == 1 and item["observed"]["invoice_receipt_events"] == 0


def test_service_entry_is_not_counted_as_goods_receipt():
    result = review(event("Record Service Entry Sheet", "5.0", T1), item_type="Service")
    item = status(result, "GOODS_RECEIPT_EXPECTATION")
    assert item["status"] == "insufficient" and item["observed"]["goods_receipt_events"] == 0
    assert "服务验收不等同于实物收货" in item["reason"]


def test_cancellation_positive_values_are_not_new_purchases():
    result = review(event("Record Goods Receipt", "30.0", T1), event("Cancel Goods Receipt", "30.0", T2))
    item = status(result, "CANCELLATION_EVENTS")
    assert item["status"] == "insufficient"
    assert item["observed"]["cancellation_values_treated_as_new_purchase"] is False


def test_zero_is_kept_separate_from_missing_and_invalid_values():
    missing = event("Record Goods Receipt", "1.0", T1)
    del missing[NUMERIC_FIELD]
    result = review(event("Create Purchase Order Item", "0", T0), missing, event("Record Invoice Receipt", "abc", T2))
    item = status(result, "EVENT_FIELD_QUALITY")
    assert item["status"] == "insufficient"
    assert item["observed"] == {"unreadable_event_count": 2, "zero_value_event_count": 1,
                                     "missing_numeric_event_count": 1, "invalid_numeric_event_count": 1}


def test_old_timestamp_is_retained_and_unknown_activity_is_kept():
    snapshot = adapt([trace([event("Vendor creates invoice", "1.0", "2001-02-23T00:00:00.000Z"),
                             event("Fixture-only unknown activity", "1.0", T1)])])
    result = evaluate_trace(snapshot, snapshot.traces[0])
    item = status(result, "ACTIVITY_VOCABULARY")
    assert item["status"] == "needs_review"
    assert item["observed"]["unsupported_activities"] == ["Fixture-only unknown activity"]
    assert snapshot.events[0].occurred_at_raw == "2001-02-23T00:00:00.000Z"


def test_unknown_or_conflicting_policy_flags_are_never_classified():
    conflicting = review(event("Record Goods Receipt", "1.0", T1), category="3-way match, invoice after GR",
                         goods="true", gr_based="TRUE")
    unsupported = review(event("Record Goods Receipt", "1.0", T1), category="new source policy")
    assert status(conflicting, "POLICY_CLASSIFICATION")["status"] == "needs_review"
    assert conflicting["conclusion"] == "needs_review"
    assert status(conflicting, "GOODS_RECEIPT_EXPECTATION")["status"] == "out_of_scope"
    assert unsupported["conclusion"] == "out_of_scope"


def test_cross_scope_requests_and_foreign_traces_are_rejected():
    snapshot = adapt([trace([event("Record Goods Receipt", "1.0", T1)])])
    other = adapt([trace([event("Record Goods Receipt", "1.0", T1)], identity="fixture-other")])
    with pytest.raises(ValueError):
        review_trace(snapshot, dataset_scope_id=scope(snapshot)["dataset_scope_id"],
                     snapshot_id=scope(other)["snapshot_id"], source_trace_id="fixture-only-item")
    with pytest.raises(ValueError):
        evaluate_trace(snapshot, other.traces[0])
    with pytest.raises(KeyError):
        review_trace(snapshot, **scope(snapshot), source_trace_id="fixture-other")


def test_every_citation_resolves_inside_the_snapshot_and_inputs_are_unchanged():
    snapshot = adapt([trace([event("Record Invoice Receipt", "1.0", T1), event("Record Goods Receipt", "1.0", T2),
                             event("Clear Invoice", "1.0", T3)], **AFTER)])
    before = deepcopy(snapshot.model_dump())
    result = review_trace(snapshot, **scope(snapshot), source_trace_id="fixture-only-item")
    known = {snapshot.traces[0].evidence_id, *(e.evidence_id for e in snapshot.events)}
    assert all(set(item["evidence_ids"]) <= known for item in result["results"])
    assert snapshot.model_dump() == before
    assert result["journal_entries_created"] == 0 and result["accounting_rules_applied"] is False


def test_citations_are_bounded_and_truncation_is_reported():
    many = [event("Record Goods Receipt", "1.0", T1) for _ in range(30)]
    item = status(review(*many), "REPEATED_STAGE_EVENTS")
    assert len(item["evidence_ids"]) == 21 and item["cited_event_total"] == 30
    assert item["cited_events_truncated"] is True


def test_summary_counts_are_observations_and_catalog_declares_sources():
    snapshot = adapt([trace([event("Record Goods Receipt", "1.0", T1)]),
                      trace([event("Record Invoice Receipt", "1.0", T1)], identity="fixture-two", **AFTER)])
    summary = summarize_snapshot(snapshot, **scope(snapshot))
    assert summary["trace_count"] == 2 and sum(summary["conclusion_counts"].values()) == 2
    assert summary["result_kind"] == "observation_counts_not_accuracy_or_anomaly_rate"
    catalog = rule_catalog()
    assert len(catalog) == len(RULES)
    assert all(rule["rule_id"].startswith("BPI2019_PROCESS.") and rule["accounting_rule"] is False
               and rule["source"]["url"].startswith("https://research.tue.nl/") and rule["not_applicable"]
               for rule in catalog)
    assert rule_pack_identity()["version"] == "bpi2019-process-rules-v1"


def test_markdown_workpaper_marks_truncation_and_keeps_source_text_inert():
    from backend.app.process_rules import trace_workpaper, workpaper_markdown
    hostile = event("Record Goods Receipt", "1.0", T1, User="line\n# injected `code`")
    snapshot = adapt([trace([hostile] + [event("Record Goods Receipt", "1.0", T1) for _ in range(25)])])
    paper = trace_workpaper(snapshot, **scope(snapshot), source_trace_id="fixture-only-item")
    text = workpaper_markdown(paper)
    assert "21 (已截断)" in text
    assert "\n# injected" not in text and "`code`" not in text and "line # injected 'code'" in text
