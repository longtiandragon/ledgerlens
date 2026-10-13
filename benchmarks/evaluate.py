"""Public evaluator entry point. Runtime investigators never receive this tool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.reference_cases import run_reference_cases
from benchmarks.run_rules import evaluate
from datasets.generate import write_dataset

ROOT = Path(__file__).resolve().parents[1]


def run_evaluation() -> dict:
    """Run the synthetic suite and return aggregate evidence, no hidden labels.

    Requires the full source checkout. Docker deployment should run this package
    as an isolated evaluator, not mount truth data into the investigation worker.
    """
    dataset = ROOT / ".local" / "datasets"
    if not (dataset / "manifest.json").exists():
        write_dataset(dataset)
    result = evaluate(dataset, "all")
    references = run_reference_cases()
    destination = ROOT / ".local" / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    # Full per-case outputs remain evaluator-owned artifacts. The API result
    # excludes cases/expected labels, particularly the sealed-test answers.
    (destination / "rules-all.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "reference-cases.json").write_text(json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: value for key, value in result.items() if key != "cases"}
    summary.update({"reference_cases": {k: v for k, v in references.items() if k != "results"},
                    "family_count": 48, "dataset_size": 480, "normal_cases": sum(not r["expected"] for r in result["cases"]),
                    "report_path": str(destination / "rules-all.json"), "status": "completed",
                    "limitations": ["合成拓扑/故障配置，不是真实企业验证", "统计规则标签检测，不是原因分类 Macro-F1",
                                    "单 Agent、多 Agent、消融实验尚未运行", "30 个独立参考例，不是财政部官方案例"]})
    return summary


def acceptance_failures(summary: dict) -> list[str]:
    """Existing 480/30 rule-label contract. Passing it says nothing about model quality."""
    references = summary.get("reference_cases") or {}
    checks = {"case_count": summary.get("case_count") == 480,
              "failures": summary.get("failures") == 0,
              "exact_label_match_rate": summary.get("exact_label_match_rate") == 1,
              "reference_cases": references.get("case_count") == 30 and references.get("passed") == 30,
              "model_calls": summary.get("model_calls") == 0}
    return [name for name, passed in checks.items() if not passed]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=run_evaluation.__doc__)
    parser.add_argument("--require-acceptance", action="store_true",
                        help="exit non-zero unless the existing 480/30 rule-label contract holds")
    options = parser.parse_args()
    summary = run_evaluation()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if options.require_acceptance and (failed := acceptance_failures(summary)):
        raise SystemExit("Rule benchmark acceptance failed: " + ", ".join(failed))
