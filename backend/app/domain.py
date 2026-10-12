"""Deterministic, snapshot-local accounting checks. No model output is trusted here.

All money crosses the API as decimal strings. Records supplied to these functions
must belong to a single immutable snapshot. Functions never mutate input records.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from pathlib import Path
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
CAS_SOURCE = "https://kjs.mof.gov.cn/zt/kjzzss/kuaijizhunzeshishi/200806/t20080618_46220.htm"
SCAS_SOURCE = "https://www.mof.gov.cn/bd/mhwzzxly/hjs/202401/t20240121_3926735.htm"
RULE_VERSION = "0.1.0"
# Capture once at module import, together with the running implementation. LF
# normalization keeps the same source checkout stable across Windows and Linux.
_RULE_SOURCE_SHA256 = hashlib.sha256(b'\0'.join(
    name.encode() + b'\0' + Path(__file__).with_name(name).read_text(encoding='utf-8').encode()
    for name in ('domain.py', 'matching.py')
)).hexdigest()


def rule_identity() -> dict[str, str]:
    return {'version': RULE_VERSION, 'source_sha256': _RULE_SOURCE_SHA256}


def rule_identity_key() -> str:
    return f'{RULE_VERSION}:{_RULE_SOURCE_SHA256}'


def ensure_rule_compatibility(policy: dict, check_run_version: str | None = None) -> None:
    if policy.get('execution_rules') != rule_identity():
        raise ValueError('快照执行规则版本或源码指纹与当前程序不一致；请创建新快照重新检查，历史底稿仍可查看')
    if check_run_version is not None and check_run_version != rule_identity_key():
        raise ValueError('检查记录的执行规则版本与当前程序不一致；禁止用新规则复现旧结论')


def money(value: Any) -> Decimal:
    if value is None or isinstance(value, (bool, float)):
        raise ValueError("Money must be an explicit finite decimal string or integer")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid decimal amount") from exc
    if not result.is_finite() or abs(result) > Decimal("999999999999.99"):
        raise ValueError("Amount is outside the supported finite range")
    if result != result.quantize(CENT):
        raise ValueError("Amount must be finite and exact to cents")
    return result.quantize(CENT)


def formatted(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def _amount(record: dict) -> Decimal:
    return money(record.get("amount", "0.00"))


def _net(record: dict) -> Decimal:
    if "debit" in record or "credit" in record:
        return money(record.get("debit", "0.00")) - money(record.get("credit", "0.00"))
    return _amount(record)


def balances(records: list[dict]) -> dict[str, str]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in records:
        if row.get("kind") in {"journal", "opening"}:
            totals[str(row.get("account", ""))] += _net(row)
    return {account: formatted(value) for account, value in sorted(totals.items())}


def _finding(rule: str, title: str, rows: list[dict], explanation: str,
             status: str = "supported", severity: str = "medium", missing: list[str] | None = None,
             metrics: dict | None = None) -> dict:
    return {"rule_id": rule, "title": title, "severity": severity, "status": status,
            "record_ids": sorted({str(r["id"]) for r in rows if r.get("id")}),
            "evidence_ids": sorted({str(r["evidence_id"]) for r in rows if r.get("evidence_id")}),
            "explanation": explanation, "missing_evidence": missing or [], "metrics": metrics or {}}


def _recognition(row: dict, policy: dict) -> bool:
    configured = policy.get("recognition_accounts")
    account = str(row.get("account", ""))
    return account in configured if configured else account.startswith(("14", "16", "5", "6"))


def check_records(records: list[dict], policy: dict) -> list[dict]:
    """Return explicit findings; absence of findings is not a compliance opinion."""
    findings: list[dict] = []
    journals = [r for r in records if r.get("kind") == "journal"]
    vouchers: dict[tuple[str, str], list[dict]] = defaultdict(list)
    allowed = {str(a.get("code", a.get("account", ""))) if isinstance(a, dict) else str(a)
               for a in policy.get("accounts", [])}
    for row in journals:
        vouchers[(str(row.get("voucher_id") or row.get("source_id") or row.get("id")),
                  str(row.get("currency", "CNY")))].append(row)
        account = str(row.get("account", ""))
        if allowed and account not in allowed:
            findings.append(_finding("ACCOUNT_VALIDITY", "科目未映射", [row],
                                     f"科目 {account} 不在当前账套的已确认科目表中；这仅证明映射缺口，不证明业务科目选择错误。",
                                     missing=["科目映射确认"], metrics={"finding_type": "mapping_gap", "account": account}))
        if money(row.get("debit", "0")) < ZERO or money(row.get("credit", "0")) < ZERO:
            findings.append(_finding("JOURNAL_SIGN", "借贷金额格式不符合约定", [row],
                                     "冲销应通过相反借贷方向表达，借贷字段不可为负。", severity="high"))
        try:
            business_month = date.fromisoformat(str(row["business_date"])).strftime("%Y-%m")
        except (ValueError, KeyError):
            findings.append(_finding("PERIOD_VALIDITY", "缺少有效业务日期", [row],
                                     "无法确定该记录的业务发生期间。", status="insufficient", missing=["业务日期"]))
            continue
        if row.get("period") != business_month:
            findings.append(_finding("PERIOD_CUTOFF", "业务日期与记账期间不同", [row],
                                     "跨期本身不构成错账，需核对确认时点、迟到资料和前期差错处理条件。",
                                     status="insufficient", missing=["确认时点或跨期处理说明"],
                                     metrics={"business_month": business_month, "posting_period": row.get("period")}))
    for (voucher_id, currency), rows in sorted(vouchers.items()):
        difference = sum((_net(r) for r in rows), ZERO)
        if difference != ZERO:
            findings.append(_finding("VOUCHER_BALANCE", "凭证借贷不平衡", rows,
                                     f"凭证 {voucher_id} 的借减贷差额为 {formatted(difference)} {currency}。",
                                     severity="high", metrics={"difference": formatted(difference), "voucher_id": voucher_id}))

    invoices: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in records:
        if row.get("invoice_id"):
            invoices[(str(row["invoice_id"]), str(row.get("currency", "CNY")),
                      str(row.get("counterparty", "")).strip().casefold())].append(row)
    for (invoice_id, currency, _), rows in sorted(invoices.items()):
        docs = [r for r in rows if r.get("kind") == "invoice"]
        booked: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            if row.get("kind") == "journal":
                booked[str(row.get("voucher_id") or row.get("source_id") or row.get("id"))].append(row)
        positive, reversed_amount = [], ZERO
        for voucher, lines in sorted(booked.items()):
            recognition = sum((_net(r) for r in lines if _recognition(r, policy)), ZERO)
            # Settlement entries (payable debit / bank credit) are not invoice recognition.
            if recognition > ZERO:
                positive.append((voucher, sum((money(r.get("debit", "0")) for r in lines), ZERO), lines))
            elif recognition < ZERO:
                reversed_amount += sum((money(r.get("credit", "0")) for r in lines), ZERO)
        if len(positive) < 2:
            continue
        unique_amounts = {_amount(r) for r in docs}
        if not docs:
            findings.append(_finding("INVOICE_EVIDENCE", "一票多凭证待核实", rows,
                                     "同一发票关联多张确认凭证，未取得发票金额，不能排除正常分拆。",
                                     status="insufficient", missing=[f"发票 {invoice_id} 原始金额和明细"]))
            continue
        if len(unique_amounts) != 1:
            findings.append(_finding("INVOICE_CONFLICT", "发票来源金额冲突", docs,
                                     "同一发票标识存在不同金额，应先核对原始单据。", status="insufficient",
                                     missing=["原始发票核验"], metrics={"amounts": sorted(formatted(x) for x in unique_amounts)}))
            continue
        expected = next(iter(unique_amounts))
        total = sum((v[1] for v in positive), ZERO) - reversed_amount
        if expected > ZERO and total > expected:
            full_count = sum(v[1] == expected for v in positive)
            rule = "DUPLICATE_INVOICE" if full_count >= 2 else "INVOICE_OVERBOOKED"
            findings.append(_finding(rule, "发票重复确认" if full_count >= 2 else "发票分拆确认超额", rows,
                                     "确认凭证净额超过发票金额；已扣除可见反向确认凭证，并排除付款核销凭证。",
                                     severity="high", metrics={"invoice_id": invoice_id, "invoice_amount": formatted(expected),
                                     "recognized_amount": formatted(total), "excess": formatted(total - expected),
                                     "voucher_ids": [v[0] for v in positive]}))

    chains: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in records:
        if row.get("order_id"):
            chains[(str(row["order_id"]), str(row.get("currency", "CNY")), str(row.get("counterparty", "")))].append(row)
    for (order_id, currency, _), rows in sorted(chains.items()):
        orders = [r for r in rows if r.get("kind") == "order"]
        if not orders:
            continue
        expected = sum((_amount(r) for r in orders), ZERO)
        receipts = [r for r in rows if r.get("kind") == "receipt"]
        received = sum((_amount(r) for r in receipts), ZERO)
        if received < expected:
            findings.append(_finding("RECEIPT_GAP", "收货证据尚未齐全", orders + receipts,
                                     "当前快照中的收货金额少于订单金额，可能为正常分批或迟到单据，尚不能认定账务错误。",
                                     status="insufficient", missing=[f"订单 {order_id} 剩余收货或取消证明"],
                                     metrics={"order_amount": formatted(expected), "received_amount": formatted(received),
                                              "gap": formatted(expected - received)}))
        elif received > expected:
            findings.append(_finding("RECEIPT_EXCESS", "收货证据超过订单金额", orders + receipts,
                                     "应核对订单变更、退货及单据关联；该差异并非舞弊结论。",
                                     metrics={"order_amount": formatted(expected), "received_amount": formatted(received)}))

    # Balance validation is blocked unless there is an explicit opening balance for the account.
    calculated = balances(records)
    opening_accounts = {str(r.get("account", "")) for r in records if r.get("kind") == "opening"}
    for row in records:
        if row.get("kind") != "trial_balance":
            continue
        account = str(row.get("account", ""))
        if account not in opening_accounts:
            findings.append(_finding("OPENING_REQUIRED", "期初余额缺失", [row],
                                     "不能假设期初余额为零。", status="insufficient", missing=[f"科目 {account} 期初余额"]))
        elif money(calculated.get(account, "0.00")) != _net(row):
            related = [r for r in records if r.get("account") == account and r.get("kind") in {"opening", "journal", "trial_balance"}]
            findings.append(_finding("TRIAL_BALANCE", "期初与发生额不能衔接期末", related,
                                     "导入的期末余额不等于期初余额加本快照发生额。",
                                     metrics={"account": account, "calculated": calculated.get(account, "0.00"),
                                              "reported": formatted(_net(row))}))
    return sorted(findings, key=lambda x: (x["rule_id"], tuple(x["record_ids"])))


def simulate(records: list[dict], adjustments: list[dict], policy: dict) -> dict:
    """Apply additional entries to a copy. Never rewrite closed periods or source rows."""
    if not adjustments:
        raise ValueError("At least one adjustment is required")
    added = deepcopy(adjustments)
    violations: list[str] = []
    for index, row in enumerate(added):
        if row.get("kind", "journal") != "journal":
            raise ValueError("Only journal adjustments are supported")
        row["kind"] = "journal"
        row.setdefault("id", f"simulation-{index + 1}")
        row.setdefault("voucher_id", "simulation-adjustment")
        row.setdefault("currency", "CNY")
        if row.get("invoice_id") and not row.get("counterparty"):
            # Only unambiguous source-document metadata may be inherited. This
            # affects the draft, never the original record or entity registry.
            parties = {r.get("counterparty", "") for r in records if r.get("kind") == "invoice"
                       and r.get("invoice_id") == row["invoice_id"] and r.get("currency", "CNY") == row["currency"]}
            if len(parties) == 1:
                row["counterparty"] = next(iter(parties))
        if not row.get("account") or "debit" not in row or "credit" not in row:
            raise ValueError("Adjustment requires account, debit and credit")
        if money(row["debit"]) < ZERO or money(row["credit"]) < ZERO:
            raise ValueError("Debit and credit must be nonnegative")
        if row.get("period") in policy.get("locked_periods", []):
            violations.append(f"期间 {row['period']} 已锁定；该草稿不得正式入账")
    adjustment_findings = check_records(added, policy)
    violations.extend(f["explanation"] for f in adjustment_findings if f["status"] == "supported")
    before = balances(records)
    after = balances([*records, *added])
    delta = {a: formatted(money(after.get(a, "0.00")) - money(before.get(a, "0.00"))) for a in sorted(set(before) | set(after))}
    note = ("小企业准则：前期差错按未来适用法处理；本结果仅为待复核草稿。"
            if policy.get("framework") == "SCAS" else
            "企业会计准则：前期差错需判断重要性、追溯重述条件及例外；模拟不自动决定会计处理。")
    return {"before": before, "after": after, "delta": delta,
            "balanced": not any(f["rule_id"] == "VOUCHER_BALANCE" for f in adjustment_findings),
            "violations": sorted(set(violations)), "policy_note": note,
            "new_findings": check_records([*records, *added], policy), "adjustments": added}


def bank_reconciliation(records: list[dict], policy: dict) -> list[dict]:
    """Compare bank movements with ledger cash movements in one snapshot period.

    This is a separate task from invoice and procurement checking. A mismatch is
    an observed difference, not a conclusion that either source is fraudulent.
    Opening bank balances, bank fees and outstanding timing items are never
    invented to force the accounts to reconcile.
    """
    from .matching import match_transactions

    period = policy.get("period")
    scoped = [r for r in records if not period or r.get("period") == period]
    banks = [r for r in scoped if r.get("kind") == "bank"]
    cash = [r for r in scoped if r.get("kind") == "journal" and str(r.get("account", "")).startswith("1002")]
    findings = []
    if not banks or not cash:
        missing = (["本期银行流水"] if not banks else []) + (["本期银行存款科目分录"] if not cash else [])
        return [_finding("BANK_RECONCILIATION_INPUT", "资金核对资料不足", [*banks, *cash],
                         "需要同一期间的银行流水及银行存款分录；缺失的一侧不可按零处理。",
                         status="insufficient", missing=missing)]

    ledger_accounts = {str(r["account"]) for r in cash}
    unmapped = [r for r in banks if not r.get("account")]
    if unmapped and len(ledger_accounts) != 1:
        return [_finding("BANK_ACCOUNT_SCOPE", "银行账户映射不明确", unmapped,
                         "存在多个银行存款科目，未标记账户的流水不能跨账户汇总抵销。",
                         status="insufficient", missing=["银行流水对应的银行存款明细科目"])]
    # Work on copies: a sole cash account is a transparent candidate mapping.
    bank_copies = deepcopy(banks)
    for row in bank_copies:
        if not row.get("account"):
            row["account"] = next(iter(ledger_accounts))
    buckets: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: {"bank": [], "ledger": []})
    for rows, side in [(bank_copies, "bank"), (cash, "ledger")]:
        for row in rows:
            buckets[(str(row["account"]), str(row.get("currency", "CNY")))][side].append(row)
    for (account, currency), sides in sorted(buckets.items()):
        bank_rows, ledger_rows = sides["bank"], sides["ledger"]
        rows = [*bank_rows, *ledger_rows]
        bank_total = sum((_amount(r) for r in bank_rows), ZERO)
        ledger_total = sum((_net(r) for r in ledger_rows), ZERO)
        metrics = {"account": account, "currency": currency, "period": period,
                   "bank_movement": formatted(bank_total), "ledger_movement": formatted(ledger_total),
                   "difference": formatted(bank_total - ledger_total),
                   "account_mapping": "single_ledger_account_candidate" if any(r["id"] in {x["id"] for x in unmapped} for r in bank_rows) else "explicit"}
        if not bank_rows or not ledger_rows:
            findings.append(_finding("BANK_ACCOUNT_EVIDENCE", "该账户的一侧资料缺失", rows,
                                     "应提供同一银行账户的两侧资料，不能把缺失的一侧当成零发生额。",
                                     status="insufficient", metrics=metrics,
                                     missing=["该账户银行流水" if not bank_rows else "该账户银行存款分录"]))
            continue
        if bank_total != ledger_total:
            findings.append(_finding("BANK_LEDGER_DIFFERENCE", "银行与账面发生额存在差异", rows,
                                     "已导入期间的现金流入减流出不一致；需核实未达账项、手续费或遗漏，系统不自动补平。",
                                     metrics=metrics))
        try:
            # Both inputs are physical cash movements, so receivable coordinates
            # preserve signed cash-in/out on both sides, including refunds.
            matching = match_transactions(bank_rows, ledger_rows, flow="receivable")
        except ValueError as exc:
            findings.append(_finding("BANK_DIRECTION_CONFLICT", "流水方向与金额约定冲突", rows,
                                     str(exc), metrics=metrics))
            continue
        by_id = {r["id"]: r for r in rows}
        for side, label, rule in [("left", "流水", "BANK_UNMATCHED"), ("right", "账面银行存款分录", "LEDGER_UNMATCHED")]:
            ids = matching["unmatched"][side]
            if ids:
                findings.append(_finding(rule, f"存在尚未匹配的{label}", [by_id[i] for i in ids],
                                         "未匹配不等于错账；可能涉及正常时差、部分结算或缺失的业务关联。",
                                         status="insufficient", missing=["银行回单、未达账项说明或关联凭证"],
                                         metrics={**metrics, "unmatched_count": len(ids)}))
        if matching["status"] in {"ambiguous", "bounded"}:
            findings.append(_finding("BANK_MATCH_AMBIGUOUS" if matching["status"] == "ambiguous" else "BANK_MATCH_BOUNDED",
                                     "资金匹配存在多个解释" if matching["status"] == "ambiguous" else "资金匹配达到搜索上限", rows,
                                     "候选组合未自动核销；需要明确来源关联或缩小查询分组。",
                                     status="insufficient" if matching["status"] == "ambiguous" else "unsupported",
                                     missing=["精确来源关联"], metrics={**metrics, "candidate_count": len(matching["candidates"])}))
        if bank_total == ledger_total and matching["status"] == "candidates" and not any(matching["unmatched"].values()):
            findings.append(_finding("BANK_CANDIDATES_ALIGNED", "资金发生额及候选关联一致", rows,
                                     "在本期间和导入范围内找到金额一致的候选关系，仍需复核确认，未自动记账或核销。",
                                     status="normal", severity="low", metrics={**metrics, "candidate_count": len(matching["candidates"])}))
    return sorted(findings, key=lambda x: (x["rule_id"], tuple(x["record_ids"])))


def rule_catalog() -> list[dict]:
    definitions = [
        ("VOUCHER_BALANCE", "凭证借贷平衡", "journal", "确定性金额约束，不等于业务正确"),
        ("JOURNAL_SIGN", "借贷字段方向", "journal", "借贷非负；反向凭证表示冲销"),
        ("ACCOUNT_VALIDITY", "账套科目映射", "journal", "仅检测科目映射缺口；不判断业务科目选择正确性"),
        ("PERIOD_VALIDITY", "业务日期有效性", "journal", "缺失日期输出证据不足"),
        ("PERIOD_CUTOFF", "业务与记账期间差异", "journal", "跨期仅作为需核实事项"),
        ("DUPLICATE_INVOICE", "发票全额重复确认", "invoice,journal", "只覆盖明确发票关联与确认账户"),
        ("INVOICE_OVERBOOKED", "发票分拆确认超额", "invoice,journal", "复杂税额和外币计量需独立规则"),
        ("INVOICE_EVIDENCE", "一票多凭证证据缺失", "journal", "不能把分拆自动认定重复"),
        ("INVOICE_CONFLICT", "发票来源冲突", "invoice", "需补证确认正确来源"),
        ("RECEIPT_GAP", "采购收货证据缺口", "order,receipt", "正常分批也会出现缺口"),
        ("RECEIPT_EXCESS", "采购收货金额超额", "order,receipt", "订单变更和退货需明确记录"),
        ("OPENING_REQUIRED", "期初余额完整性", "opening,trial_balance", "不将缺失余额默认为零"),
        ("TRIAL_BALANCE", "期初发生期末衔接", "opening,journal,trial_balance", "仅对完整导入期间计算"),
        ("BANK_LEDGER_DIFFERENCE", "银行与账面发生额差异", "bank,journal", "独立资金核对任务；差异可能为正常未达账项"),
        ("BANK_UNMATCHED", "未匹配银行流水", "bank,journal", "方向、币种、主体、订单和日期分组；不能视为错账结论"),
        ("LEDGER_UNMATCHED", "未匹配银行存款分录", "bank,journal", "不能自动生成手续费或汇差使其平衡"),
    ]
    result = []
    for framework, source in [("CAS", CAS_SOURCE), ("SCAS", SCAS_SOURCE)]:
        for rule, title, required, limit in definitions:
            result.append({"rule_id": rule, "title": title, "framework": framework, "version": RULE_VERSION,
                           "source_sha256": _RULE_SOURCE_SHA256,
                           "effective_from": None, "effective_to": None, "required_kinds": required.split(","),
                           "validation_status": "experimental", "status": "experimental",
                           "source_url": source, "source_note": "该链接仅用于差错处理制度背景；技术完整性检查不是对应条款的完整实现。",
                           "limitations": limit, "validation_cases": "backend/tests/test_domain.py"})
    return result
