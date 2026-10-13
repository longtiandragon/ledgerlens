"""Public summary of root-cause track runs from local reports; no model calls.

Every run keeps its failures, cost and source report hash. Invalidated or
interrupted runs are listed with the reason instead of being dropped.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.costs import estimate_batch, load_prices  # noqa: E402

REPORTS = ROOT / ".local/reports"
OUTPUT = ROOT / "docs/root-cause-results.json"
PRICES = ROOT / "benchmarks/deepseek-prices-2026-10-08.json"
INTERRUPTION = "中断：主机内存不足且会话空闲时被Claude Code终止，守护进程同被终止，未重启"
# id, label, report, split, role in the study, validity note. Newest first: the
# evaluation centre shows the first verified final run as the current result.
V3_RUNS = [
    ("v3-sealed-single", "v3最终对比 · 封存测试集（单Agent与基线）", "root-cause-v3-sealed-single.json", "sealed_test",
     "final", "有效；冻结配置，9个未用封存案例×3次重复；按预注册方法分析，见root-cause-v3-sealed-partial-analysis.json"),
    ("v3-sealed-active-interrupted", "v3主动调查 · 封存测试集（中断）", "root-cause-v3-sealed-active.json",
     "sealed_test", "final", INTERRUPTION + "；计划18个样本完成8个（第1次重复的8案），仅作描述，不作预注册对比"),
    ("v3-sealed-multi-interrupted", "v3固定多Agent · 封存测试集（中断）", "root-cause-v3-sealed-multi.json",
     "sealed_test", "final", INTERRUPTION + "；计划9个样本完成6个，仅作描述，不作预注册对比"),
    ("v3-smoke-development-v27", "v27三模式冒烟 · 开发集（中断）", "root-cause-v3-smoke-v27.json", "development", "smoke",
     "中断：主机内存不足被终止；仅4个三模式共同案例计分；13个模型样本无拒绝；按余额差实际¥2.26"),
    ("v3-smoke-development-v26", "v26单Agent冒烟 · 开发集", "root-cause-v3-smoke-v26.json", "development", "smoke",
     "有效；1/9结构失败，未达冒烟标准，促成v27解析修订"),
    ("v3-baselines-validation", "v3 确定性基线 · 验证集", "root-cause-v3-validation-baselines.json", "validation",
     "baseline", "有效；已查看，未用于调参"),
    ("v3-baselines-development", "v3 确定性基线 · 开发集", "root-cause-v3-development-baselines.json", "development",
     "baseline", "有效"),
]
# Runs killed externally keep "running" in their report; the summary states the
# observed outcome and counts only repeats that were actually reached.
OVERRIDES = {"root-cause-v3-smoke-v27.json": {"status": "interrupted"},
             "root-cause-v3-sealed-active.json": {"status": "interrupted", "repeat": 1},
             "root-cause-v3-sealed-multi.json": {"status": "interrupted"}}
# Reports without runner metrics are scored separately and never edited in place:
# report -> (scores file, key path, method to keep or None for all).
DESCRIPTIVE = ("interrupted_runs_descriptive", "all_completed_samples")
METRICS_FILES = {"root-cause-v3-smoke-v27.json": ("root-cause-v3-smoke-v27-partial-scores.json", ("on_common_cases",), None),
                 "root-cause-v3-sealed-active.json": ("root-cause-v3-sealed-partial-analysis.json", DESCRIPTIVE, "active"),
                 "root-cause-v3-sealed-multi.json": ("root-cause-v3-sealed-partial-analysis.json", DESCRIPTIVE, "multi")}
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
CONCLUSION = (
    "核心主张“模型＋主动补证优于同预算确定性方法”在两代独立封存测试上均未获支持。v3封存集（新数据、v27窄契约，"
    "9例×3次）：单Agent原因F1 0.508、结论70.4%、结构失败0/27，仍低于同预算最强确定性基线（规则＋固定补证与关键词补证"
    "均为0.563／77.8%），差值−0.054（场景族自助区间−0.547至+0.400）；F1≥0.85、增益≥0.05、补证≥90%三项预注册目标"
    "均未达成。v2封存集单Agent为0.438／38.9%、6/18输出失败。窄契约消除了结构失败，剩余差距来自推理。主动调查与固定"
    "多Agent的封存运行被中断（8/18、6/9样本）：已完成样本中主动调查0.524／50.0%（2次结构失败），固定多Agent "
    "0.800／100%，但样本少、仅1次重复、每样本请求20.7次（单Agent 4.7次），未完成预注册对比，不能作为支持证据。")
LIMITATIONS = ["合成数据，为检验补证与文字理解能力而设计，不能外推为真实账套效果；数据与真值由同一作者编写。",
               "封存集仅9个场景族；单Agent差值的自助区间跨越零，样本量不足以排除小幅差异。",
               "主动调查与固定多Agent的封存运行未完成，其数字只是已完成样本的描述，不是预注册结果。",
               "费用为按官方低谷价的列表价估算，不是账单；中断运行按守护进程同一公式事后计算。"]


def _metrics(report: dict) -> dict:
    if "metrics" in report:  # model runner report or separately scored report
        source = report["metrics"]
    else:  # deterministic baseline report
        source = {name: data["overall"] for name, data in report["methods"].items()}
    return {name: {key: value[key] for key in ("samples", "failures", "category_macro_f1", "conclusion_accuracy",
                                               "decisive_evidence_obtained")} for name, value in source.items()}


def _cost(path: Path, report: dict, prices: dict) -> str | None:
    """Guard log when present; for interrupted runs whose guard died with them, the guard's formula."""
    guard = path.with_name(path.stem + "-guard.json")
    if guard.exists():
        return json.loads(guard.read_text(encoding="utf-8")).get("final_offpeak_cny")
    if path.name not in OVERRIDES:
        return None
    groups = estimate_batch(report, prices)["groups"].values()
    usd = sum(Decimal(g["known_usage_minimum_usd"]) for g in groups)
    return str(round((usd + sum(g["unknown_attempts"] for g in groups) * Decimal("0.003")) * Decimal("7.3"), 3))


