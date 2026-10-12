"""Reproducible purchase/settlement scenario builders; no production oracle import.

Run: python -m datasets.generate --output .local/datasets
Observed records and withheld evidence are separate from evaluator-only truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ACCOUNTS = ["1002", "1122", "1405", "2202", "6602", "9999"]


def _cash(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def _id(seed: str) -> str:
    return str(uuid5(NAMESPACE_URL, "ledgerlens:" + seed))


def _record(seed: str, kind: str, source: str, cents: int = 0, **extra) -> dict:
    return {"id": _id(f"{seed}:{source}"), "evidence_id": _id(f"{seed}:evidence:{source}"),
            "source_id": source, "kind": kind, "business_date": "2026-09-20", "period": "2026-09",
            "observed_at": "2026-09-30T12:00:00+00:00", "currency": "CNY", "counterparty": "合成供应商甲",
            "reference": source, "order_id": "PO-001", "description": "合成业务，仅用于研究验证",
            "amount": _cash(cents), **extra}


def _journal(seed: str, voucher: str, cents: int, invoice_id: str = "INV-001", debit_account: str = "1405",
             credit_account: str = "2202", **extra) -> list[dict]:
    common = {"voucher_id": voucher, "invoice_id": invoice_id, **extra}
    return [_record(seed, "journal", f"{voucher}-1", account=debit_account, debit=_cash(cents), credit="0.00", **common),
            _record(seed, "journal", f"{voucher}-2", account=credit_account, debit="0.00", credit=_cash(cents), **common)]


def demo_bundle(framework: str = "CAS") -> dict:
    if framework not in {"CAS", "SCAS"}:
        raise ValueError("framework must be CAS or SCAS")
    seed = f"demo-{framework}"
    records = [_record(seed, "order", "PO-001", 10_000_000),
               _record(seed, "invoice", "INV-001", 10_000_000, invoice_id="INV-001"),
               _record(seed, "receipt", "RCV-001", 6_000_000),
               _record(seed, "bank", "PAY-001", -6_000_000, invoice_id="INV-001", direction="outgoing"),
               _record(seed, "bank", "PAY-002", -4_000_000, invoice_id="INV-001", direction="outgoing")]
    records += _journal(seed, "JV-001", 10_000_000) + _journal(seed, "JV-002", 10_000_000)
    records += _journal(seed, "PAY-JV-001", 6_000_000, debit_account="2202", credit_account="1002")
    records += _journal(seed, "PAY-JV-002", 4_000_000, debit_account="2202", credit_account="1002")
    supplemental = [_record(seed, "receipt", "RCV-002", 4_000_000, business_date="2026-09-25",
                            observed_at="2026-10-02T09:00:00+00:00")]
    return {"policy": {"framework": framework, "version": "demo-1", "period": "2026-09", "currency": "CNY",
                       "accounts": ACCOUNTS[:-1], "locked_periods": [], "recognition_accounts": ["1405", "6602"]},
            "records": records, "supplemental_records": supplemental,
            "scenario": {"name": "十万元采购：正常分批付款、重复确认与迟到收货单", "synthetic": True,
                         "initial_as_of": "2026-09-30T23:59:59+00:00", "supplemental_as_of": "2026-10-03T00:00:00+00:00"}}


# Each family changes the graph structure and/or fault mechanism. These are
# synthetic topology/fault configurations, not 48 independently sampled industries.
# Faults are never encoded into record descriptions or IDs visible to the agent.
FAMILIES = [
    ("single_complete", [100], [100], [100], "normal"),
    ("split_receipts", [60, 40], [100], [100], "normal"),
    ("split_payments", [100], [100], [60, 40], "normal"),
    ("split_invoices", [100], [60, 40], [100], "normal"),
    ("three_receipts", [20, 30, 50], [100], [40, 60], "normal"),
    ("three_payments", [50, 50], [100], [20, 30, 50], "normal"),
    ("paired_invoice_settlement", [60, 40], [60, 40], [60, 40], "normal"),
    ("equal_payments_distinct", [25, 25, 50], [100], [50, 50], "normal"),
    ("four_way_receipts", [10, 20, 30, 40], [40, 60], [100], "normal"),
    ("invoice_reversal_rebook", [100], [100], [100], "reversal"),
    ("one_invoice_double_post", [100], [100], [100], "duplicate"),
    ("double_post_split_receipts", [60, 40], [100], [100], "duplicate"),
    ("double_post_three_payments", [100], [100], [20, 30, 50], "duplicate"),
    ("one_of_two_invoices_double", [60, 40], [60, 40], [100], "duplicate"),
    ("late_second_receipt", [60, 40], [100], [60, 40], "late_receipt"),
    ("late_final_receipt", [20, 30, 50], [100], [100], "late_receipt"),
    ("no_receipt_available", [100], [100], [100], "missing_receipt"),
    ("invoice_missing_after_split", [60, 40], [100], [60, 40], "missing_invoice_split"),
    ("invoice_values_conflict", [100], [100], [100], "invoice_conflict"),
    ("ledger_line_omitted", [100], [100], [100], "unbalanced"),
    ("split_invoice_line_omitted", [60, 40], [60, 40], [100], "unbalanced"),
    ("unmapped_inventory_account", [100], [100], [100], "account"),
    ("late_business_date", [100], [100], [100], "period"),
    ("trial_balance_mismatch", [100], [100], [100], "trial"),
    ("trial_without_opening", [100], [100], [100], "missing_opening"),
    ("over_receipt_order_change", [60, 40], [100], [100], "receipt_excess"),
    ("duplicate_and_late_receipt", [60, 40], [100], [60, 40], "duplicate_late"),
    ("duplicate_and_bad_account", [100], [100], [50, 50], "duplicate_account"),
    ("partial_booked_invoice", [100], [100], [60, 40], "partial_booking"),
    ("split_booking_exceeds_invoice", [100], [100], [50, 50], "overbooking"),
    ("four_receipts_two_invoices", [15, 15, 35, 35], [30, 70], [30, 70], "normal"),
    ("five_partial_settlements", [30, 70], [100], [10, 15, 20, 25, 30], "normal"),
    ("third_invoice_duplicate", [20, 30, 50], [20, 30, 50], [50, 50], "duplicate_last"),
    ("two_unavailable_receipts", [25, 25, 50], [50, 50], [100], "late_two"),
    ("reversal_with_partial_settlement", [40, 60], [100], [40, 60], "reversal"),
    ("split_source_conflict", [30, 70], [30, 70], [50, 50], "invoice_conflict"),
    ("wrong_account_and_period", [50, 50], [50, 50], [25, 75], "account_period"),
    ("unbalanced_and_late", [25, 75], [100], [25, 75], "unbalanced_late"),
    ("six_receipt_cumulative", [5, 10, 15, 20, 25, 25], [45, 55], [20, 30, 50], "normal"),
    ("four_invoice_combined_payment", [40, 60], [10, 20, 30, 40], [100], "normal"),
    ("five_receipt_duplicate", [10, 15, 20, 25, 30], [40, 60], [100], "duplicate"),
    ("last_of_four_missing", [10, 20, 30, 40], [30, 70], [30, 70], "late_receipt"),
    ("three_invoice_missing_voucher_line", [50, 50], [20, 30, 50], [50, 50], "unbalanced"),
    ("receipt_excess_with_split_invoices", [40, 60], [40, 60], [20, 30, 50], "receipt_excess"),
    ("conflict_and_late_evidence", [35, 65], [100], [35, 65], "conflict_late"),
    ("reversal_and_duplicate_remaining", [30, 30, 40], [100], [30, 70], "reversal_duplicate"),
    ("trial_and_account_mismatch", [20, 80], [20, 80], [40, 60], "trial_account"),
    ("identical_visible_two_causes", [50, 50], [100], [50, 50], "indeterminate"),
]


def build_family(index: int, variant: int) -> tuple[dict, dict]:
    name, receipt_parts, invoice_parts, payment_parts, fault = FAMILIES[index]
    framework = "CAS" if variant % 2 == 0 else "SCAS"
    visible_variant = variant % 2 if fault == "indeterminate" else variant
    seed = f"family-{index + 1:02d}-variant-{visible_variant:02d}"
    # Scale changes exact cents, while schedule/fault changes define families.
    unit = 100_000 + visible_variant * 173
    total = unit * 100
    records = [_record(seed, "order", "PO-001", total)]
    receipts = [_record(seed, "receipt", f"RCV-{i+1:03d}", unit * part) for i, part in enumerate(receipt_parts)]
    supplemental: list[dict] = []
    labels: list[str] = []
    if fault in {"late_receipt", "duplicate_late", "unbalanced_late", "conflict_late", "indeterminate", "late_two"}:
        n = 2 if fault == "late_two" else 1
        supplemental, receipts = receipts[-n:], receipts[:-n]
        for row in supplemental:
            row["observed_at"] = "2026-10-02T09:00:00+00:00"
        labels.append("RECEIPT_GAP")
    if fault == "missing_receipt":
        supplemental, receipts = receipts, []
        labels.append("RECEIPT_GAP")
    records.extend(receipts)
    if fault == "receipt_excess":
        records.append(_record(seed, "receipt", "RCV-EXTRA", unit * 10))
        labels.append("RECEIPT_EXCESS")
    for i, part in enumerate(invoice_parts):
        inv = f"INV-{i+1:03d}"
        records.append(_record(seed, "invoice", inv, unit * part, invoice_id=inv))
        records.extend(_journal(seed, f"JV-{i+1:03d}", unit * part, invoice_id=inv))
    for i, part in enumerate(payment_parts):
        # A combined payment is attached to the order, not falsely attributed to
        # one invoice where multiple invoices exist.
        records.append(_record(seed, "bank", f"PAY-{i+1:03d}", -unit * part,
                               direction="outgoing", invoice_id="INV-001" if len(invoice_parts) == 1 else ""))
        records.extend(_journal(seed, f"PAY-JV-{i+1:03d}", unit * part,
                                invoice_id="INV-001" if len(invoice_parts) == 1 else "",
                                debit_account="2202", credit_account="1002"))
    if fault in {"duplicate", "duplicate_late", "duplicate_account", "duplicate_last", "reversal_duplicate"}:
        chosen = len(invoice_parts)-1 if fault == "duplicate_last" else 0
        records.extend(_journal(seed, "JV-COPY", unit * invoice_parts[chosen], invoice_id=f"INV-{chosen+1:03d}"))
        labels.append("DUPLICATE_INVOICE")
    if fault in {"reversal", "reversal_duplicate"}:
        records.extend(_journal(seed, "JV-REVERSE", unit * invoice_parts[0], debit_account="2202", credit_account="1405"))
        records.extend(_journal(seed, "JV-REBOOK", unit * invoice_parts[0]))
    if fault in {"unbalanced", "unbalanced_late"}:
        records = [r for r in records if r["source_id"] != "JV-001-2"]
        labels.append("VOUCHER_BALANCE")
    if fault in {"account", "duplicate_account", "account_period", "trial_account"}:
        # Use settlement side so invoice recognition detection remains observable.
        next(r for r in records if r["source_id"] == "PAY-JV-001-1")["account"] = "8888"
        labels.append("ACCOUNT_VALIDITY")
    if fault in {"period", "account_period"}:
        for row in records:
            if row["source_id"].startswith("JV-001-"):
                row["business_date"] = "2026-08-31"
        labels.append("PERIOD_CUTOFF")
    if fault in {"invoice_conflict", "conflict_late"}:
        records.append(_record(seed, "invoice", "INV-SOURCE-2", unit * (invoice_parts[0] + 1), invoice_id="INV-001"))
        records.extend(_journal(seed, "JV-SPLIT", unit, invoice_id="INV-001"))
        labels.append("INVOICE_CONFLICT")
    if fault in {"partial_booking", "overbooking", "missing_invoice_split"}:
        records = [r for r in records if not r["source_id"].startswith("JV-001-")]
        pieces = [40, 30] if fault == "partial_booking" else [60, 60] if fault == "overbooking" else [60, 40]
        for i, part in enumerate(pieces):
            records.extend(_journal(seed, f"JV-PART-{i+1}", unit * part))
        if fault == "overbooking":
            labels.append("INVOICE_OVERBOOKED")
        if fault == "missing_invoice_split":
            records = [r for r in records if r["kind"] != "invoice"]
            labels.append("INVOICE_EVIDENCE")
    if fault in {"trial", "missing_opening", "trial_account"}:
        if fault != "missing_opening":
            records.append(_record(seed, "opening", "OPEN-1405", account="1405", debit="0.00", credit="0.00"))
        records.append(_record(seed, "trial_balance", "TB-1405", total + unit, account="1405"))
        labels.append("OPENING_REQUIRED" if fault == "missing_opening" else "TRIAL_BALANCE")
    split = "development" if index < 30 else "validation" if index < 38 else "sealed_test"
    case_id = f"case-{index+1:02d}-{variant:02d}"
    bundle = {"case_id": case_id, "family_id": f"family-{index+1:02d}", "split": split,
              "policy": {"framework": framework, "version": "synthetic-v1", "period": "2026-09", "currency": "CNY",
                         "accounts": ACCOUNTS[:-1], "locked_periods": []},
              "records": records, "synthetic": True}
    truth = {"case_id": case_id, "family_id": bundle["family_id"], "framework": framework,
             "family_name": name, "fault_mechanism": fault, "expected_rules": sorted(set(labels)),
             "hidden_root_cause": ("document_not_yet_received" if variant < 5 else "goods_not_yet_received")
             if fault == "indeterminate" else fault,
             "determinability": "insufficient" if any(x in labels for x in ["RECEIPT_GAP", "INVOICE_EVIDENCE", "INVOICE_CONFLICT", "PERIOD_CUTOFF", "OPENING_REQUIRED"]) else "normal" if not labels else "supported",
             "reference_business_total": _cash(total), "expected_evidence_after_release": [r["id"] for r in supplemental]}
    bundle["supplemental_records"] = supplemental
    return bundle, truth


def write_dataset(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    for folder in ["observed", "on_demand", "truth"]:
        (destination / folder).mkdir(exist_ok=True)
    families = []
    cases = []
    for i, spec in enumerate(FAMILIES):
        families.append({"family_id": f"family-{i+1:02d}", "name": spec[0], "receipts": spec[1],
                         "invoices": spec[2], "payments": spec[3], "fault": spec[4],
                         "split": "development" if i < 30 else "validation" if i < 38 else "sealed_test"})
        for variant in range(10):
            observable, truth = build_family(i, variant)
            evidence = observable.pop("supplemental_records")
            case_id = observable["case_id"]
            payload = json.dumps(observable, ensure_ascii=False, indent=2)
            # Hash exactly the bytes written on every platform (no Windows CRLF translation).
            (destination / "observed" / f"{case_id}.json").write_bytes(payload.encode("utf-8"))
            (destination / "on_demand" / f"{case_id}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            (destination / "truth" / f"{case_id}.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
            cases.append({"case_id": case_id, "split": observable["split"], "framework": observable["policy"]["framework"],
                          "sha256": hashlib.sha256(payload.encode()).hexdigest()})
    manifest = {"version": "1.0.0", "synthetic": True, "case_count": len(cases), "family_count": len(families),
                "coverage_note": "48 synthetic topology/fault configurations; no claim of 48 independent industry populations.",
                "truth_access": "Evaluator only. Do not mount truth/ into API or worker containers.", "families": families, "cases": cases}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "demo.json").write_text(json.dumps(demo_bundle(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"case_count": len(cases), "family_count": len(families), "destination": str(destination.resolve())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".local/datasets"))
    print(json.dumps(write_dataset(parser.parse_args().output), ensure_ascii=False))
