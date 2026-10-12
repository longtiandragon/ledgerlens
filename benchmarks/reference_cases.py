"""30 independently authored known-answer examples, NOT official MOF cases.

Expected answers below are literal reviewed arithmetic/constraint expectations.
No production function is used to generate expected answers.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path


POLICY = {"framework": "CAS", "accounts": ["1002", "1405", "2202"], "period": "2026-09", "version": "reference-v1"}


def row(identifier: str, kind: str = "journal", **values):
    return {"id": identifier, "evidence_id": "e-" + identifier, "source_id": identifier,
            "kind": kind, "currency": "CNY", "counterparty": "供应商", "order_id": "PO-1",
            "business_date": "2026-09-20", "period": "2026-09", "voucher_id": "V-1",
            "debit": "0.00", "credit": "0.00", **values}


def journal(voucher: str, amount: str, debit="1405", credit="2202", **values):
    return [row(voucher + "-d", voucher_id=voucher, account=debit, debit=amount, **values),
            row(voucher + "-c", voucher_id=voucher, account=credit, credit=amount, **values)]


def invoice(identifier="I-1", amount="100.00"):
    return row(identifier, "invoice", amount=amount, invoice_id="I-1")


def transaction(identifier, amount, **values):
    result = row(identifier, "invoice", amount=amount, **values)
    result.pop("debit")
    result.pop("credit")
    return result


def cases() -> list[dict]:
    j100 = journal("V-1", "100.00", invoice_id="I-1")
    specs = [
        ("REF-01", "精确到分累加", "balances", journal("V-1", "0.10") + journal("V-2", "0.20"), {"1405": "0.30", "2202": "-0.30"}),
        ("REF-02", "大额金额保留分位", "balances", journal("V-1", "999999999.99"), {"1405": "999999999.99", "2202": "-999999999.99"}),
        ("REF-03", "贷方期初余额", "balances", [row("O-1", "opening", account="2202", credit="19.01")], {"2202": "-19.01"}),
        ("REF-04", "反向分录后的净额", "balances", journal("V-1", "100.00") + journal("V-2", "60.00", "2202", "1405"), {"1405": "40.00", "2202": "-40.00"}),
        ("REF-05", "缺失期初不能默认零", "rules", [row("T-1", "trial_balance", account="1405", debit="100.00")], ["OPENING_REQUIRED"]),
        ("REF-06", "明确零期初可参与勾稽", "rules", [row("O-1", "opening", account="1405"), *journal("V-1", "100.00"), row("T-1", "trial_balance", account="1405", debit="100.00")], []),
        ("REF-07", "期末余额相差一分", "rules", [row("O-1", "opening", account="1405"), *journal("V-1", "100.00"), row("T-1", "trial_balance", account="1405", debit="100.01")], ["TRIAL_BALANCE"]),
        ("REF-08", "凭证缺少贷方", "rules", [row("D-1", account="1405", debit="100.00")], ["VOUCHER_BALANCE"]),
        ("REF-09", "借贷平衡但科目未知", "rules", journal("V-1", "100.00", "8888", "2202"), ["ACCOUNT_VALIDITY"]),
        ("REF-10", "正反两凭证均平衡", "rules", journal("V-1", "100.00") + journal("V-2", "100.00", "2202", "1405"), []),
        ("REF-11", "业务与记账期间不同", "rules", journal("V-1", "100.00", business_date="2026-08-31"), ["PERIOD_CUTOFF"]),
        ("REF-12", "业务日期不可解析", "rules", journal("V-1", "100.00", business_date="unknown"), ["PERIOD_VALIDITY"]),
        ("REF-13", "同一发票重复全额确认", "rules", [invoice(), *j100, *journal("V-2", "100.00", invoice_id="I-1")], ["DUPLICATE_INVOICE"]),
        ("REF-14", "发票合法分拆确认", "rules", [invoice(), *journal("V-1", "60.00", invoice_id="I-1"), *journal("V-2", "40.00", invoice_id="I-1")], []),
        ("REF-15", "重复已被反向冲销", "rules", [invoice(), *j100, *journal("V-2", "100.00", invoice_id="I-1"), *journal("V-3", "100.00", "2202", "1405", invoice_id="I-1")], []),
        ("REF-16", "多凭证但没有发票金额", "rules", [*j100, *journal("V-2", "100.00", invoice_id="I-1")], ["INVOICE_EVIDENCE"]),
        ("REF-17", "同标识发票来源冲突", "rules", [invoice(), invoice("I-2", "101.00"), *j100, *journal("V-2", "1.00", invoice_id="I-1")], ["INVOICE_CONFLICT"]),
        ("REF-18", "部分确认不等于重复", "rules", [invoice(), *journal("V-1", "40.00", invoice_id="I-1"), *journal("V-2", "30.00", invoice_id="I-1")], []),
        ("REF-19", "分拆总额超过发票", "rules", [invoice(), *journal("V-1", "60.00", invoice_id="I-1"), *journal("V-2", "60.00", invoice_id="I-1")], ["INVOICE_OVERBOOKED"]),
        ("REF-20", "采购分批收货完整", "rules", [row("O", "order", amount="100.00"), row("R1", "receipt", amount="60.00"), row("R2", "receipt", amount="40.00")], []),
        ("REF-21", "收货资料不足", "rules", [row("O", "order", amount="100.00"), row("R1", "receipt", amount="60.00")], ["RECEIPT_GAP"]),
        ("REF-22", "收货超过已知订单", "rules", [row("O", "order", amount="100.00"), row("R1", "receipt", amount="110.00")], ["RECEIPT_EXCESS"]),
        ("REF-23", "付款核销不重复确认成本", "rules", [invoice(), *j100, *journal("PAY-1", "60.00", "2202", "1002", invoice_id="I-1"), *journal("PAY-2", "40.00", "2202", "1002", invoice_id="I-1")], []),
        ("REF-24", "相同金额两笔流水不是重复凭证", "rules", [row("B1", "bank", amount="-50.00"), row("B2", "bank", amount="-50.00")], []),
        ("REF-25", "一对多精确匹配", "match", ([transaction("I", "100.00")], [transaction("B1", "60.00"), transaction("B2", "40.00")]), {"status": "candidates", "candidate_count": 1}),
        ("REF-26", "相同金额候选不能擅自选择", "match", ([transaction("I", "50.00")], [transaction("B1", "50.00"), transaction("B2", "50.00")]), {"status": "ambiguous", "candidate_count": 2}),
        ("REF-27", "币种不同不匹配", "match", ([transaction("I", "50.00")], [transaction("B", "50.00", currency="USD")]), {"status": "unmatched", "candidate_count": 0}),
        ("REF-28", "往来主体不同不匹配", "match", ([transaction("I", "50.00")], [transaction("B", "50.00", counterparty="其他主体")]), {"status": "unmatched", "candidate_count": 0}),
        ("REF-29", "退款方向不匹配应付", "match", ([transaction("I", "50.00")], [dict(transaction("B", "50.00"), kind="bank")]), {"status": "unmatched", "candidate_count": 0}),
        ("REF-30", "相隔过久保留未匹配", "match", ([transaction("I", "50.00")], [transaction("B", "50.00", business_date="2025-09-20")]), {"status": "unmatched", "candidate_count": 0}),
    ]
    return [{"id": identifier, "title": title, "type": kind, "records": deepcopy(records), "expected": expected,
             "provenance": "independently_authored_reference", "official_case": False,
             "basis": "Decimal-cent arithmetic or explicitly documented supported business constraint; not an official standards interpretation"}
            for identifier, title, kind, records, expected in specs]


def run_reference_cases() -> dict:
    # These imports call the implementation under test, never construct answers.
    from app.domain import balances, check_records
    from app.matching import match_transactions
    results = []
    for case in cases():
        try:
            if case["type"] == "balances":
                actual = balances(case["records"])
            elif case["type"] == "rules":
                actual = sorted({f["rule_id"] for f in check_records(case["records"], POLICY)})
            else:
                result = match_transactions(*case["records"])
                actual = {"status": result["status"], "candidate_count": len(result["candidates"])}
            results.append({"id": case["id"], "passed": actual == case["expected"], "actual": actual, "expected": case["expected"]})
        except Exception as exc:
            results.append({"id": case["id"], "passed": False, "error": f"{type(exc).__name__}: {exc}"})
    return {"case_count": len(results), "passed": sum(r["passed"] for r in results), "official_case_count": 0,
            "reference_case_count": len(results), "results": results}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    print(json.dumps(run_reference_cases(), ensure_ascii=False, indent=2))
