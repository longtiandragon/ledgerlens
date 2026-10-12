"""Acceptance of the read-only ERPNext GL adapter against a real local ERPNext.

Requires `docker compose -f infra/erpnext/compose.yaml up -d` with the site created.
Posts hand-specified synthetic journal entries (plus a cancelled entry and one
outside the date range), exports with the production adapter, and compares
against (1) hand-written expectations and (2) ERPNext's own GL aggregates. Then
imports the export into a running LedgerLens API and compares balances.
No model calls. All data is synthetic and labelled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.connectors import fetch_erpnext_gl  # noqa: E402

COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra/erpnext/compose.yaml")]
COMPANY, ABBR = "LedgerLens Synthetic Co", "LLS"
SUPPLIER = "合成供应商甲"
# Hand-specified postings (synthetic). Expected September GL totals are written by hand below.
ENTRIES = [
    ("purchase", "2026-09-05", [("Administrative Expenses", "100000.00", "0"), ("Creditors", "0", "100000.00")]),
    ("duplicate", "2026-09-06", [("Administrative Expenses", "100000.00", "0"), ("Creditors", "0", "100000.00")]),
    ("payment-1", "2026-09-10", [("Creditors", "60000.00", "0"), ("Cash", "0", "60000.00")]),
    ("payment-2", "2026-09-20", [("Creditors", "40000.00", "0"), ("Cash", "0", "40000.00")]),
]
CANCELLED = ("cancelled", "2026-09-15", [("Administrative Expenses", "777.77", "0"), ("Creditors", "0", "777.77")])
OUTSIDE = ("october", "2026-10-02", [("Administrative Expenses", "555.55", "0"), ("Creditors", "0", "555.55")])
# ERPNext forbids manual journal entries to stock accounts (perpetual inventory), so the
# synthetic purchase is posted to an expense account.
MAPPING_ROLES = {"Administrative Expenses": "6602", "Creditors": "2202", "Cash": "1002"}
EXPECTED = {  # account code -> (debit, credit) for 2026-09, written by hand
    "6602": (Decimal("200000.00"), Decimal("0")),
    "2202": (Decimal("100000.00"), Decimal("200000.00")),
    "1002": (Decimal("0"), Decimal("100000.00")),
}


def bench(*args: str) -> str:
    result = subprocess.run([*COMPOSE, "exec", "-T", "backend", "bench", "--site", "frontend", *args],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if result.returncode:
        raise RuntimeError(f"bench {args[0]} failed: {result.stderr.strip()[-600:]}")
    return result.stdout.strip()


def ensure_setup() -> None:
    done = bench("execute", "frappe.db.get_single_value", "--args", json.dumps(["System Settings", "setup_complete"]))
    if done.strip().endswith("1"):
        return
    bench("execute", "frappe.desk.page.setup_wizard.setup_wizard.setup_complete", "--kwargs", json.dumps({"args": {
        "language": "English", "country": "China", "timezone": "Asia/Shanghai", "currency": "CNY",
        "full_name": "Synthetic Admin", "email": "synthetic-admin@example.invalid",
        "company_name": COMPANY, "company_abbr": ABBR, "chart_of_accounts": "Standard",
        "fy_start_date": "2026-01-01", "fy_end_date": "2026-12-31", "setup_demo": 0}}))


def api_credentials() -> tuple[str, str]:
    secret_output = bench("execute", "frappe.core.doctype.user.user.generate_keys", "--args", json.dumps(["Administrator"]))
    secret = json.loads(secret_output.splitlines()[-1])["api_secret"]
    key = bench("execute", "frappe.db.get_value", "--args", json.dumps(["User", "Administrator", "api_key"]))
    return key.splitlines()[-1].strip().strip('"'), secret


def post_entries(client: httpx.Client, accounts: dict[str, str]) -> dict:
    if not client.get("/api/resource/Supplier", params={"filters": json.dumps([["name", "=", SUPPLIER]])}).json()["data"]:
        client.post("/api/resource/Supplier", json={"supplier_name": SUPPLIER, "supplier_group": "All Supplier Groups"}
                    ).raise_for_status()
    # Idempotent: entries from an earlier run are reused, never posted twice.
    existing = client.get("/api/resource/Journal Entry", params={
        "fields": json.dumps(["name", "user_remark", "docstatus"]), "limit_page_length": 100,
        "filters": json.dumps([["company", "=", COMPANY], ["user_remark", "like", "LedgerLens synthetic acceptance:%"]])
    }).json()["data"]
    names = {row["user_remark"].split(": ", 1)[1]: row["name"] for row in existing if row["docstatus"] in (1, 2)}
    for label, posting_date, lines in [*ENTRIES, CANCELLED, OUTSIDE]:
        if label in names:
            continue
        rows = []
        for role, debit, credit in lines:
            row = {"account": accounts[role], "debit_in_account_currency": debit, "credit_in_account_currency": credit}
            if role == "Creditors":
                row.update(party_type="Supplier", party=SUPPLIER)
            rows.append(row)
        response = client.post("/api/resource/Journal Entry", json={
            "company": COMPANY, "posting_date": posting_date, "voucher_type": "Journal Entry",
            "user_remark": f"LedgerLens synthetic acceptance: {label}", "accounts": rows, "docstatus": 1})
        response.raise_for_status()
        names[label] = response.json()["data"]["name"]
    status = client.get(f"/api/resource/Journal Entry/{names['cancelled']}").json()["data"]["docstatus"]
    if status == 1:
        client.post("/api/method/frappe.client.cancel", json={"doctype": "Journal Entry", "name": names["cancelled"]}
                    ).raise_for_status()
    return names


def erpnext_aggregates(client: httpx.Client, accounts: dict[str, str]) -> dict:
    """ERPNext's own Trial Balance report: period movements computed by its accounting logic,
    a path independent of the adapter's row-by-row GL conversion."""
    response = client.post("/api/method/frappe.desk.query_report.run", json={
        "report_name": "Trial Balance", "filters": {"company": COMPANY, "fiscal_year": "2026",
                                                    "from_date": "2026-09-01", "to_date": "2026-09-30",
                                                    "show_zero_values": 0}})
    response.raise_for_status()
    code = {name: MAPPING_ROLES[role] for role, name in accounts.items()}
    return {code[row["account"]]: (Decimal(str(row["debit"])).quantize(Decimal("0.01")),
                                   Decimal(str(row["credit"])).quantize(Decimal("0.01")))
            for row in response.json()["message"]["result"]
            if isinstance(row, dict) and row.get("account") in code}


