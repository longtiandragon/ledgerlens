from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from scripts.validate_erpnext_returns import COMPANY, ITEM, SUPPLIER, ROOT, ensure_invoice, inspect
from scripts.summarize_erpnext_returns import summarize


@pytest.fixture
def report():
    # Independently handwritten documents and GL postings, not validator output.
    docs, records, stock = {}, [], []
    for label, day, qty, net, tax, grand, remain_qty, remain_value, postings in [
        (
            "purchase",
            "2026-09-21",
            "10",
            "123.40",
            "12.34",
            "135.74",
            "10",
            "123.40",
            [("1405", "123.40", "0.00"), ("TEST-TAX", "12.34", "0.00"), ("2202", "0.00", "135.74")],
        ),
        (
            "return",
            "2026-09-22",
            "-3",
            "-37.02",
            "-3.70",
            "-40.72",
            "7",
            "86.38",
            [("1405", "0.00", "37.02"), ("TEST-TAX", "0.00", "3.70"), ("2202", "40.72", "0.00")],
        ),
    ]:
        docs[label] = {
            "name": label,
            "docstatus": 1,
            "company": COMPANY,
            "supplier": SUPPLIER,
            "is_return": int(label == "return"),
            "update_stock": 1,
            "posting_date": day,
            "return_against": "purchase",
            "base_net_total": net,
            "base_total_taxes_and_charges": tax,
            "base_grand_total": grand,
            "outstanding_amount": grand,
            "items": [
                {
                    "name": label + "-line",
                    "item_code": ITEM,
                    "qty": qty,
                    "rate": "12.34",
                    "warehouse": "Stores",
                    "purchase_invoice_item": "purchase-line",
                }
            ],
            "taxes": [
                {
                    "category": "Total",
                    "charge_type": "On Net Total",
                    "add_deduct_tax": "Add",
                    "rate": "10",
                    "base_tax_amount": tax,
                }
            ],
        }
        for index, (account, debit, credit) in enumerate(postings):
            records.append(
                {
                    "source_id": f"{label}-{index}",
                    "kind": "journal",
                    "currency": "CNY",
                    "business_date": day,
                    "period": "2026-09",
                    "voucher_id": label,
                    "account": account,
                    "debit": debit,
                    "credit": credit,
                }
            )
        stock.append(
            {
                "voucher_no": label,
                "voucher_detail_no": label + "-line",
                "company": COMPANY,
                "warehouse": "Stores",
                "item_code": ITEM,
                "actual_qty": qty,
                "stock_value_difference": net,
                "qty_after_transaction": remain_qty,
                "stock_value": remain_value,
            }
        )
    return {
        "schema_version": "erpnext-tax-return-v1",
        "status": "completed",
        "company": COMPANY,
        "synthetic": True,
        "model_requests": 0,
        "documents": docs,
        "export": {"company": COMPANY, "currency": "CNY", "records": records},
        "stock": stock,
        "trial_balance": {
            "1405": ["123.40", "37.02"],
            "TEST-TAX": ["12.34", "3.70"],
            "2202": ["40.72", "135.74"],
        },
        "duration_seconds": 1,
        "source_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in (
                "scripts/validate_erpnext_returns.py",
                "scripts/validate_erpnext_semantics.py",
                "backend/app/connectors.py",
            )
        },
    }


@pytest.mark.parametrize(
    "tamper",
    [
        "none",
        "tax",
        "return_sign",
        "stock",
        "company",
        "child",
        "date",
        "currency",
        "float",
        "source_duplicate",
        "extra_gl",
        "trial_balance",
        "invented_invoice",
    ],
)
def test_independent_return_evidence_rejects_false_pass_flags(report, tamper):
    report["checks"] = {"everything_passed": True}  # Must be ignored.
    docs, records = report["documents"], report["export"]["records"]
    if tamper == "tax":
        docs["return"]["taxes"][0]["base_tax_amount"] = "-3.71"
    if tamper == "return_sign":
        docs["return"]["items"][0]["qty"] = "3"
    if tamper == "stock":
        report["stock"][1]["stock_value"] = "123.40"
    if tamper == "company":
        docs["return"]["company"] = "Another Company"
    if tamper == "child":
        docs["return"]["items"][0]["purchase_invoice_item"] = "wrong-line"
    if tamper == "date":
        docs["return"]["posting_date"] = "2026-10-13"
    if tamper == "currency":
        report["export"]["currency"] = "USD"
    if tamper == "float":
        records[0]["debit"] = 123.4
    if tamper == "source_duplicate":
        records[1]["source_id"] = records[0]["source_id"]
    if tamper == "extra_gl":
        records.append(deepcopy(records[0]))
    if tamper == "trial_balance":
        report["trial_balance"]["2202"][0] = "40.73"
    if tamper == "invented_invoice":
        records[0]["invoice_id"] = "made-up"
    assert all(inspect(report).values()) == (tamper == "none")


@pytest.mark.parametrize("tamper", ["none", "changed_doc", "changed_code", "paid", "failed_evidence", "failed_run"])
def test_summary_checks_actual_payload_and_identical_replay(tmp_path, report, tamper):
    first, replay = tmp_path / "first.json", tmp_path / "replay.json"
    first.write_text(json.dumps(report), encoding="utf-8")
    if tamper == "changed_doc":
        report["documents"]["purchase"]["modified"] = "different"
    if tamper == "changed_code":
        report["source_sha256"]["backend/app/connectors.py"] = "changed"
    if tamper == "paid":
        report["model_requests"] = 1
    if tamper == "failed_run":
        report["status"] = "failed"
    if tamper == "failed_evidence":
        report["documents"]["return"]["base_grand_total"] = "-40.73"
    replay.write_text(json.dumps(report), encoding="utf-8")
    if tamper == "none":
        assert summarize(first, replay)["recomputed_checks_per_run"] == [24, 24]
    else:
        with pytest.raises(ValueError):
            summarize(first, replay)


@pytest.mark.parametrize("statuses", [[1], [0], [2], [1, 1]])
def test_fixture_replay_only_reuses_one_submitted_invoice(statuses):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.url.params:
            assert json.loads(request.url.params["filters"])[0] == ["company", "=", COMPANY]
            return httpx.Response(
                200, json={"data": [{"name": f"pi-{i}", "docstatus": s} for i, s in enumerate(statuses)]}
            )
        return httpx.Response(200, json={"data": {"name": "pi-0", "docstatus": 1}})

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handler)) as client:
        if statuses == [1]:
            assert ensure_invoice(client, "fixture", {})["name"] == "pi-0"
        else:
            with pytest.raises(ValueError, match="no replacement"):
                ensure_invoice(client, "fixture", {})
    assert set(calls) == {"GET"}
