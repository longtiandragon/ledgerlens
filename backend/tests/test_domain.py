from copy import deepcopy
from decimal import Decimal
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.domain import balances, bank_reconciliation, check_records, money, rule_catalog, simulate
from benchmarks.oracle import reference_balances
from datasets.generate import FAMILIES, build_family, demo_bundle


def codes(records, policy):
    return {f["rule_id"] for f in check_records(records, policy)}


def test_demo_duplicate_and_late_receipt_are_different_findings():
    demo = demo_bundle()
    findings = check_records(demo["records"], demo["policy"])
    assert {f["rule_id"] for f in findings} == {"DUPLICATE_INVOICE", "RECEIPT_GAP"}
    duplicate = next(f for f in findings if f["rule_id"] == "DUPLICATE_INVOICE")
    gap = next(f for f in findings if f["rule_id"] == "RECEIPT_GAP")
    assert duplicate["metrics"]["excess"] == "100000.00"
    assert duplicate["status"] == "supported" and gap["status"] == "insufficient"
    assert len(duplicate["evidence_ids"]) == len(duplicate["record_ids"])


def test_supplement_changes_only_new_snapshot():
    demo = demo_bundle()
    frozen = deepcopy(demo["records"])
    assert "RECEIPT_GAP" in codes(frozen, demo["policy"])
    assert "RECEIPT_GAP" not in codes([*frozen, *demo["supplemental_records"]], demo["policy"])
    assert "RECEIPT_GAP" in codes(frozen, demo["policy"])
    assert frozen == demo["records"]


@pytest.mark.parametrize("index", range(10))
def test_normal_topologies_and_reversal_not_duplicate(index):
    bundle, truth = build_family(index, 0)
    assert truth["expected_rules"] == []
    assert check_records(bundle["records"], bundle["policy"]) == []


def test_independent_fraction_oracle_and_input_order():
    demo = demo_bundle()
    original = deepcopy(demo["records"])
    expected = {"1002": "-100000.00", "1405": "200000.00", "2202": "-100000.00"}
    assert reference_balances(original) == expected
    assert balances(original) == expected
    assert balances(list(reversed(original))) == expected
    assert check_records(original, demo["policy"]) == check_records(list(reversed(original)), demo["policy"])
    assert demo["records"] == original


def test_decimal_rejects_lossy_missing_or_nonfinite_money():
    for value in [None, 0.1, True, "nan", "Infinity", "0.001", "", "1e100"]:
        with pytest.raises(ValueError):
            money(value)
    assert money("0.10") + money("0.20") == Decimal("0.30")


def test_simulation_reverses_duplicate_but_never_mutates_source():
    demo = demo_bundle()
    original = deepcopy(demo["records"])
    adjustments = [{"account": "2202", "debit": "100000.00", "credit": "0.00"},
                   {"account": "1405", "debit": "0.00", "credit": "100000.00"}]
    for row in adjustments:
        row.update({"invoice_id": "INV-001", "period": "2026-09", "business_date": "2026-09-30"})
    result = simulate(original, adjustments, demo["policy"])
    assert result["balanced"] and result["violations"] == []
    assert result["delta"]["1405"] == "-100000.00"
    assert not any(f["rule_id"] == "DUPLICATE_INVOICE" for f in result["new_findings"])
    assert original == demo["records"]
    assert all("id" not in row for row in adjustments)


def test_unbalanced_simulation_and_closed_period_are_flagged():
    demo = demo_bundle("SCAS")
    demo["policy"]["locked_periods"] = ["2026-09"]
    result = simulate(demo["records"], [{"account": "1405", "debit": "0.01", "credit": "0.00",
                                       "period": "2026-09", "business_date": "2026-09-30"}], demo["policy"])
    assert not result["balanced"] and len(result["violations"]) == 2
    assert "未来适用法" in result["policy_note"]


def test_trial_balance_requires_explicit_opening():
    bundle, _ = build_family(24, 0)
    assert "OPENING_REQUIRED" in codes(bundle["records"], bundle["policy"])
    assert "TRIAL_BALANCE" not in codes(bundle["records"], bundle["policy"])


def test_all_development_fault_labels_and_oracle():
    # Development cases only: do not consume sealed truth while implementing.
    for family in range(30):
        for variant in (0, 1):
            bundle, truth = build_family(family, variant)
            assert codes(bundle["records"], bundle["policy"]) == set(truth["expected_rules"]), (family, variant)
            assert balances(bundle["records"]) == reference_balances(bundle["records"])


def test_rule_catalog_is_explicitly_experimental_and_framework_separated():
    catalog = rule_catalog()
    assert {r["framework"] for r in catalog} == {"CAS", "SCAS"}
    assert all(r["validation_status"] == "experimental" and r["source_note"] for r in catalog)
    assert len({(r["rule_id"], r["framework"]) for r in catalog}) == len(catalog)


def test_balance_can_pass_but_account_can_fail():
    bundle, _ = build_family(21, 0)
    rules = codes(bundle["records"], bundle["policy"])
    assert "VOUCHER_BALANCE" not in rules and "ACCOUNT_VALIDITY" in rules


def test_family_configuration_count_and_framework_allocation_without_test_truth():
    assert len(FAMILIES) == 48
    assert len({(tuple(r), tuple(i), tuple(p), f) for _, r, i, p, f in FAMILIES}) == 48
    assert sum(v % 2 == 0 for _ in FAMILIES for v in range(10)) == 240