def main() -> None:
    prices = load_prices(PRICES)
    runs = []
    for identity, label, filename, split, role, validity in [*V3_RUNS, *RUNS]:
        path = REPORTS / filename
        if not path.exists():
            runs.append({"id": identity, "label": label, "status": "missing_local_report", "report": filename})
            continue
        data = path.read_bytes()
        report = json.loads(data)
        cost = _cost(path, report, prices)
        if filename in METRICS_FILES:
            scores, keys, method = METRICS_FILES[filename]
            source = json.loads((REPORTS / scores).read_text(encoding="utf-8"))
            for key in keys:
                source = source[key]
            report = {**report, "metrics": source if method is None else {method: source[method]}}
        report = {**report, **OVERRIDES.get(filename, {})}
        runs.append({"id": identity, "label": label, "split": split, "role": role, "validity": validity,
                     "report": filename, "report_sha256": hashlib.sha256(data).hexdigest(),
                     "prompt_version": report.get("prompt_version"), "runner_version": report.get("runner_version"),
                     "repeat": report.get("repeat", 1), "paid_requests": report.get("paid_requests", 0),
                     "offpeak_cost_cny_estimate": cost, "status": report.get("status", "completed"),
                     "methods": _metrics(report)})
    summary = {"schema_version": "root-cause-results-v1", "created_at": datetime.now(UTC).isoformat(),
               "track_version": "root-cause-track-v3", "dataset_version": "root-cause-v3.0.0",
               "earlier_dataset_versions": ["root-cause-v2.0.0"], "runs": runs,
               "conclusion": CONCLUSION, "limitations": LIMITATIONS}
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"runs": len(runs), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
