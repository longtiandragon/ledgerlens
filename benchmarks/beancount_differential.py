"""Optional third-party differential checks for a narrow CNY ledger subset.

Handwritten Beancount postings and independently handwritten LedgerLens records
are paired below. Beancount loader/validation/balance APIs produce the oracle;
production functions are used only for the actual side of the comparison.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA_VERSION = "beancount-basic-differential-v1"
REQUIRED_VERSION = "3.2.3"
ACCOUNT_MAP = {"1002": "Assets:Bank", "1122": "Assets:Receivable", "1405": "Assets:Inventory",
               "2202": "Liabilities:Payable", "4001": "Equity:Capital", "6001": "Income:Sales",
               "6602": "Expenses:Administration"}
HEADER = '''option "operating_currency" "CNY"
option "tolerance_multiplier" "0"
2000-01-01 open Assets:Bank CNY
2000-01-01 open Assets:Receivable CNY
2000-01-01 open Assets:Inventory CNY
2000-01-01 open Liabilities:Payable CNY
2000-01-01 open Equity:Capital CNY
2000-01-01 open Income:Sales CNY
2000-01-01 open Expenses:Administration CNY
'''


def row(voucher: str, when: str, account: str, debit: str, credit: str, *, kind: str = "journal") -> dict:
    """Write a fixture record; never compute a reference balance."""
    return {"id": f"{voucher}:{account}", "kind": kind, "voucher_id": voucher,
            "business_date": when, "period": when[:7], "currency": "CNY", "account": account,
            "debit": debit, "credit": credit}


@dataclass(frozen=True)
class Fixture:
    identity: str
    description: str
    source: str
    records: list[dict]
    cutoff_exclusive: str
    oracle_valid: bool = True
    business_error_not_detectable: bool = False


def fixtures() -> list[Fixture]:
    # The amounts on each side are separately written literals, never extracted
    # from production balances or rendered into Beancount from production rows.
    cents = HEADER + '''2026-01-05 * "V-CENTS"
  Assets:Bank 0.10 CNY
  Assets:Bank 0.20 CNY
  Equity:Capital -0.30 CNY
2026-02-01 balance Assets:Bank 0.30 CNY
2026-02-01 balance Equity:Capital -0.30 CNY
'''
    split = HEADER + '''2026-01-01 * "opening"
  Assets:Bank 120000.00 CNY
  Equity:Capital -120000.00 CNY
2026-01-05 * "V-PURCHASE"
  Assets:Inventory 100000.00 CNY
  Liabilities:Payable -100000.00 CNY
2026-01-10 * "V-PAYMENT-1"
  Liabilities:Payable 60000.00 CNY
  Assets:Bank -60000.00 CNY
2026-01-20 * "V-PAYMENT-2"
  Liabilities:Payable 40000.00 CNY
  Assets:Bank -40000.00 CNY
2026-02-01 balance Assets:Bank 20000.00 CNY
2026-02-01 balance Assets:Inventory 100000.00 CNY
2026-02-01 balance Liabilities:Payable 0.00 CNY
2026-02-01 balance Equity:Capital -120000.00 CNY
'''
    reversal = HEADER + '''2026-01-01 * "opening"
  Assets:Bank 1000.00 CNY
  Equity:Capital -1000.00 CNY
2026-01-08 * "V-EXPENSE"
  Expenses:Administration 299.99 CNY
  Assets:Bank -299.99 CNY
2026-01-09 * "V-REVERSAL"
  Assets:Bank 299.99 CNY
  Expenses:Administration -299.99 CNY
2026-02-01 balance Assets:Bank 1000.00 CNY
2026-02-01 balance Expenses:Administration 0.00 CNY
'''
    large = HEADER + '''2026-01-05 * "V-LARGE"
  Assets:Bank 999999999999.98 CNY
  Equity:Capital -999999999999.98 CNY
2026-01-06 * "V-LAST-CENT"
  Assets:Bank 0.01 CNY
  Equity:Capital -0.01 CNY
2026-02-01 balance Assets:Bank 999999999999.99 CNY
2026-02-01 balance Equity:Capital -999999999999.99 CNY
'''
    multi = HEADER + '''2025-12-31 * "opening"
  Assets:Bank 1000.00 CNY
  Assets:Receivable 100.00 CNY
  Equity:Capital -1100.00 CNY
2026-01-05 * "V-SALE"
  Assets:Receivable 200.00 CNY
  Income:Sales -200.00 CNY
2026-01-10 * "V-COLLECTION"
  Assets:Bank 150.00 CNY
  Assets:Receivable -150.00 CNY
2026-01-20 * "V-EXPENSE"
  Expenses:Administration 100.00 CNY
  Assets:Bank -100.00 CNY
2026-02-01 balance Assets:Bank 1050.00 CNY
2026-02-01 balance Assets:Receivable 150.00 CNY
2026-02-08 * "V-REFUND"
  Income:Sales 50.00 CNY
  Assets:Bank -50.00 CNY
2026-03-01 balance Assets:Bank 1000.00 CNY
2026-03-01 balance Income:Sales -150.00 CNY
2026-03-04 * "V-MARCH"
  Assets:Bank 0.30 CNY
  Equity:Capital -0.30 CNY
2026-04-01 balance Assets:Bank 1000.30 CNY
2026-04-01 balance Equity:Capital -1100.30 CNY
'''
    wrong = HEADER + '''; Intended purchase is incorrectly classified as administration expense.
; Double-entry arithmetic has no knowledge of that business intent.
2026-01-05 * "V-WRONG-ACCOUNT"
  Expenses:Administration 100.00 CNY
  Assets:Bank -100.00 CNY
2026-02-01 balance Expenses:Administration 100.00 CNY
2026-02-01 balance Assets:Bank -100.00 CNY
'''
    unbalanced = HEADER + '''2026-01-05 * "V-ONE-CENT-GAP"
  Expenses:Administration 100.00 CNY
  Assets:Bank -99.99 CNY
'''
    opening_jan = [row("O", "2026-01-01", "1002", "1000.00", "0.00", kind="opening"),
                   row("O", "2026-01-01", "1122", "100.00", "0.00", kind="opening"),
                   row("O", "2026-01-01", "4001", "0.00", "1100.00", kind="opening")]
    january = [*opening_jan,
               row("V-SALE", "2026-01-05", "1122", "200.00", "0.00"),
               row("V-SALE", "2026-01-05", "6001", "0.00", "200.00"),
               row("V-COLLECTION", "2026-01-10", "1002", "150.00", "0.00"),
               row("V-COLLECTION", "2026-01-10", "1122", "0.00", "150.00"),
               row("V-EXPENSE", "2026-01-20", "6602", "100.00", "0.00"),
               row("V-EXPENSE", "2026-01-20", "1002", "0.00", "100.00")]
    february = [row("O", "2026-02-01", "1002", "1050.00", "0.00", kind="opening"),
                row("O", "2026-02-01", "1122", "150.00", "0.00", kind="opening"),
                row("O", "2026-02-01", "4001", "0.00", "1100.00", kind="opening"),
                row("O", "2026-02-01", "6001", "0.00", "200.00", kind="opening"),
                row("O", "2026-02-01", "6602", "100.00", "0.00", kind="opening"),
                row("V-REFUND", "2026-02-08", "6001", "50.00", "0.00"),
                row("V-REFUND", "2026-02-08", "1002", "0.00", "50.00")]
    march = [row("O", "2026-03-01", "1002", "1000.00", "0.00", kind="opening"),
             row("O", "2026-03-01", "1122", "150.00", "0.00", kind="opening"),
             row("O", "2026-03-01", "4001", "0.00", "1100.00", kind="opening"),
             row("O", "2026-03-01", "6001", "0.00", "150.00", kind="opening"),
             row("O", "2026-03-01", "6602", "100.00", "0.00", kind="opening"),
             row("V-MARCH", "2026-03-04", "1002", "0.30", "0.00"),
             row("V-MARCH", "2026-03-04", "4001", "0.00", "0.30")]
    return [
        Fixture("exact-cents", "Exact 0.10 + 0.20 postings and explicit 0.30 credit", cents,
                [{**row("V-CENTS", "2026-01-05", "1002", "0.10", "0.00"), "id": "V-CENTS:bank-a"},
                 {**row("V-CENTS", "2026-01-05", "1002", "0.20", "0.00"), "id": "V-CENTS:bank-b"},
                 row("V-CENTS", "2026-01-05", "4001", "0.00", "0.30")], "2026-02-01"),
        Fixture("normal-split-payment", "One purchase settled with normal 60000 + 40000 payments", split,
                [row("O", "2026-01-01", "1002", "120000.00", "0.00", kind="opening"),
                 row("O", "2026-01-01", "4001", "0.00", "120000.00", kind="opening"),
                 row("V-PURCHASE", "2026-01-05", "1405", "100000.00", "0.00"),
                 row("V-PURCHASE", "2026-01-05", "2202", "0.00", "100000.00"),
                 row("V-PAYMENT-1", "2026-01-10", "2202", "60000.00", "0.00"),
                 row("V-PAYMENT-1", "2026-01-10", "1002", "0.00", "60000.00"),
                 row("V-PAYMENT-2", "2026-01-20", "2202", "40000.00", "0.00"),
                 row("V-PAYMENT-2", "2026-01-20", "1002", "0.00", "40000.00")], "2026-02-01"),
        Fixture("refund-reversal", "Opposite debit/credit reversal; zero expense account retained", reversal,
                [row("O", "2026-01-01", "1002", "1000.00", "0.00", kind="opening"),
                 row("O", "2026-01-01", "4001", "0.00", "1000.00", kind="opening"),
                 row("V-EXPENSE", "2026-01-08", "6602", "299.99", "0.00"),
                 row("V-EXPENSE", "2026-01-08", "1002", "0.00", "299.99"),
                 row("V-REVERSAL", "2026-01-09", "1002", "299.99", "0.00"),
                 row("V-REVERSAL", "2026-01-09", "6602", "0.00", "299.99")], "2026-02-01"),
        Fixture("large-exact-cent", "Supported maximum balance with one-cent addition", large,
                [row("V-LARGE", "2026-01-05", "1002", "999999999999.98", "0.00"),
                 row("V-LARGE", "2026-01-05", "4001", "0.00", "999999999999.98"),
                 row("V-LAST-CENT", "2026-01-06", "1002", "0.01", "0.00"),
                 row("V-LAST-CENT", "2026-01-06", "4001", "0.00", "0.01")], "2026-02-01"),
        Fixture("multi-month-january", "Historical source as of February versus January opening + movement", multi,
                january, "2026-02-01"),
        Fixture("multi-month-february", "Historical source as of March versus independent February carryforward", multi,
                february, "2026-03-01"),
        Fixture("multi-month-march", "Historical source as of April versus independent March carryforward", multi,
                march, "2026-04-01"),
        Fixture("balanced-wrong-account-boundary", "Balanced but wrong account cannot be detected by this oracle", wrong,
                [row("V-WRONG-ACCOUNT", "2026-01-05", "6602", "100.00", "0.00"),
                 row("V-WRONG-ACCOUNT", "2026-01-05", "1002", "0.00", "100.00")], "2026-02-01",
                business_error_not_detectable=True),
        Fixture("unbalanced-one-cent", "One-cent imbalance rejected by both validation subsets", unbalanced,
                [row("V-ONE-CENT-GAP", "2026-01-05", "6602", "100.00", "0.00"),
                 row("V-ONE-CENT-GAP", "2026-01-05", "1002", "0.00", "99.99")], "2026-02-01",
                oracle_valid=False),
    ]


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _restrict_source(source: str) -> None:
    account = r"(?:Assets|Liabilities|Equity|Income|Expenses)(?::[A-Z][A-Za-z0-9]*)+"
    allowed = [r'option "operating_currency" "CNY"', r'option "tolerance_multiplier" "0"',
               rf"\d{{4}}-\d{{2}}-\d{{2}} open {account} CNY",
               r'\d{4}-\d{2}-\d{2} \* "[A-Za-z0-9 -]+"',
               rf"{account} [-+]?\d+\.\d{{2}} CNY",
               rf"\d{{4}}-\d{{2}}-\d{{2}} balance {account} [-+]?\d+\.\d{{2}} CNY"]
    for line in source.splitlines():
        text = line.strip()
        if text and not text.startswith(";") and not any(re.fullmatch(pattern, text) for pattern in allowed):
            raise ValueError("Source is outside explicit-posting CNY subset; includes/plugins/inference are forbidden")
    if source.count('option "tolerance_multiplier" "0"') != 1:
        raise ValueError("Exact-cent differential requires explicit zero validation tolerance")


def oracle(source: str, cutoff_exclusive: str) -> dict:
    """Compute reference values solely with Beancount; import no production code."""
    _restrict_source(source)
    from beancount import loader
    from beancount.core.data import Transaction
    from beancount.ops import summarize, validation

    entries, errors, options = loader.load_string(source)
    checked = validation.validate(entries, options)
    balance_errors = validation.validate_check_transaction_balances(entries, options)
    cutoff = date.fromisoformat(cutoff_exclusive)
    imbalance_vouchers = sorted({error.entry.narration for error in balance_errors
                                 if error.entry.date < cutoff})
    other_error_types = sorted({type(error).__name__ for error in [*errors, *checked]
                               if error not in balance_errors})
    error_types = sorted({type(error).__name__ for error in [*errors, *checked]})
    if error_types:
        return {"valid": False, "error_types": error_types, "balances": None,
                "imbalance_vouchers": imbalance_vouchers, "other_error_types": other_error_types,
                "api": ["loader.load_string", "ops.validation.validate",
                        "ops.validation.validate_check_transaction_balances"]}
    for entry in entries:
        if isinstance(entry, Transaction):
            for posting in entry.postings:
                if (posting.cost is not None or posting.price is not None or posting.units is None
                        or posting.units.currency != "CNY"):
                    raise ValueError("Oracle accepted a posting outside the supported semantic subset")
    inventory, _ = summarize.balance_by_account(entries, cutoff)
    numbers = {}
    for account, positions in inventory.items():
        number = positions.get_currency_units("CNY").number
        if not number.is_finite() or number != number.quantize(Decimal("0.01")):
            raise ValueError("Oracle balance is not an exact cent value")
        numbers[account] = format(number.quantize(Decimal("0.01")), ".2f")
    return {"valid": True, "error_types": [], "other_error_types": [], "imbalance_vouchers": [],
            "balances": numbers,
            "api": ["loader.load_string", "ops.validation.validate", "ops.summarize.balance_by_account",
                    "core.inventory.Inventory.get_currency_units"],
            "cutoff_exclusive": cutoff_exclusive}


def compare_fixture(fixture: Fixture) -> dict:
    records = deepcopy(fixture.records)
    cutoff = date.fromisoformat(fixture.cutoff_exclusive)
    record_ids = set()
    for record in records:
        if not isinstance(record.get("id"), str) or not record["id"] or record["id"] in record_ids:
            raise ValueError("Production fixture records require unique explicit source IDs")
        record_ids.add(record["id"])
        if (record.get("kind") not in {"opening", "journal"} or record.get("currency") != "CNY"
                or record.get("account") not in ACCOUNT_MAP):
            raise ValueError("Production record is outside the CNY general-ledger subset")
        if (date.fromisoformat(record["business_date"]) >= cutoff
                or record["period"] != record["business_date"][:7]):
            raise ValueError("Production record is outside the explicit date cutoff or posting period")
        for field in ["debit", "credit"]:
            value = record.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d{2}", value):
                raise ValueError("Production fixture amounts must be explicit nonnegative cent strings")
    input_hash = hashlib.sha256(canonical(records)).hexdigest()
    reference = oracle(fixture.source, fixture.cutoff_exclusive)
    # This import deliberately occurs only after the independent oracle finishes.
    from app.domain import balances, check_records

    actual = balances(records)
    mapped = {}
    for account, number in actual.items():
        if account not in ACCOUNT_MAP:
            raise ValueError("Production fixture account has no semantic Beancount mapping")
        mapped[ACCOUNT_MAP[account]] = number
    findings = check_records(records, {"accounts": list(ACCOUNT_MAP)})
    actual_imbalances = [finding["metrics"]["voucher_id"] for finding in findings
                         if finding["rule_id"] == "VOUCHER_BALANCE"]
    differences = []
    if reference["valid"]:
        expected = reference["balances"]
        for account in sorted(set(expected) | set(mapped)):
            if expected.get(account) != mapped.get(account):
                differences.append({"account": account, "oracle": expected.get(account),
                                    "production": mapped.get(account)})
    # Invalid Beancount data is not a source of successful numeric expected
    # values. The negative fixture compares only imbalance rejection behavior.
    valid_as_expected = reference["valid"] == fixture.oracle_valid
    balance_agrees = not differences if reference["valid"] else None
    rejection_agrees = (not reference["other_error_types"]
                        and sorted(actual_imbalances) == reference["imbalance_vouchers"])
    unchanged = input_hash == hashlib.sha256(canonical(records)).hexdigest()
    return {"id": fixture.identity, "description": fixture.description,
            "status": "passed" if valid_as_expected and balance_agrees is not False and rejection_agrees and unchanged
            else "failed", "source_sha256": hashlib.sha256(fixture.source.encode()).hexdigest(),
            "record_sha256": input_hash, "oracle": reference, "actual_balances": mapped,
            "balance_agrees": balance_agrees, "differences": differences,
            "production_imbalance_vouchers": actual_imbalances,
            "validation_classification_agrees": rejection_agrees, "original_records_unchanged": unchanged,
            "business_error_detection": "outside_oracle_capability" if fixture.business_error_not_detectable
            else "not_evaluated"}


def package_inventory() -> list[dict]:
    return [{"name": name, "version": metadata.version(name),
             "license_metadata": metadata.metadata(name).get("License-Expression")
             or metadata.metadata(name).get("License"),
             "source_url": f"https://pypi.org/project/{name}/{metadata.version(name)}/"}
            for name in ["beancount", "regex", "click", "python-dateutil"]]


def run() -> dict:
    report = {"schema_version": SCHEMA_VERSION, "run_at": datetime.now(timezone.utc).isoformat(),
              "scope": "single_CNY_explicit_postings_signed_debit_minus_credit_without_cost_or_price",
              "source_urls": {"repository": "https://github.com/beancount/beancount",
                              "package": "https://pypi.org/project/beancount/3.2.3/",
                              "license": "https://raw.githubusercontent.com/beancount/beancount/v3/COPYING"},
              "fixture_origin": "independently_handwritten_two_representations_not_production_generated",
              "accounting_standard_oracle": False, "real_business_accuracy_measured": None,
              "limitations": ["No CAS/SCAS recognition, importance, period correction or business validity oracle.",
                              "Balance_by_account cutoff is exclusive; production fixture supplies explicit carryforward.",
                              "Zero tolerance and exact cents; no currency conversions, costs, prices or auto postings.",
                              "Balanced wrong account fixture passes arithmetic deliberately and shows this limitation.",
                              "No third-party source code copied or dependency bundled into application runtime."]}
    try:
        packages = package_inventory()
    except metadata.PackageNotFoundError:
        return {**report, "status": "dependency_unavailable", "cases": [], "passed": 0, "failed": 0}
    if packages[0]["version"] != REQUIRED_VERSION:
        return {**report, "status": "unsupported_dependency_version", "packages": packages,
                "cases": [], "passed": 0, "failed": 0}
    results = []
    for fixture in fixtures():
        try:
            results.append(compare_fixture(fixture))
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            results.append({"id": fixture.identity, "status": "failed", "failure_type": type(error).__name__})
    from app.domain import rule_identity

    passed = sum(result["status"] == "passed" for result in results)
    return {**report, "status": "passed" if passed == len(results) else "failed", "packages": packages,
            "production_rule_identity": rule_identity(),
            "cases": results, "case_count": len(results), "passed": passed, "failed": len(results) - passed,
            "valid_balance_comparisons": sum(result.get("oracle", {}).get("valid") is True for result in results),
            "negative_imbalance_checks": sum(result.get("oracle", {}).get("valid") is False for result in results),
            "balance_difference_count": sum(len(result.get("differences", [])) for result in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/beancount-validation-summary.json"))
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result.get(key) for key in ["status", "case_count", "passed", "failed",
                                                    "valid_balance_comparisons", "negative_imbalance_checks",
                                                    "balance_difference_count"]}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