def test_same_local_invoice_number_from_different_suppliers_is_not_duplicate():
    demo = demo_bundle()
    rows = deepcopy(demo["records"])
    for row in rows:
        if row.get("voucher_id") == "JV-002":
            row["counterparty"] = "另一供应商"
    assert "DUPLICATE_INVOICE" not in codes(rows, demo["policy"])


def test_thirty_independent_reference_examples():
    from benchmarks.reference_cases import run_reference_cases
    result = run_reference_cases()
    assert result["case_count"] == 30
    assert result["passed"] == 30
    assert result["official_case_count"] == 0


@given(st.lists(st.integers(min_value=0, max_value=1_000_000), min_size=1, max_size=25))
@settings(max_examples=50)
def test_many_exact_cent_movements_against_integer_oracle(values):
    rows = []
    for i, cents in enumerate(values):
        amount = f"{cents // 100}.{cents % 100:02d}"
        rows += [{"kind": "journal", "account": "1405", "debit": amount, "credit": "0.00"},
                 {"kind": "journal", "account": "2202", "debit": "0.00", "credit": amount}]
    total = sum(values)
    expected = f"{total // 100}.{total % 100:02d}"
    assert balances(rows)["1405"] == expected
    assert balances(rows) == reference_balances(rows)


def bank_row(identifier, amount, **extra):
    return {"id": identifier, "evidence_id": f"e-{identifier}", "kind": "bank", "amount": amount,
            "period": "2026-09", "business_date": "2026-09-20", "currency": "CNY",
            "counterparty": "客户甲", "order_id": "order-1", **extra}


def cash_row(identifier, debit="0.00", credit="0.00", **extra):
    return {**bank_row(identifier, "0.00"), "kind": "journal", "account": "1002",
            "debit": debit, "credit": credit, **extra}


def test_bank_reconciliation_requires_both_sides_without_assuming_zero():
    result = bank_reconciliation([bank_row("bank", "100.00")], {"period": "2026-09"})
    assert result[0]["rule_id"] == "BANK_RECONCILIATION_INPUT"
    assert result[0]["status"] == "insufficient"


def test_bank_reconciliation_both_receipts_and_payments():
    rows = [bank_row("b1", "100.00"), cash_row("j1", debit="100.00"),
            bank_row("b2", "-40.00"), cash_row("j2", credit="40.00")]
    original = deepcopy(rows)
    result = bank_reconciliation(rows, {"period": "2026-09"})
    assert len(result) == 1
    assert result[0]["rule_id"] == "BANK_CANDIDATES_ALIGNED"
    assert result[0]["metrics"]["bank_movement"] == "60.00"
    assert rows == original


def test_bank_total_difference_does_not_invent_fee_adjustment():
    rows = [bank_row("b", "99.00"), cash_row("j", debit="100.00")]
    result = bank_reconciliation(rows, {"period": "2026-09"})
    discrepancy = next(f for f in result if f["rule_id"] == "BANK_LEDGER_DIFFERENCE")
    assert discrepancy["metrics"]["difference"] == "-1.00"
    assert {f["rule_id"] for f in result} == {"BANK_LEDGER_DIFFERENCE", "BANK_UNMATCHED", "LEDGER_UNMATCHED"}
    assert all("adjustments" not in f for f in result)


def test_bank_equal_total_with_wrong_counterparty_still_has_unmatched_records():
    rows = [bank_row("b", "100.00"), cash_row("j", debit="100.00", counterparty="客户乙")]
    result = bank_reconciliation(rows, {"period": "2026-09"})
    assert {f["rule_id"] for f in result} == {"BANK_UNMATCHED", "LEDGER_UNMATCHED"}


def test_bank_accounts_cannot_cancel_each_other():
    rows = [bank_row("b1", "110.00", account="100201"), cash_row("j1", debit="100.00", account="100201"),
            bank_row("b2", "90.00", account="100202"), cash_row("j2", debit="100.00", account="100202")]
    result = bank_reconciliation(rows, {"period": "2026-09"})
    differences = [f["metrics"]["difference"] for f in result if f["rule_id"] == "BANK_LEDGER_DIFFERENCE"]
    assert sorted(differences) == ["-10.00", "10.00"]


def test_bank_missing_mapping_with_multiple_accounts_is_insufficient():
    rows = [bank_row("b", "100.00"), cash_row("j1", debit="40.00", account="100201"),
            cash_row("j2", debit="60.00", account="100202")]
    result = bank_reconciliation(rows, {"period": "2026-09"})
    assert result[0]["rule_id"] == "BANK_ACCOUNT_SCOPE"
    assert result[0]["status"] == "insufficient"


def test_bank_reconciliation_does_not_use_other_period():
    rows = [bank_row("b", "100.00"), cash_row("j", debit="100.00"),
            bank_row("future", "1000.00", period="2026-10")]
    result = bank_reconciliation(rows, {"period": "2026-09"})
    assert result[0]["rule_id"] == "BANK_CANDIDATES_ALIGNED"
    assert "future" not in result[0]["record_ids"]


def test_unknown_account_is_a_mapping_gap_not_a_business_misclassification():
    bundle, _ = build_family(21, 0)
    finding = next(f for f in check_records(bundle["records"], bundle["policy"]) if f["rule_id"] == "ACCOUNT_VALIDITY")
    assert finding["metrics"]["finding_type"] == "mapping_gap"
    assert "不证明业务科目选择错误" in finding["explanation"]
