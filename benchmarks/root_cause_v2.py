"""Root-cause v2 evaluator and same-budget deterministic baselines (Apache-2.0).

Every method gets the same visible snapshot and at most ONE structured evidence
request (kind plus optional reference). The evaluator answers it from withheld
records, returning an explicit empty list when nothing matches. Truth files
state categories and the expected conclusion with/without decisive evidence;
they are read only after a method has produced its prediction.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

TRACK_VERSION = "root-cause-track-v2"
ROOT = Path(__file__).resolve().parents[1]
REQUEST_BUDGET = 1
KINDS = ("order", "receipt", "invoice", "bank", "journal")
# Keyword lists were written from DEVELOPMENT wording only (datasets/generate_v2.py).
KEYWORDS = {
    "order_change": ("取消", "不再", "调整为已到货"),
    "invoice_voided": ("作废", "重开", "失效", "重新开具"),
    "price_adjustment": ("折让", "红字", "下调", "优惠", "负数发票"),
}


def _ids(record: dict) -> set:
    return {record.get(key) for key in ("order_id", "invoice_id", "reference", "source_id")} - {None, ""}


def release(withheld: list[dict], request: dict | None, visible: list[dict] = ()) -> list[dict]:
    """A reference may name the withheld record itself, or any VISIBLE document of
    the same purchase chain (order): asking "the invoice for INV-001" is natural."""
    if not request or request.get("kind") not in KINDS:
        return []
    reference = request.get("reference")
    chains = {r.get("order_id") for r in visible if reference in _ids(r)} - {None, ""} if reference else set()
    return [r for r in withheld if r.get("kind") == request["kind"] and (
        not reference or reference in _ids(r) or r.get("order_id") in chains)]


def expected(truth: dict, released: list[dict]) -> dict:
    decisive = {r.get("kind") for r in released} & set(truth["decisive_kinds"])
    return {"categories": sorted(truth["categories"]),
            "conclusion": truth["conclusion_with_evidence"] if decisive else truth["conclusion_without_evidence"],
            "decisive_obtained": bool(decisive)}


def _rules(records: list[dict], policy: dict) -> set[str]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.domain import check_records
    return {f["rule_id"] for f in check_records(records, policy) if f["status"] in {"supported", "insufficient"}}


def _interpret(signal: set[str], released: list[dict], records: list[dict], period: str) -> dict:
    """Deterministic reading of a response; shared by R1 and R2."""
    if "DUPLICATE_INVOICE" in signal:
        known = {r.get("invoice_id") for r in records if r.get("kind") == "invoice"}
        if any(r["kind"] == "invoice" and r.get("invoice_id") not in known for r in released):
            return {"categories": ["reference_mismatch"], "conclusion": "supported"}
        return {"categories": ["duplicate_posting"], "conclusion": "supported"}
    if "RECEIPT_GAP" in signal:
        if any(r["kind"] == "order" and r["amount"].startswith("-") for r in released):
            return {"categories": ["order_change"], "conclusion": "normal"}
        receipts = [r for r in released if r["kind"] == "receipt"]
        if receipts and all(str(r.get("business_date", ""))[:7] <= period for r in receipts):
            return {"categories": ["late_document"], "conclusion": "normal"}
        if receipts:
            return {"categories": ["goods_in_transit"], "conclusion": "supported"}
        return {"categories": [], "conclusion": "insufficient"}
    return {"categories": ["normal_business"], "conclusion": "normal"}


def rule_only(records, policy, withheld):
    return {**_interpret(_rules(records, policy), [], records, policy["period"]), "request": None, "released": []}


def rule_fixed_request(records, policy, withheld):
    signal = _rules(records, policy)
    kind = "invoice" if "DUPLICATE_INVOICE" in signal else "receipt" if "RECEIPT_GAP" in signal else None
    request = {"kind": kind} if kind else None
    released = release(withheld, request, records)
    return {**_interpret(signal, released, records, policy["period"]), "request": request, "released": released}


def keyword_request(records, policy, withheld):
    signal = _rules(records, policy)
    notes = " ".join(str(r.get("description", "")) for r in records)
    hit = {name for name, words in KEYWORDS.items() if any(word in notes for word in words)}
    if "DUPLICATE_INVOICE" in signal:
        request = {"kind": "invoice"}
    elif "RECEIPT_GAP" in signal:
        request = {"kind": "order" if "order_change" in hit else "receipt"}
    elif hit & {"invoice_voided", "price_adjustment"}:
        request = {"kind": "invoice"}
    else:
        request = None
    released = release(withheld, request, records)
    result = _interpret(signal, released, records, policy["period"])
    if not signal and "invoice_voided" in hit:
        result = {"categories": ["invoice_voided"], "conclusion": "supported"}
    elif not signal and "price_adjustment" in hit:
        result = {"categories": ["price_adjustment"], "conclusion": "supported"}
    return {**result, "request": request, "released": released}


METHODS = {"rule_only": rule_only, "rule_fixed_request": rule_fixed_request, "keyword_request": keyword_request}


def score(rows: list[dict], categories: dict) -> dict:
    """Rows: {family_id, truth, prediction, released}; failures stay in the denominator."""
    per = {name: {"tp": 0, "fp": 0, "fn": 0} for name in categories}
    correct = obtained = needing = requests = 0
    for row in rows:
        want = expected(row["truth"], row["released"])
        failed = bool(row["prediction"].get("error"))
        got = set() if failed else set(row["prediction"].get("categories") or [])
        for name in categories:
            in_want, in_got = name in want["categories"], name in got
            per[name]["tp"] += in_want and in_got
            per[name]["fp"] += in_got and not in_want
            per[name]["fn"] += in_want and not in_got
        correct += (not failed) and row["prediction"].get("conclusion") == want["conclusion"]
        if row["truth"]["decisive_kinds"]:
            needing += 1
            obtained += want["decisive_obtained"]
        requests += bool(row["prediction"].get("request"))
    scored = [name for name, c in per.items() if c["tp"] + c["fn"] > 0]
    f1 = {name: (2 * per[name]["tp"] / (2 * per[name]["tp"] + per[name]["fp"] + per[name]["fn"])) for name in scored}
    return {"samples": len(rows), "failures": sum(bool(r["prediction"].get("error")) for r in rows),
            "category_macro_f1": round(sum(f1.values()) / len(f1), 4) if f1 else None,
            "per_category_f1": {k: round(v, 4) for k, v in f1.items()},
            "false_positive_categories": {k: c["fp"] for k, c in per.items() if c["fp"]},
            "conclusion_accuracy": round(correct / len(rows), 4) if rows else None,
            "decisive_evidence_obtained": f"{obtained}/{needing}", "requests_made": requests,
            "family_bootstrap_conclusion": _bootstrap(rows)}


def _bootstrap(rows, iterations=2000, seed=1729):
    families = defaultdict(list)
    for row in rows:
        want = expected(row["truth"], row["released"])["conclusion"]
        families[row["family_id"]].append((not row["prediction"].get("error"))
                                          and row["prediction"].get("conclusion") == want)
    if len(families) < 2:
        return None
    rng, keys, values = random.Random(seed), sorted(families), []
    for _ in range(iterations):
        sample = [v for key in (rng.choice(keys) for _ in keys) for v in families[key]]
        values.append(sum(sample) / len(sample))
    values.sort()
    return {"families": len(keys), "lower": round(values[int(.025 * iterations)], 4),
            "upper": round(values[int(.975 * iterations) - 1], 4)}


def load_case(dataset: Path, case_id: str) -> tuple[dict, list[dict]]:
    observed = json.loads((dataset / "observed" / f"{case_id}.json").read_text(encoding="utf-8"))
    withheld = json.loads((dataset / "on_demand" / f"{case_id}.json").read_text(encoding="utf-8"))
    return observed, withheld


def run_baselines(dataset: Path, split: str) -> dict:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    cases = [c for c in manifest["cases"] if c["split"] == split]
    report = {"schema_version": TRACK_VERSION, "dataset_version": manifest["version"], "split": split,
              "created_at": datetime.now(UTC).isoformat(), "model_requests": 0, "request_budget": REQUEST_BUDGET,
              "methods": {}}
    for name, method in METHODS.items():
        rows = []
        for case in cases:
            observed, withheld = load_case(dataset, case["case_id"])
            prediction = method(observed["records"], observed["policy"], withheld)
            truth = json.loads((dataset / "truth" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
            rows.append({"case_id": case["case_id"], "family_id": case["family_id"], "truth": truth,
                         "prediction": prediction, "released": prediction["released"]})
        groups = defaultdict(list)
        for row in rows:
            groups[row["truth"]["signal_group"]].append(row)
        report["methods"][name] = {"overall": score(rows, manifest["categories"]),
                                   "by_signal_group": {g: score(r, manifest["categories"]) for g, r in sorted(groups.items())}}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(".local/datasets-v2"))
    parser.add_argument("--split", choices=["development", "validation", "sealed_test"], default="development")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_baselines(args.dataset, args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, data in report["methods"].items():
        o = data["overall"]
        print(name, "F1", o["category_macro_f1"], "concl", o["conclusion_accuracy"],
              "evidence", o["decisive_evidence_obtained"], "fp", o["false_positive_categories"])


if __name__ == "__main__":
    main()
