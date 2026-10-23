"""Treatment drafts: determinable corrections become lines, the rest name what is missing."""
from copy import deepcopy

import pytest

from app.domain import finding_key, month_end_findings, simulate
from app.treatment import build_draft, compare_lines, investigation_context, propose
from datasets.generate import demo_bundle

POLICY = {"framework": "CAS", "period": "2026-09", "currency": "CNY", "locked_periods": [],
          "accounts": ["1002", "1405", "2202", "6602", "6603"], "recognition_accounts": ["1405", "6602"]}


def row(identifier, kind, **fields):
    return {"id": identifier, "evidence_id": f"e-{identifier}", "source_id": identifier, "kind": kind,
            "business_date": "2026-09-20", "period": "2026-09", "currency": "CNY", "counterparty": "供应商甲",
            "observed_at": "2026-09-30T12:00:00+00:00", **fields}


def voucher(name, amount, debit_account="1405", credit_account="2202", **fields):
    return [row(f"{name}-1", "journal", voucher_id=name, account=debit_account, debit=amount, credit="0.00", **fields),
            row(f"{name}-2", "journal", voucher_id=name, account=credit_account, debit="0.00", credit=amount, **fields)]


def finding(records, rule, policy=POLICY):
    return next(f for f in month_end_findings(records, policy) if f["rule_id"] == rule)


def lines_for_simulation(draft):
    return [{k: v for k, v in line.items()} for line in draft["lines"]]


def test_duplicate_draft_reverses_the_later_full_voucher_and_resolves_only_that_finding():
    demo = demo_bundle()
    records, policy = deepcopy(demo["records"]), {**demo["policy"], "period": "2026-09"}
    duplicate = finding(records, "DUPLICATE_INVOICE", policy)
    draft = propose(duplicate, records, policy)
    assert draft["status"] == "proposed" and draft["summary"] == "冲回重复确认凭证 JV-002"
    assert [a["voucher_id"] for a in draft["alternatives"]] == ["JV-001"]
    assert {(line["account"], line["debit"], line["credit"]) for line in draft["lines"]} == {
        ("1405", "0.00", "100000.00"), ("2202", "100000.00", "0.00")}
    assert all(line["business_date"] == "2026-09-30" and line["period"] == "2026-09" for line in draft["lines"])
    result = simulate(records, lines_for_simulation(draft), policy, target=duplicate)
    assert result["target"]["status"] == "resolved" and result["balanced"] and result["violations"] == []
    assert [f["rule_id"] for f in result["comparison"]["resolved"]] == ["DUPLICATE_INVOICE"]
    assert result["comparison"]["introduced"] == []
    assert records == demo["records"]


def test_overbooked_split_reduces_the_latest_voucher_by_the_excess_only():
    records = [row("INV", "invoice", invoice_id="INV-9", amount="100.00"),
               *voucher("JV-A", "60.00", invoice_id="INV-9"),
               *voucher("JV-B", "50.00", invoice_id="INV-9", business_date="2026-09-25")]
    overbooked = finding(records, "INVOICE_OVERBOOKED")
    draft = propose(overbooked, records, POLICY)
    assert draft["status"] == "proposed" and "JV-B" in draft["summary"] and "10.00" in draft["summary"]
    assert {(line["account"], line["debit"], line["credit"]) for line in draft["lines"]} == {
        ("2202", "10.00", "0.00"), ("1405", "0.00", "10.00")}
    result = simulate(records, lines_for_simulation(draft), POLICY, target=overbooked)
    assert result["target"]["status"] == "resolved" and result["comparison"]["introduced"] == []


def test_unbalanced_voucher_draft_fixes_amount_and_side_but_requires_the_account():
    records = [row("JV-1", "journal", voucher_id="JV-1", account="1405", debit="100.00", credit="0.00"),
               row("JV-2", "journal", voucher_id="JV-1", account="2202", debit="0.00", credit="60.00")]
    unbalanced = finding(records, "VOUCHER_BALANCE")
    draft = propose(unbalanced, records, POLICY)
    assert draft["status"] == "needs_input"
    assert draft["lines"] == [{**draft["lines"][0], "voucher_id": "JV-1", "account": "", "debit": "0.00", "credit": "40.00"}]
    assert draft["line_basis"][0]["input_required"] == ["account"]
    with pytest.raises(ValueError):
        simulate(records, lines_for_simulation(draft), POLICY)
    filled = [{**draft["lines"][0], "account": "2202"}]
    result = simulate(records, filled, POLICY, target=unbalanced)
    # The completed voucher balances together with its source lines.
    assert result["balanced"] and result["target"]["status"] == "resolved"


