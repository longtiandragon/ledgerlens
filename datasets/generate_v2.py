"""Root-cause dataset v2: same rule signal, different causes (Apache-2.0).

Design (docs/ROOT_CAUSE_TRACK.md): every cause has three topologies, one per
split, and every split uses its own wording templates that are never shared.
Truth states the categories and the expected conclusion with and without the
decisive withheld evidence. No model generated any text; templates are fixed
before any model run. Facts never appear in record IDs or source IDs.

Run: python -m datasets.generate_v2 --output .local/datasets-v2
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from datasets.generate import ACCOUNTS, _journal, _record

VERSION = "root-cause-v2.0.0"
SPLITS = ("development", "validation", "sealed_test")
VARIANTS = 10
LATE_OBSERVED = "2026-10-03T09:00:00+00:00"
SUPPLIERS = ["合成供应商甲", "合成供应商乙", "合成供应商丙", "合成供应商丁", "合成供应商戊"]
# Receipt splits (percent of order) per topology; index = split.
TOPOLOGIES = ([100], [60, 40], [20, 30, 50])

# Wording per meaning and split. Development templates may contain obvious
# keywords; validation/sealed wording is new and partly avoids them.
TEXT = {
    "order_reduced": {
        "development": ["供应商来函：订单剩余数量取消，不再发货。", "供应商通知：经协商，订单数量调整为已到货部分。",
                        "备注：采购部确认后续批次不再执行。", "供应商备注：余下部分双方同意不再供应。"],
        "validation": ["供应商说明：剩下的那批货我们这边就不发了，按已送到的结算。",
                       "采购员留言：和对方谈妥，尾单作罢。", "对方来电记录：其余数量撤单。",
                       "备注：按实际到货量结算，未到部分终止。"],
        "sealed_test": ["供应商邮件：另外那部分产能排不上，经贵司同意就此打住。", "业务备注：后半单经双方确认不再履行。",
                        "来函摘要：未交付部分予以撤销，按已交付金额开票。", "采购备注：剩余货物供应商无法提供，双方解除该部分。"],
    },
    "invoice_voided": {
        "development": ["供应商来函：本张发票开具有误已作废，将重新开具。", "供应商通知：该发票作废，请以重开发票为准。",
                        "备注：原票信息错误，供应商已重开新票。", "供应商备注：此票已失效，新票随后寄出。"],
        "validation": ["供应商说明：这张票抬头写错了，已经收回重开，旧票请勿入账。",
                       "对方财务留言：上张票号码有误，已在系统冲掉并另开一张。", "来电记录：原票不能用了，新票已经寄出。",
                       "备注：供应商撤回原发票，替换票据后补。"],
        "sealed_test": ["供应商邮件：之前那张发票税号有问题，我们已在税务系统里红冲并换开。",
                        "业务备注：原始票据对方已注销，以替换票为准。", "来函摘要：该票据作废处理，替代票另行送达。",
                        "财务留言：供应商把这张票撤了，重新出了一张。"],
    },
    "price_adjustment": {
        "development": ["供应商来函：同意给予折让，将开具红字发票。", "供应商通知：价格下调，差额以红字发票冲减。",
                        "备注：质量瑕疵，供应商同意折让并开红字票。", "供应商备注：给予价格优惠，后续开负数发票。"],
        "validation": ["供应商说明：这批货有点小问题，货款我们让一部分，会补一张负数票。",
                       "采购员留言：谈下来便宜一点，对方出冲减票据。", "对方来电记录：同意少收部分货款，另开冲减凭证。",
                       "备注：按协议返还部分价款，对方开具冲销发票。"],
        "sealed_test": ["供应商邮件：考虑到交期延误，我们减收一部分款项，并开具相应的冲减票。",
                        "业务备注：双方确认降价，差额由对方出具负数票据冲回。", "来函摘要：给予价款减让，开具红字凭证。",
                        "财务留言：对方同意打个折，会寄一张金额为负的票过来。"],
    },
    "benign": {
        "development": ["供应商备注：请按合同约定付款。", "备注：感谢合作。", "供应商来函：发票已寄出，请注意查收。",
                        "备注：货物已按时送达。"],
        "validation": ["供应商说明：期待下次合作。", "采购员留言：本次交付顺利。", "对方来电记录：确认已收到款项通知。",
                       "备注：包装完好。"],
        "sealed_test": ["供应商邮件：节日快乐，祝合作愉快。", "业务备注：本单验收无异常。", "来函摘要：开户行信息未变。",
                        "财务留言：对账单已发送。"],
    },
}

# cause -> (signal group, categories, conclusion without / with decisive evidence, decisive kinds)
CAUSES = {
    "dup_true": ("duplicate_signal", ["duplicate_posting"], "supported", "supported", []),
    "dup_second_delivery": ("duplicate_signal", ["reference_mismatch"], "supported", "supported", ["invoice"]),
    "gap_late_document": ("gap_signal", ["late_document"], "insufficient", "normal", ["receipt"]),
    "gap_in_transit": ("gap_signal", ["goods_in_transit"], "insufficient", "supported", ["receipt"]),
    "gap_order_reduced": ("gap_signal", ["order_change"], "insufficient", "normal", ["order"]),
    "gap_undeterminable": ("gap_signal", [], "insufficient", "insufficient", []),
    "normal_benign_text": ("no_signal", ["normal_business"], "normal", "normal", []),
    "invoice_voided_text": ("no_signal", ["invoice_voided"], "supported", "supported", ["invoice"]),
    "price_adjustment_text": ("no_signal", ["price_adjustment"], "supported", "supported", ["invoice"]),
}
CATEGORIES = {
    "normal_business": "正常业务安排", "duplicate_posting": "同一发票重复入账",
    "reference_mismatch": "另一笔交货错挂到同一发票编号", "late_document": "期内已收货、单据期后才取得",
    "goods_in_transit": "期末货物尚未到达（在途）", "order_change": "订单数量已变更或取消",
    "invoice_voided": "发票已作废并重开", "price_adjustment": "已约定折让或降价、需冲减",
}


def _purchase(seed: str, unit: int, parts: list[int], supplier: str, *, order_total: int | None = None,
              invoice_total: int | None = None) -> list[dict]:
    """A complete, consistent purchase: order, receipts, one invoice, recognition and payment."""
    total = unit * 100
    common = {"counterparty": supplier}
    rows = [_record(seed, "order", "PO-001", order_total if order_total is not None else total, **common)]
    rows += [_record(seed, "receipt", f"RCV-{i + 1:03d}", unit * part, **common) for i, part in enumerate(parts)]
    amount = invoice_total if invoice_total is not None else total
    rows.append(_record(seed, "invoice", "INV-001", amount, invoice_id="INV-001", **common))
    rows += _journal(seed, "JV-001", amount, counterparty=supplier)
    rows.append(_record(seed, "bank", "PAY-001", -amount, direction="outgoing", invoice_id="INV-001", **common))
    rows += _journal(seed, "PAY-JV-001", amount, debit_account="2202", credit_account="1002", counterparty=supplier)
    return rows


def _note(rows: list[dict], kind: str, text: str) -> None:
    target = next(r for r in rows if r["kind"] == kind)
    target["description"] = f"{target['description']}；{text}"


def build_case(cause: str, split: str, variant: int, *, version: str = VERSION,
               namespace: str = 'rc2', text: dict | None = None,
               topologies: tuple | None = None, unit_offset: int = 0) -> tuple[dict, list[dict], dict]:
    """Shared accounting construction; default v2 bytes and truth remain unchanged."""
    group, categories, without, with_evidence, decisive = CAUSES[cause]
    topology = (topologies or TOPOLOGIES)[SPLITS.index(split)]
    framework = "CAS" if variant % 2 == 0 else "SCAS"
    seed = f"{namespace}-{cause}-{split}-{variant:02d}"
    unit = 100_000 + unit_offset + variant * 137 + SPLITS.index(split) * 11
    supplier = SUPPLIERS[variant % len(SUPPLIERS)]
    templates = TEXT if text is None else text
    pick = lambda meaning: templates[meaning][split][variant % 4]  # noqa: E731
    withheld: list[dict] = []
    late = {"observed_at": LATE_OBSERVED, "counterparty": supplier}

    if cause == "dup_true":
        rows = _purchase(seed, unit, topology, supplier)
        rows += _journal(seed, "JV-002", unit * 100, counterparty=supplier)
    elif cause == "dup_second_delivery":
        # Two deliveries; the second delivery's voucher carries the first invoice number.
        # Both deliveries' receipts are visible, so no receipt gap hints at the cause.
        rows = _purchase(seed, unit, topology, supplier, order_total=unit * 200)
        rows.append(_record(seed, "receipt", f"RCV-{len(topology) + 1:03d}", unit * 100, counterparty=supplier))
        rows += _journal(seed, "JV-002", unit * 100, counterparty=supplier)
        withheld = [_record(seed, "invoice", "INV-002", unit * 100, invoice_id="INV-002", **late)]
    elif cause in {"gap_late_document", "gap_in_transit", "gap_order_reduced", "gap_undeterminable"}:
        rows = _purchase(seed, unit, topology, supplier)
        last = next(r for r in reversed(rows) if r["kind"] == "receipt")
        rows.remove(last)
        if cause == "gap_late_document":
            withheld = [{**last, **late}]
            if variant % 3 == 0:
                _note(rows, "invoice", pick("benign"))  # a harmless distractor
        elif cause == "gap_in_transit":
            withheld = [{**last, **late, "business_date": "2026-10-02", "period": "2026-10"}]
        elif cause == "gap_order_reduced":
            withheld = [_record(seed, "order", "PO-001-CHG", -int(last["amount"].replace(".", "")),
                                description="订单变更：减少未交付部分", **late)]
            if variant % 10 < 7:
                _note(rows, "order", pick("order_reduced"))
    elif cause == "normal_benign_text":
        rows = _purchase(seed, unit, topology, supplier)
        _note(rows, "invoice", pick("benign"))
    elif cause == "invoice_voided_text":
        rows = _purchase(seed, unit, topology, supplier)
        _note(rows, "invoice", pick("invoice_voided"))
        withheld = [_record(seed, "invoice", "INV-001-R", unit * 100, invoice_id="INV-001-R", **late)]
    elif cause == "price_adjustment_text":
        rows = _purchase(seed, unit, topology, supplier)
        _note(rows, "invoice", pick("price_adjustment"))
        withheld = [_record(seed, "invoice", "INV-001-N", -unit * 3, invoice_id="INV-001-N", **late)]
    else:
        raise ValueError(cause)

    # Opaque identifiers: the cause appears only in evaluator-owned truth.
    family = namespace + "-f-" + hashlib.sha256(f"{version}:{cause}:{split}".encode()).hexdigest()[:10]
    case_id = namespace + "-c-" + hashlib.sha256(f"{version}:{cause}:{split}:{variant}".encode()).hexdigest()[:12]
    observed = {"case_id": case_id, "family_id": family, "split": split, "synthetic": True,
                "policy": {"framework": framework, "version": version, "period": "2026-09", "currency": "CNY",
                           "accounts": ACCOUNTS[:-1], "locked_periods": []},
                "records": rows}
    truth = {"case_id": case_id, "family_id": family, "framework": framework, "cause": cause,
             "signal_group": group, "categories": categories, "conclusion_without_evidence": without,
             "conclusion_with_evidence": with_evidence, "decisive_kinds": decisive,
             "visible_note_present": any("；" in r.get("description", "") for r in rows)}
    return observed, withheld, truth


def write_dataset(destination: Path) -> dict:
    for folder in ("observed", "on_demand", "truth"):
        (destination / folder).mkdir(parents=True, exist_ok=True)
    cases = []
    for cause in CAUSES:
        for split in SPLITS:
            for variant in range(VARIANTS):
                observed, withheld, truth = build_case(cause, split, variant)
                payload = json.dumps(observed, ensure_ascii=False, indent=2).encode("utf-8")
                case_id = observed["case_id"]
                (destination / "observed" / f"{case_id}.json").write_bytes(payload)
                (destination / "on_demand" / f"{case_id}.json").write_text(
                    json.dumps(withheld, ensure_ascii=False, indent=2), encoding="utf-8")
                (destination / "truth" / f"{case_id}.json").write_text(
                    json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
                cases.append({"case_id": case_id, "family_id": observed["family_id"], "split": split,
                              "framework": observed["policy"]["framework"], "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {"version": VERSION, "synthetic": True, "case_count": len(cases), "categories": CATEGORIES,
                "evidence_budget": {"requests_per_case": 1, "request_fields": ["kind", "reference"]},
                "truth_access": "Evaluator only.", "cases": cases}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"case_count": len(cases), "destination": str(destination.resolve())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".local/datasets-v2"))
    print(json.dumps(write_dataset(parser.parse_args().output), ensure_ascii=False))
