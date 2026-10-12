"""Model methods on the root-cause v2 track, through the real investigation graph.

Each sample: round 1 sees the visible snapshot with all deterministic rule
results as verified claims and may make ONE structured evidence request. If it
does, this evaluator releases matching withheld records into a new snapshot and
a fresh round 2 investigation runs with no further requests. Truth is read only
after every sample has finished. Failures stay in the denominator.

Example:
    python -m benchmarks.root_cause_v2_models --split validation --per-family 1 \
        --modes single active --repeat 3 --max-paid-requests 200 --env-file .env --report <path>
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.investigation import PROMPT_VERSION, investigate  # noqa: E402
from app.model_gateway import BudgetExceeded, DeepSeekGateway, notify  # noqa: E402
from benchmarks.root_cause_v2 import METHODS, TRACK_VERSION, load_case, release, score  # noqa: E402
from benchmarks.paid_budget import PricedRequestBudget  # noqa: E402
from benchmarks.run_agents import (  # noqa: E402
    BoundedGateway, PaidRequestBudget, _atomic_json, _model_configuration, _new_json,
)

CUTOFF = "2026-09-30T23:59:59+00:00"
RUNNER_VERSION = "rc-runner-v3"  # Hard dispatch cap includes both rounds, retries and failed samples.
FAILED = {"failed", "budget_exhausted"}
FROZEN_SOURCES = (
    'backend/app/investigation.py', 'backend/app/model_contract.py', 'backend/app/model_gateway.py',
    'backend/app/domain.py', 'backend/app/evidence_planning.py', 'backend/app/evidence_selection.py',
    'benchmarks/root_cause_v2.py', 'benchmarks/root_cause_v2_models.py', 'benchmarks/run_agents.py',
    'benchmarks/paid_budget.py', 'datasets/generate.py', 'datasets/generate_v2.py', 'datasets/generate_v3.py',
)


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FROZEN_SOURCES}


def validate_plan(plan, options, manifest_bytes, selected, model):
    if (plan.get('dataset_manifest_sha256') != hashlib.sha256(manifest_bytes).hexdigest()
            or plan.get('source_sha256') != source_hashes()
            or plan.get('selected_case_ids') != [c['case_id'] for c in selected]
            or plan.get('model') != model
            or any(plan.get(name) != getattr(options, name) for name in (
                'split', 'per_family', 'repeat', 'modes', 'max_paid_requests', 'max_cny'))):
        raise ValueError('Experiment differs from its frozen plan; prepare and review a new plan')


class ProgressGateway(BoundedGateway):
    """Persist each actual attempt and returned usage for the external cost guard."""

    def __init__(self, gateway, budget, sample_id, on_progress):
        super().__init__(gateway, budget, sample_id)
        self.on_progress = on_progress

    async def chat(self, messages, *, on_attempt=None, **kwargs):
        async def progress(event):
            await notify(self.on_progress, event)
            await notify(on_attempt, event)
        return await super().chat(messages, on_attempt=progress, **kwargs)


def root_cause_task(records: list[dict], categories: dict, *, allowed: bool, response: str | None = None,
                    released: list[dict] | None = None) -> dict:
    explanation = ("根据程序规则结果、可见资料和文字备注判断差异原因。" +
                   ("可提出一次结构化补证。" if allowed else "") + (response or ""))
    return {"task_type": "root_cause", "rule_id": "ROOT_CAUSE_TASK", "title": "差异原因调查", "status": "unsupported",
            "record_ids": [r["id"] for r in records], "evidence_ids": [r["evidence_id"] for r in records],
            "explanation": explanation, "missing_evidence": [],
            "metrics": {"cause_taxonomy": dict(categories), "evidence_request_allowed": allowed,
                        # The obtained records are ordinary visible evidence of the new snapshot.
                        **({"released_records": [dict(r) for r in released]} if released is not None else {})}}


def _final(result: dict) -> dict:
    proposals = result.get("proposals") or []
    final = proposals[-1] if proposals else {}
    assessment = final.get("cause_assessment") or {}
    return {"categories": list(assessment.get("categories") or []), "conclusion": final.get("status"),
            "request": assessment.get("evidence_request")}


async def run_model_case(observed: dict, withheld: list[dict], categories: dict, mode: str, gateway) -> dict:
    records = observed["records"]
    policy = {**observed["policy"], "snapshot_cutoff": CUTOFF}
    first = await investigate(records, policy, root_cause_task(records, categories, allowed=True),
                              mode=mode, gateway=gateway)
    rounds, requests_log = [first], list(first.get("model_requests", []))
    if first.get("status") in FAILED:
        return {"prediction": {"error": first.get("error") or {"code": first["status"]}}, "released": [],
                "rounds": rounds, "model_requests": requests_log}
    outcome = _final(first)
    request, released = outcome["request"], []
    if request:
        released = release(withheld, request, records)
        response = (f"评测程序已处理补证请求（{request['kind']}）：取得{len(released)}条快照外记录，已并入本快照，"
                    "内容见metrics.released_records，请核对其日期、编号、金额和备注。"
                    if released else f"评测程序已处理补证请求（{request['kind']}）：没有符合的快照外记录。")
        later = [*records, *released]
        second_policy = {**policy, "snapshot_cutoff": max([CUTOFF, *(r["observed_at"] for r in released)])}
        second = await investigate(later, second_policy,
                                   root_cause_task(later, categories, allowed=False, response=response,
                                                   released=released),
                                   mode=mode, gateway=gateway)
        rounds.append(second)
        offset = first.get('usage', {}).get('calls', 0)
        requests_log += [{**r, "request_index": (r.get("request_index") or 0) + offset}
                         for r in second.get("model_requests", [])]
        if second.get("status") in FAILED:
            return {"prediction": {"error": second.get("error") or {"code": second["status"]}, "request": request},
                    "released": released, "rounds": rounds, "model_requests": requests_log}
        outcome = {**_final(second), "request": request}
    return {"prediction": outcome, "released": released, "rounds": rounds, "model_requests": requests_log}


def select(manifest: dict, split: str, per_family: int) -> list[dict]:
    """First N variants of every family; uses opaque IDs only, never truth."""
    families: dict[str, list[dict]] = {}
    for case in manifest["cases"]:
        if case["split"] == split:
            families.setdefault(case["family_id"], []).append(case)
    return [case for family in sorted(families) for case in sorted(families[family], key=lambda c: c["case_id"])[:per_family]]


async def run(options) -> dict:
    if options.report.exists():
        raise ValueError('Refusing to overwrite an existing report')
    if (type(options.max_paid_requests) is not int or not 0 <= options.max_paid_requests <= 20_000
            or type(options.repeat) is not int or not 1 <= options.repeat <= 10
            or type(options.per_family) is not int or not 1 <= options.per_family <= 100
            or not options.modes or len(set(options.modes)) != len(options.modes)
            or not set(options.modes) <= set(METHODS) | {'single', 'multi', 'active'}):
        raise ValueError('Invalid benchmark limits or modes')
    manifest_bytes = (options.dataset / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    categories = manifest["categories"]
    cases = select(manifest, options.split, options.per_family)
    if not cases:
        raise ValueError('No cases in the selected split')
    # Verify only observable/requestable data before dispatch. Truth stays evaluator-only.
    for case in cases:
        for folder in ('observed', 'on_demand'):
            digest = case.get('file_sha256', {}).get(folder)
            if digest is not None and hashlib.sha256(
                    (options.dataset / folder / f"{case['case_id']}.json").read_bytes()).hexdigest() != digest:
                raise ValueError(f'Frozen {folder} data hash mismatch')
    config = _model_configuration(options.env_file)
    gateway = None
    if any(mode not in METHODS for mode in options.modes):
        if manifest.get('version') != 'root-cause-v3.0.0':
            raise ValueError('The v26 contract requires fresh v3 data; v2 splits are retired for model tuning')
        if not config["DEEPSEEK_API_KEY"]:
            raise ValueError("模型方法需要 DEEPSEEK_API_KEY")
        optional = {key: config[name] for key, name in (("base_url", "DEEPSEEK_BASE_URL"), ("model", "DEEPSEEK_MODEL"))
                    if config[name]}
        gateway = DeepSeekGateway(config["DEEPSEEK_API_KEY"], **optional)
    plan_path = getattr(options, 'plan', None)
    if options.split == 'sealed_test' and gateway and plan_path is None:
        raise ValueError('Sealed model evaluation requires a separately reviewed frozen plan')
    if plan_path is not None:
        validate_plan(json.loads(plan_path.read_text(encoding='utf-8')), options, manifest_bytes, cases,
                      gateway.model if gateway else None)
    report = {"schema_version": TRACK_VERSION + "-models", "created_at": datetime.now(UTC).isoformat(),
              "dataset_version": manifest["version"], "split": options.split, "prompt_version": PROMPT_VERSION,
              'dataset_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
              'source_sha256': source_hashes(),
              "runner_version": RUNNER_VERSION,
              "modes": list(options.modes), "repeat": options.repeat, "selected_case_ids": [c["case_id"] for c in cases],
              "paid_request_limit": options.max_paid_requests, "paid_requests": 0, "status": "running", "cases": []}
    started = time.perf_counter()
    budget = (PricedRequestBudget(options.max_paid_requests, getattr(options, 'max_cny', '1.20'), gateway.model)
              if gateway else PaidRequestBudget(options.max_paid_requests))
    _new_json(options.report, report)
    for repeat in range(options.repeat):
        for case in cases:
            observed, withheld = load_case(options.dataset, case["case_id"])
            for mode in options.modes:
                row = {"case_id": case["case_id"], "family_id": case["family_id"], "method": mode,
                       "repeat": repeat + 1, "model_requests": [], "paid_requests": 0, "error": None}
                sample_id = f"{case['case_id']}:{repeat + 1}:{mode}"
                def persist(event):
                    cost_error = None
                    if event.get('event') == 'model_usage':
                        row['model_requests'].append({
                            **{k: event.get(k) for k in ('request_usage', 'provider_model', 'provider_system_fingerprint',
                                                        'context_bytes', 'response_token_limit')},
                            'request_index': budget.calls_by_sample[sample_id]})
                    if isinstance(budget, PricedRequestBudget):
                        # Persist first even when a provider reports usage above its reservation.
                        try:
                            budget.settle(sample_id, event)
                        except BudgetExceeded as exc:
                            cost_error = exc
                        finally:
                            report['cost_budget'] = budget.public()
                    report['paid_requests'] = budget.used
                    report['current_sample'] = {**row, 'sample_id': sample_id,
                                                'paid_requests': budget.calls_by_sample[sample_id],
                                                'current_event': event.get('event')}
                    _atomic_json(options.report, report)
                    if cost_error:
                        raise cost_error

                bounded = ProgressGateway(gateway, budget, sample_id, persist) if mode not in METHODS else None
                try:
                    if mode in METHODS:
                        outcome = METHODS[mode](observed["records"], observed["policy"], withheld)
                        row.update(prediction=outcome, released=outcome["released"])
                    elif report["paid_requests"] >= options.max_paid_requests:
                        row.update(prediction={"error": {"code": "batch_budget_exhausted"}}, released=[])
                    else:
                        result = await run_model_case(observed, withheld, categories, mode, bounded)
                        # Codes and field paths only; never rejected model text.
                        diagnostics = [{"round": index + 1, "role": step.get("role"),
                                        "codes": [{k: d[k] for k in ("code", "path") if k in d}
                                                  for d in step.get("diagnostics", [])[:20]]}
                                       for index, round_ in enumerate(result["rounds"])
                                       for step in round_.get("steps", []) if step.get("event") == "output_validation_failed"]
                        row.update(prediction=result["prediction"], released=result["released"],
                                   validation_failures=diagnostics,
                                   model_requests=result["model_requests"],
                                   rounds=len(result["rounds"]),
                                   tokens=sum(r.get("usage", {}).get("tokens", 0) for r in result["rounds"]))
                except Exception as exc:  # recorded, never silently dropped
                    row.update(prediction={"error": {"code": "evaluation_error", "message": type(exc).__name__}},
                               released=row.get("released", []))
                row["error"] = row["prediction"].get("error")
                row['paid_requests'] = budget.calls_by_sample[sample_id]
                if bounded:
                    row['request_attempts'] = bounded.request_attempts
                    row['request_not_dispatched'] = bounded.request_not_dispatched
                report['paid_requests'] = budget.used
                if isinstance(budget, PricedRequestBudget):
                    report['cost_budget'] = budget.public()
                report["cases"].append(row)
                report.pop('current_sample', None)
                report["duration_seconds"] = time.perf_counter() - started
                _atomic_json(options.report, report)
    # Truth is read only now, after every sample has finished.
    by_method: dict[str, list[dict]] = {}
    for row in report["cases"]:
        truth_bytes = (options.dataset / 'truth' / f"{row['case_id']}.json").read_bytes()
        entry = next(case for case in cases if case['case_id'] == row['case_id'])
        digest = entry.get('file_sha256', {}).get('truth')
        if digest is not None and hashlib.sha256(truth_bytes).hexdigest() != digest:
            report['status'] = 'scoring_failed'
            report['scoring_error'] = 'frozen_truth_hash_mismatch'
            _atomic_json(options.report, report)
            raise ValueError('Frozen truth data hash mismatch; predictions retained without scores')
        truth = json.loads(truth_bytes)
        by_method.setdefault(row["method"], []).append({**row, "truth": truth})
    report["metrics"] = {method: score(rows, categories) for method, rows in by_method.items()}
    report["status"] = "completed_with_failures" if any(r["error"] for r in report["cases"]) else "completed"
    _atomic_json(options.report, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=Path(".local/datasets-v3"))
    parser.add_argument("--split", choices=["development", "validation", "sealed_test"], default="development")
    parser.add_argument("--per-family", type=int, default=1)
    parser.add_argument("--modes", nargs="+", default=["rule_only", "rule_fixed_request", "keyword_request", "single"],
                        choices=[*METHODS, "single", "multi", "active"])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-paid-requests", type=int, default=100)
    parser.add_argument('--max-cny', default='1.20', help='Pre-dispatch peak-price reservation cap in CNY')
    parser.add_argument('--plan', type=Path, help='Frozen experiment plan; mandatory for sealed model evaluation')
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args(argv)
    if options.report.exists():
        parser.error("Refusing to overwrite an existing report")
    report = asyncio.run(run(options))
    print(json.dumps({"status": report["status"], "paid_requests": report["paid_requests"],
                      "metrics": {m: {k: v[k] for k in ("category_macro_f1", "conclusion_accuracy",
                                                         "decisive_evidence_obtained", "failures")}
                                  for m, v in report["metrics"].items()}}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