@pytest.mark.parametrize("business_date,framework,status,needle", [
    ("2026-10-02", "CAS", "conditional", "提前入账"),
    ("2026-08-30", "CAS", "not_applicable", "同一会计年度"),
    ("2025-12-30", "CAS", "needs_evidence", "POLICY_CAS_PRIOR_PERIOD_ERROR_MATERIALITY"),
    ("2025-12-30", "SCAS", "not_applicable", "未来适用法"),
])
def test_period_cutoff_path_depends_on_direction_year_and_framework(business_date, framework, status, needle):
    records = voucher("JV-P", "80.00", business_date=business_date)
    policy = {**POLICY, "framework": framework}
    cutoff = finding(records, "PERIOD_CUTOFF", policy)
    draft = propose(cutoff, records, policy)
    assert draft["status"] == status
    assert needle in draft["reason"] + draft["framework_note"]
    assert bool(draft["lines"]) is (status == "conditional")
    if status == "conditional":
        result = simulate(records, lines_for_simulation(draft), policy)
        assert result["balanced"] and result["delta"]["1405"] == "-80.00"


def test_unmatched_bank_fee_draft_needs_counter_account_and_then_reconciles():
    records = [row("B-PAY", "bank", amount="-1000.00", order_id="PO-1", account="1002"),
               row("B-FEE", "bank", amount="-15.00", order_id="PO-1", account="1002", reference="手续费"),
               row("J-PAY-1", "journal", voucher_id="J-PAY", account="2202", debit="1000.00", credit="0.00", order_id="PO-1"),
               row("J-PAY-2", "journal", voucher_id="J-PAY", account="1002", debit="0.00", credit="1000.00", order_id="PO-1")]
    difference = finding(records, "BANK_LEDGER_DIFFERENCE")
    assert difference["metrics"]["difference"] == "-15.00"
    draft = propose(difference, records, POLICY)
    assert draft["status"] == "needs_input" and draft["evidence_kind"] is None
    assert [(line["account"], line["debit"], line["credit"]) for line in draft["lines"]] == [
        ("1002", "0.00", "15.00"), ("", "15.00", "0.00")]
    assert [b["input_required"] for b in draft["line_basis"]] == [[], ["account"]]
    filled = [draft["lines"][0], {**draft["lines"][1], "account": "6603"}]
    result = simulate(records, filled, POLICY, target=difference)
    assert result["target"]["status"] == "resolved"
    assert {f["rule_id"] for f in result["comparison"]["resolved"]} == {"BANK_LEDGER_DIFFERENCE", "BANK_UNMATCHED"}
    assert [f["rule_id"] for f in result["comparison"]["normal"]] == ["BANK_CANDIDATES_ALIGNED"]
    assert result["comparison"]["introduced"] == []


def test_ledger_side_difference_without_unmatched_statement_needs_evidence_not_a_plug():
    records = [row("B-PAY", "bank", amount="-1000.00", order_id="PO-1", account="1002"),
               *voucher("J-PAY", "1000.00", debit_account="2202", credit_account="1002", order_id="PO-1"),
               *voucher("J-EXTRA", "20.00", debit_account="6602", credit_account="1002", order_id="PO-2")]
    difference = finding(records, "BANK_LEDGER_DIFFERENCE")
    draft = propose(difference, records, POLICY)
    assert draft["status"] == "needs_evidence" and draft["lines"] == []


@pytest.mark.parametrize("rule,kind", [("RECEIPT_GAP", "receipt"), ("INVOICE_CONFLICT", "invoice"),
                                       ("OPENING_REQUIRED", "opening")])
def test_insufficient_findings_never_get_guessed_entries(rule, kind):
    records = {
        "RECEIPT_GAP": [row("PO", "order", order_id="PO-1", amount="100.00"),
                        row("RCV", "receipt", order_id="PO-1", amount="60.00")],
        "INVOICE_CONFLICT": [row("I1", "invoice", invoice_id="INV-1", amount="100.00"),
                             row("I2", "invoice", invoice_id="INV-1", amount="90.00"),
                             *voucher("JV-1", "100.00", invoice_id="INV-1"), *voucher("JV-2", "90.00", invoice_id="INV-1")],
        "OPENING_REQUIRED": [row("TB", "trial_balance", account="1405", amount="100.00")],
    }[rule]
    draft = propose(finding(records, rule), records, POLICY)
    assert draft["status"] == "needs_evidence" and draft["lines"] == [] and draft["evidence_kind"] == kind
    assert draft["needs"]


