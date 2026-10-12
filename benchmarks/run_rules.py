"""Evaluate rule labels. This does not estimate LLM root-cause effectiveness."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.domain import check_records  # noqa: E402 - standalone benchmark bootstrap


def evaluate(dataset: Path, split: str = "development") -> dict:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    selected = [c for c in manifest["cases"] if split == "all" or c["split"] == split]
    rows = []
    tally = defaultdict(Counter)
    started = time.perf_counter()
    for case in selected:
        case_id = case["case_id"]
        observable = json.loads((dataset / "observed" / f"{case_id}.json").read_text(encoding="utf-8"))
        truth = json.loads((dataset / "truth" / f"{case_id}.json").read_text(encoding="utf-8"))
        expected = set(truth["expected_rules"])
        error = None
        try:
            findings = check_records(observable["records"], observable["policy"])
            predicted = {f["rule_id"] for f in findings}
        except Exception as exc:
            predicted = set()
            error = f"{type(exc).__name__}: {exc}"
        for label in expected | predicted:
            tally[label]["tp" if label in expected & predicted else "fp" if label in predicted else "fn"] += 1
        rows.append({"case_id": case_id, "framework": case["framework"], "expected": sorted(expected),
                     "predicted": sorted(predicted), "exact_match": not error and expected == predicted, "error": error})
    per_rule = {}
    for label, counts in sorted(tally.items()):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        per_rule[label] = {"tp": tp, "fp": fp, "fn": fn,
                           "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}
    return {"method": "deterministic_rules", "metric_scope": "synthetic injected rule-label detection, not real accounting accuracy or LLM cause classification",
            "created_at": datetime.now(timezone.utc).isoformat(), "dataset_version": manifest["version"], "split": split,
            "case_count": len(rows), "failures": sum(r["error"] is not None for r in rows),
            "exact_label_match_rate": sum(r["exact_match"] for r in rows) / len(rows) if rows else None,
            "macro_rule_f1": sum(r["f1"] for r in per_rule.values()) / len(per_rule) if per_rule else None,
            "per_rule": per_rule, "frameworks": {framework: {"cases": sum(r["framework"] == framework for r in rows),
                                      "exact_matches": sum(r["framework"] == framework and r["exact_match"] for r in rows)} for framework in ["CAS", "SCAS"]},
            "duration_seconds": time.perf_counter() - started, "model_calls": 0,
            "multi_agent_comparison": "not_run", "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path(".local/datasets"))
    parser.add_argument("--split", choices=["development", "validation", "sealed_test", "all"], default="development")
    parser.add_argument("--report", type=Path, default=Path(".local/reports/rules-development.json"))
    args = parser.parse_args()
    result = evaluate(args.dataset, args.split)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"cases", "per_rule"}}, ensure_ascii=False))
