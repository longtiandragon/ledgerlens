"""Public summary of root-cause track runs from local reports; no model calls.

Every run keeps its failures, cost and source report hash. Invalidated runs are
listed with the reason instead of being dropped.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / ".local/reports"
OUTPUT = ROOT / "docs/root-cause-results.json"
# id, label, report, split, role in the study, validity note
RUNS = [
    ("v2-baselines-development", "v2 确定性基线 · 开发集", "root-cause-v2-baselines-development.json", "development",
     "baseline", "有效"),
    ("v2-baselines-validation", "v2 确定性基线 · 验证集", "root-cause-v2-baselines-validation.json", "validation",
     "baseline", "有效"),
    ("v2-smoke-validation-v24", "单Agent冒烟 · 验证集", "root-cause-v2-smoke-v24.json", "validation", "smoke",
     "作废：补证释放规则缺陷，按相关单据编号提出的请求未能释放资料"),
    ("v2-smoke-development-v25", "单Agent冒烟 · 开发集", "root-cause-v2-dev-smoke-v25.json", "development", "smoke",
     "有效；仅用于开发集调整"),
    ("v2-smoke-development-runner2", "单Agent冒烟（第二轮显示补证）· 开发集", "root-cause-v2-dev-smoke-runner2.json",
     "development", "smoke", "有效；仅用于开发集调整"),
    ("v2-sealed-final", "最终对比 · 封存测试集", "root-cause-v2-sealed-final.json", "sealed_test", "final",
     "有效；冻结配置；重复2次（规划要求3次）"),
]


def _metrics(report: dict) -> dict:
    if "metrics" in report:  # model runner report
        source = report["metrics"]
    else:  # deterministic baseline report
        source = {name: data["overall"] for name, data in report["methods"].items()}
    return {name: {key: value[key] for key in ("samples", "failures", "category_macro_f1", "conclusion_accuracy",
                                               "decisive_evidence_obtained")} for name, value in source.items()}


def main() -> None:
    runs = []
    for identity, label, filename, split, role, validity in RUNS:
        path = REPORTS / filename
        if not path.exists():
            runs.append({"id": identity, "label": label, "status": "missing_local_report", "report": filename})
            continue
        data = path.read_bytes()
        report = json.loads(data)
        guard = REPORTS / (path.stem + "-guard.json")
        cost = json.loads(guard.read_text(encoding="utf-8")).get("final_offpeak_cny") if guard.exists() else None
        runs.append({"id": identity, "label": label, "split": split, "role": role, "validity": validity,
                     "report": filename, "report_sha256": hashlib.sha256(data).hexdigest(),
                     "prompt_version": report.get("prompt_version"), "runner_version": report.get("runner_version"),
                     "repeat": report.get("repeat", 1), "paid_requests": report.get("paid_requests", 0),
                     "offpeak_cost_cny_estimate": cost, "status": report.get("status", "completed"),
                     "methods": _metrics(report)})
    summary = {"schema_version": "root-cause-results-v1", "created_at": datetime.now(UTC).isoformat(),
               "track_version": "root-cause-track-v2", "dataset_version": "root-cause-v2.0.0", "runs": runs,
               "conclusion": ("在封存测试集上，单Agent（原因F1 0.438、结论38.9%、6/18输出失败）低于同预算确定性基线"
                              "（关键词补证0.688／77.8%）；主动补证与模型优于确定性方法的主张未获支持。"),
               "limitations": ["合成数据，为检验补证与文字理解能力而设计，不能外推为真实账套效果。",
                               "封存集仅9个场景族、2次重复，低于规划的3次。",
                               "费用为按官方低谷价的列表价估算，不是账单。"]}
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"runs": len(runs), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
