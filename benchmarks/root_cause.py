"""Root-cause investigation track with evaluator-owned evidence release (Apache-2.0).

The rule-label track (run_agents.py) scores labels the deterministic engine
matches by construction, so it cannot show what investigation adds. This track
scores the plan's question: given the rule results, does a method reach the
right cause and conclusion, and does requesting withheld evidence help?

Truth comes only from the generator's evaluator files (fault_mechanism and
expected_rules); no production function produces it. Evidence is released only
by this evaluator, only for structured requests matching withheld records.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

TRACK_VERSION = "root-cause-track-v1"
CONCLUSIONS = ("supported", "normal", "insufficient", "unsupported")

# Visible taxonomy: methods choose from these; descriptions carry no case data.
CATEGORIES = {
    "normal_business": "正常业务安排（分批、合并付款、冲销后重记）",
    "duplicate_posting": "同一发票重复入账",
    "late_document": "业务发生在期内、单据期后才取得",
    "posting_omission": "分录遗漏或借贷不平",
    "account_mapping": "科目映射错误",
    "period_misstatement": "业务日期与记账期间不一致",
    "source_conflict": "同一单据存在相互冲突的来源",
    "receipt_excess": "收货金额超过订单",
    "booking_amount": "确认金额与发票不一致（不足或超额）",
    "invoice_missing": "已记账但缺少对应发票",
    "balance_inconsistency": "期初、发生额与试算平衡不衔接",
}

# Evaluator-only mapping from the generator's fault mechanism. Each entry is a
# documented judgment about what the synthetic fault represents.
FAULT_CAUSES = {
    "normal": {"normal_business"}, "reversal": {"normal_business"},
    "duplicate": {"duplicate_posting"}, "duplicate_last": {"duplicate_posting"},
    "reversal_duplicate": {"duplicate_posting"},
    "late_receipt": {"late_document"}, "late_two": {"late_document"}, "missing_receipt": {"late_document"},
    "duplicate_late": {"duplicate_posting", "late_document"},
    "unbalanced_late": {"posting_omission", "late_document"},
    "conflict_late": {"source_conflict", "late_document"},
    "unbalanced": {"posting_omission"}, "account": {"account_mapping"},
    "duplicate_account": {"duplicate_posting", "account_mapping"},
    "period": {"period_misstatement"}, "account_period": {"account_mapping", "period_misstatement"},
    "invoice_conflict": {"source_conflict"}, "receipt_excess": {"receipt_excess"},
    "partial_booking": {"booking_amount"}, "overbooking": {"booking_amount"},
    "missing_invoice_split": {"invoice_missing"},
    "trial": {"balance_inconsistency"}, "missing_opening": {"balance_inconsistency"},
    "trial_account": {"balance_inconsistency", "account_mapping"},
}
# The generator gives this family identical visible data for two hidden causes;
# its released evidence also matches the late-receipt families. Reported
# separately and excluded from the main metrics (see docs/ROOT_CAUSE_TRACK.md).
INDETERMINATE_FAULTS = {"indeterminate"}
# Labels whose condition stays open without further evidence (generator policy).
OPEN_LABELS = {"INVOICE_EVIDENCE", "INVOICE_CONFLICT", "PERIOD_CUTOFF", "OPENING_REQUIRED"}
RELEASE_KINDS = ("order", "receipt", "invoice", "bank", "journal", "opening", "trial_balance")


def expected(truth: dict, *, released: bool) -> dict:
    """Expected outcome for the evidence state the method actually reached."""
    fault = truth["fault_mechanism"]
    if fault in INDETERMINATE_FAULTS:
        return {"categories": None, "conclusion": "insufficient", "main_metric": False}
    if fault not in FAULT_CAUSES:
        raise ValueError(f"Fault mechanism without a documented cause mapping: {fault}")
    labels = set(truth["expected_rules"])
    if "RECEIPT_GAP" in labels and released:
        labels.discard("RECEIPT_GAP")  # withheld receipts complete the gap once obtained
    if labels & OPEN_LABELS or "RECEIPT_GAP" in labels:
        conclusion = "insufficient"
    elif FAULT_CAUSES[fault] <= {"normal_business", "late_document"}:
        conclusion = "normal"  # a late document, once obtained, is a normal difference
    else:
        conclusion = "supported"
    return {"categories": sorted(FAULT_CAUSES[fault]), "conclusion": conclusion, "main_metric": True}


def release(on_demand: list[dict], requests: list[dict]) -> list[dict]:
    """Release withheld records matching a structured kind (and optional reference)."""
    released = []
    for record in on_demand:
        for request in requests:
            if request.get("kind") != record.get("kind"):
                continue
            reference = request.get("reference")
            if reference and reference not in {record.get(k) for k in ("order_id", "invoice_id", "reference")}:
                continue
            released.append(record)
            break
    return released


# --- Baselines (methods, not truth). They may use production rules. ---------
LABEL_TO_CATEGORY = {
    "DUPLICATE_INVOICE": "duplicate_posting", "VOUCHER_BALANCE": "posting_omission",
    "JOURNAL_SIGN": "posting_omission", "ACCOUNT_VALIDITY": "account_mapping",
    "PERIOD_VALIDITY": "period_misstatement", "PERIOD_CUTOFF": "period_misstatement",
    "INVOICE_CONFLICT": "source_conflict", "RECEIPT_EXCESS": "receipt_excess",
    "INVOICE_OVERBOOKED": "booking_amount", "INVOICE_EVIDENCE": "invoice_missing",
    "TRIAL_BALANCE": "balance_inconsistency", "OPENING_REQUIRED": "balance_inconsistency",
}


def _rules_view(records: list[dict], policy: dict) -> tuple[list[dict], str]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.domain import check_records
    findings = check_records(records, policy)
    status = ("insufficient" if any(f["status"] == "insufficient" for f in findings)
              else "supported" if any(f["status"] == "supported" for f in findings) else "normal")
    return findings, status


def rule_only(records: list[dict], policy: dict) -> dict:
    findings, status = _rules_view(records, policy)
    labels = sorted({f["rule_id"] for f in findings if f["status"] in {"supported", "insufficient"}})
    categories = sorted({LABEL_TO_CATEGORY[label] for label in labels if label in LABEL_TO_CATEGORY})
    return {"categories": categories or (["normal_business"] if status == "normal" else []),
            "conclusion": status, "requests": [], "rule_labels": labels}


def rule_fixed_request(records: list[dict], policy: dict, on_demand: list[dict]) -> dict:
    """Fixed protocol: a receipt gap always triggers a receipt request."""
    first = rule_only(records, policy)
    if "RECEIPT_GAP" not in first["rule_labels"]:
        return {**first, "released": 0}
    requests = [{"kind": "receipt", "reference": None}]
    released = release(on_demand, requests)
    if not released:
        return {**first, "requests": requests, "released": 0}
    after = rule_only([*records, *released], policy)
    categories = set(after["categories"]) - {"normal_business"}
    if "RECEIPT_GAP" not in after["rule_labels"]:
        categories.add("late_document")
    return {"categories": sorted(categories), "conclusion": after["conclusion"], "requests": requests,
            "released": len(released), "rule_labels": after["rule_labels"]}


# --- Scoring -----------------------------------------------------------------
def score(rows: list[dict]) -> dict:
    """Rows: {family_id, truth, prediction, released}. Failures stay in the denominator."""
    main = [row for row in rows if expected(row["truth"], released=row["released"])["main_metric"]]
    per = {name: {"tp": 0, "fp": 0, "fn": 0} for name in CATEGORIES}
    conclusion_ok = 0
    for row in main:
        want = expected(row["truth"], released=row["released"])
        got = set(row["prediction"].get("categories") or []) if not row["prediction"].get("error") else set()
        for name in CATEGORIES:
            in_want, in_got = name in want["categories"], name in got
            per[name]["tp"] += in_want and in_got
            per[name]["fp"] += in_got and not in_want
            per[name]["fn"] += in_want and not in_got
        conclusion_ok += (not row["prediction"].get("error")) and row["prediction"].get("conclusion") == want["conclusion"]
    supported = [name for name, c in per.items() if c["tp"] + c["fn"] > 0]
    f1 = {}
    for name in supported:
        c = per[name]
        f1[name] = 2 * c["tp"] / (2 * c["tp"] + c["fp"] + c["fn"]) if c["tp"] + c["fp"] + c["fn"] else 0.0
    release_rows = [row for row in main if "late_document" in expected(row["truth"], released=False)["categories"]]
    other = [row for row in rows if not expected(row["truth"], released=row["released"])["main_metric"]]
    return {
        "samples": len(rows), "main_metric_samples": len(main),
        "failures": sum(bool(row["prediction"].get("error")) for row in rows),
        "category_macro_f1": sum(f1.values()) / len(f1) if f1 else None, "per_category_f1": f1,
        "per_category_counts": {name: per[name] for name in supported},
        "conclusion_accuracy": conclusion_ok / len(main) if main else None,
        "withheld_evidence_cases": len(release_rows),
        "withheld_evidence_obtained": sum(bool(row["released"]) for row in release_rows),
        "requests_without_withheld_evidence": sum(bool(row["prediction"].get("requests"))
                                                  for row in main if row not in release_rows),
        "indeterminate_family": {"samples": len(other), "conclusion_insufficient": sum(
            row["prediction"].get("conclusion") == "insufficient" for row in other)},
        "family_bootstrap_conclusion_accuracy": _bootstrap(main),
    }


def _bootstrap(rows: list[dict], iterations: int = 2000, seed: int = 1729) -> dict | None:
    families = defaultdict(list)
    for row in rows:
        want = expected(row["truth"], released=row["released"])["conclusion"]
        families[row["family_id"]].append(
            (not row["prediction"].get("error")) and row["prediction"].get("conclusion") == want)
    if len(families) < 2:
        return None
    rng, keys, values = random.Random(seed), sorted(families), []
    for _ in range(iterations):
        sample = [item for key in (rng.choice(keys) for _ in keys) for item in families[key]]
        values.append(sum(sample) / len(sample))
    values.sort()
    return {"families": len(keys), "lower": values[int(.025 * iterations)], "upper": values[int(.975 * iterations) - 1]}


def _case_files(dataset: Path, split: str) -> list[dict]:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    return [case for case in manifest["cases"] if split == "all" or case["split"] == split]


def run_baselines(dataset: Path, split: str) -> dict:
    results = {}
    for method in ("rule_only", "rule_fixed_request"):
        rows = []
        for case in _case_files(dataset, split):
            observed = json.loads((dataset / "observed" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
            on_demand = json.loads((dataset / "on_demand" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
            prediction = (rule_only(observed["records"], observed["policy"]) if method == "rule_only" else
                          rule_fixed_request(observed["records"], observed["policy"], on_demand))
            # Truth is read only after the method has produced its prediction.
            truth = json.loads((dataset / "truth" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
            rows.append({"case_id": case["case_id"], "family_id": truth["family_id"], "truth": truth,
                         "prediction": prediction, "released": bool(prediction.get("released"))})
        results[method] = {"metrics": score(rows), "rows": [
            {"case_id": row["case_id"], "family_id": row["family_id"], "released": row["released"],
             "prediction": row["prediction"]} for row in rows]}
    return {"schema_version": TRACK_VERSION, "created_at": datetime.now(UTC).isoformat(), "split": split,
            "model_requests": 0, "methods": results,
            "scope": "synthetic root-cause categories and conclusions; not real-company accuracy"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(".local/datasets"))
    parser.add_argument("--split", choices=["development", "validation", "sealed_test", "all"], default="development")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_baselines(args.dataset, args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({method: {k: v for k, v in data["metrics"].items() if k not in {"per_category_counts"}}
                      for method, data in report["methods"].items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