def test_mapping_gap_and_aligned_funds_need_no_adjustment():
    records = voucher("JV-M", "10.00", debit_account="1499")
    assert propose(finding(records, "ACCOUNT_VALIDITY"), records, POLICY)["status"] == "not_applicable"
    aligned = [row("B", "bank", amount="-10.00", order_id="PO-1", account="1002"),
               *voucher("J", "10.00", debit_account="2202", credit_account="1002", order_id="PO-1")]
    draft = propose(finding(aligned, "BANK_CANDIDATES_ALIGNED"), aligned, POLICY)
    assert draft["status"] == "not_applicable" and draft["lines"] == []


def test_book_without_bank_statements_gets_no_funds_case():
    records = voucher("J", "10.00", debit_account="2202", credit_account="1002")
    assert not any(f["rule_id"].startswith("BANK_") for f in month_end_findings(records, POLICY))
    statements_only = [row("B", "bank", amount="-10.00", order_id="PO-1", account="1002")]
    assert [f["rule_id"] for f in month_end_findings(statements_only, POLICY)] == ["BANK_RECONCILIATION_INPUT"]


def test_draft_digest_is_stable_and_bound_to_case_snapshot_and_lines():
    demo = demo_bundle()
    duplicate = finding(demo["records"], "DUPLICATE_INVOICE", demo["policy"])
    first = build_draft("case-1", "snap-1", duplicate, demo["records"], demo["policy"])
    assert first["digest"] == build_draft("case-1", "snap-1", duplicate, demo["records"], demo["policy"])["digest"]
    assert first["digest"] != build_draft("case-2", "snap-1", duplicate, demo["records"], demo["policy"])["digest"]
    assert first["digest"] != build_draft("case-1", "snap-2", duplicate, demo["records"], demo["policy"])["digest"]
    # Investigation context is advice only: it never changes lines or the digest.
    with_run = build_draft("case-1", "snap-1", duplicate, demo["records"], demo["policy"],
                           {"id": "run", "mode": "rules", "status": "complete", "result": {"status": "normal"}})
    assert with_run["digest"] == first["digest"] and "正常" in with_run["investigation"]["warning"]


def test_compare_lines_separates_completed_inputs_from_edits():
    draft = {"lines": [{"voucher_id": "V", "account": "", "debit": "5.00", "credit": "0.00"}],
             "line_basis": [{"source_record_ids": ["r"], "input_required": ["account"]}]}
    completed = compare_lines(draft, [{"voucher_id": "V", "account": "6603", "debit": "5.00", "credit": "0.00"}])
    assert completed == {"edited": False, "edits": [],
                         "completed_inputs": [{"line": 1, "field": "account", "draft": "", "submitted": "6603"}]}
    edited = compare_lines(draft, [{"voucher_id": "V", "account": "6603", "debit": "6.00", "credit": "0.00"},
                                   {"voucher_id": "V", "account": "1002", "debit": "0.00", "credit": "6.00"}])
    assert edited["edited"] and {"line": 2, "change": "added"} in edited["edits"]
    assert {"line": 1, "field": "debit", "draft": "5.00", "submitted": "6.00"} in edited["edits"]


@pytest.mark.parametrize("outcome,status,warned", [
    ("failed", "proposed", True), ("budget_exhausted", "needs_input", True), ("normal", "proposed", True),
    ("insufficient", "proposed", True), ("insufficient", "needs_evidence", False), ("supported", "proposed", False)])
def test_investigation_context_warns_without_overriding(outcome, status, warned):
    context = investigation_context({"id": "r", "mode": "single", "status": outcome, "result": {"status": outcome}}, status)
    assert (context["warning"] is not None) is warned
    assert investigation_context(None, status) is None


def test_finding_key_ignores_draft_rows():
    found = {"rule_id": "X", "record_ids": ["b", "simulation-1", "a"]}
    assert finding_key(found) == ("X", ("a", "b"))