def totals(records: list[dict]) -> dict:
    result: dict[str, list[Decimal]] = {}
    for row in records:
        pair = result.setdefault(row["account"], [Decimal("0"), Decimal("0")])
        pair[0] += Decimal(row["debit"])
        pair[1] += Decimal(row["credit"])
    return {k: (v[0].quantize(Decimal("0.01")), v[1].quantize(Decimal("0.01"))) for k, v in result.items()}


def verify(url: str, ledgerlens: str) -> dict:
    report = {"schema_version": "erpnext-acceptance-v1", "started_at": datetime.now(UTC).isoformat(),
              "erpnext_image": "frappe/erpnext:v16.50.0", "synthetic": True, "model_requests": 0, "checks": {}}
    checks = report["checks"]
    try:
        ensure_setup()
        key, secret = api_credentials()
        with httpx.Client(base_url=url, headers={"Authorization": f"token {key}:{secret}"}, timeout=60,
                          trust_env=False) as client:
            listed = client.get("/api/resource/Account", params={
                "filters": json.dumps([["company", "=", COMPANY], ["is_group", "=", 0]]),
                "fields": json.dumps(["name", "account_name"]), "limit_page_length": 500}).json()["data"]
            accounts = {role: next(a["name"] for a in listed if a["account_name"] == role) for role in MAPPING_ROLES}
            report["voucher_names"] = post_entries(client, accounts)
            aggregates = erpnext_aggregates(client, accounts)
        mapping = {name: MAPPING_ROLES[role] for role, name in accounts.items()}
        export = asyncio.run(fetch_erpnext_gl(url, key, secret, COMPANY, "2026-09-01", "2026-09-30", mapping))
        records = export["records"]
        exported = totals(records)
        vouchers = {row["voucher_id"] for row in records}
        checks["company_currency_cny"] = export["currency"] == "CNY"
        checks["exported_totals_match_hand_expectations"] = exported == EXPECTED
        checks["exported_totals_match_erpnext_aggregates"] = exported == aggregates
        checks["cancelled_entry_excluded"] = report["voucher_names"]["cancelled"] not in vouchers
        checks["out_of_range_entry_excluded"] = report["voucher_names"]["october"] not in vouchers
        checks["eight_lines_four_vouchers"] = len(records) == 8 and len(vouchers) == 4
        checks["amounts_are_exact_strings"] = all(isinstance(r["debit"], str) and "." in r["debit"] for r in records)
        report["exported_totals"] = {k: [str(a), str(b)] for k, (a, b) in exported.items()}

        export_path = ROOT / ".local/reports/erpnext-export-v1.json"
        export_path.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
        with httpx.Client(base_url=ledgerlens, timeout=60, trust_env=False) as api:
            book = api.post("/api/v1/books", json={"name": "ERPNext联调 · 合成", "framework": "CAS",
                                                   "period": "2026-09"}).json()
            preview = api.post("/api/v1/imports", data={"book_id": book["id"]},
                               files={"file": ("erpnext-gl.json", export_path.read_bytes(), "application/json")}).json()
            checks["import_preview_without_errors"] = not preview.get("errors") and preview["record_count"] == 8
            api.post(f"/api/v1/imports/{preview['id']}/confirm").raise_for_status()
            snapshot = api.post("/api/v1/snapshots", json={"book_id": book["id"]}).json()
            run = api.post("/api/v1/check-runs", json={"book_id": book["id"], "snapshot_id": snapshot["id"]}).json()
            rules = sorted({case["rule_id"] for case in run["cases"]})
            report["ledgerlens_rules"] = rules
            checks["no_voucher_balance_findings"] = "VOUCHER_BALANCE" not in rules
            stored = api.get(f"/api/v1/books/{book['id']}/records").json()
            checks["ledgerlens_totals_match_export"] = totals(
                [r for r in stored if r.get("kind") == "journal"]) == exported
            report["book_id"] = book["id"]
    except Exception as exc:
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:500],
                           "where": f"{Path(frame.filename).name}:{frame.lineno}"}
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["passed"] = bool(checks) and all(checks.values()) and "error" not in report
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8090")
    parser.add_argument("--ledgerlens", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/reports/erpnext-acceptance-v1.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite an existing acceptance report")
    started = time.time()
    report = verify(args.url, args.ledgerlens)
    report["duration_seconds"] = round(time.time() - started, 1)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("passed", "checks", "error", "ledgerlens_rules")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
