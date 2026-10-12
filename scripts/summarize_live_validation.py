"""Update public trial history from local reports without model calls or secrets.

The original protocol/v4 pilot summary is retained. Case data, hidden answers,
model text and private reasoning never enter the public aggregate.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected report object")
    return value


def run_metadata(identity: str | None) -> dict:
    if not identity or not (ROOT / "runtime/ledgerlens.sqlite").is_file():
        return {}
    # Read-only connection; this script cannot start or change an investigation.
    database = (ROOT / "runtime/ledgerlens.sqlite").as_posix()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT result FROM agent_runs WHERE id=?", (identity,)).fetchone()
    if not row or not row[0]:
        return {}
    value = json.loads(row[0])
    return {key: value.get(key) for key in (
        "prompt_version", "tool_version", "configured_model", "model_parameters", "model_requests")}


def main() -> None:
    output = ROOT / "docs/live-model-validation-summary.json"
    summary = read_json(output)
    trials = []
    for path in sorted((ROOT / "runtime").glob("deepseek-product-*-v*.json")):
        if not re.fullmatch(r"deepseek-product-(single|multi|active)-v\d+\.json", path.name):
            continue
        report = read_json(path)
        identity = report.get("run_id") or report.get("id")
        metadata = run_metadata(identity)
        if not metadata:
            result = report.get("result") or {}
            metadata = {key: result.get(key) for key in (
                "prompt_version", "tool_version", "configured_model", "model_parameters", "model_requests")}
        error = report.get("error")
        if report.get("run_error"):
            try:
                error = json.loads(report["run_error"])
            except (ValueError, TypeError):
                error = {"code": "unstructured_run_error"}
        verification = report.get("verifications", {})
        trials.append({
            "report": str(path.relative_to(ROOT)).replace("\\", "/"),
            "mode": report.get("mode"), "acceptance_status": report.get("status"),
            "run_status": report.get("run_status", report.get("status")),
            "started_at": report.get("started_at", report.get("created_at")),
            "finished_at": report.get("finished_at"), "elapsed_seconds": report.get("elapsed_seconds"),
            "usage": report.get("usage"), "error_code": error.get("code") if isinstance(error, dict) else None,
            "original_records_unchanged": verification.get("original_records_unchanged"),
            "reference_checks": verification.get("references"),
            "exports": verification.get("exports"), **metadata,
        })
    batches = []
    for path in sorted((ROOT / ".local/reports").glob("deepseek-pilot-v*.json")):
        if not re.fullmatch(r'deepseek-pilot-v\d+\.json', path.name):
            continue
        report = read_json(path)
        rows = report.get("cases", [])
        methods = sorted({row["method"] for row in rows})
        expected = {(case, method, repeat + 1) for case in report.get("selected_case_ids", [])
                    for method in methods for repeat in range(report.get("repeat", 0))}
        observed = {(row.get("case_id"), row.get("method"), row.get("repeat")) for row in rows}
        repeats_executed = (bool(expected) and observed == expected and len(rows) == len(expected)
                            and all(row.get("method") == "rules" or row.get("paid_requests", 0) > 0 for row in rows))
        batches.append({
            "report": str(path.relative_to(ROOT)).replace("\\", "/"),
            "schema_version": report.get("schema_version"), "status": report.get("status"),
            "requested_repeats": report.get("repeat"), "finished_samples": len(rows),
            "paid_requests": report.get("paid_requests"),
            "paid_requests_are_lower_bound": report.get("status") in {"running", "interrupted"},
            "current_sample_unscored": bool(report.get("current_sample")),
            "full_repeat_requirement_met": report.get("status") in {"completed", "completed_with_failures"}
                                            and report.get("repeat", 0) >= 3 and repeats_executed,
            "by_method": {method: {
                "samples": sum(row.get("method") == method for row in rows),
                "failed": sum(row.get("method") == method and bool(row.get("error")) for row in rows),
                "error_counts": dict(Counter((row.get("error") or {}).get("code")
                                              for row in rows if row.get("method") == method and row.get("error"))),
            } for method in methods},
            'quality_metrics': {method: {key: group.get(key) for key in (
                'samples', 'completed', 'failures', 'families', 'supported_rule_labels', 'macro_rule_f1',
                'observed_rule_macro_f1', 'exact_rule_match_rate', 'status_match_rate',
                'unexpected_predicted_rule_count', 'normal_false_positive_rate',
                'paid_requests', 'tokens', 'cost_scope', 'cost_sample_rows', 'unscorable')}
                                for method, group in report.get('groups', {}).items()},
            "interruption_reason": report.get("interruption", {}).get("reason"),
            "quality_claim": "No root-cause or professional accounting accuracy established by these pilot rows.",
        })
        cost_path = path.with_name(path.stem + '-costs.json')
        if cost_path.is_file():
            cost = read_json(cost_path)
            if cost.get('source_report_sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError('Cost estimate does not match this report version')
            batches[-1]['list_price_estimate'] = {key: cost.get(key) for key in (
                'schema_version', 'currency', 'price_source', 'groups', 'limitations')}
    summary.update(updated_at=datetime.now(UTC).isoformat(), trial_history=trials, batch_history=batches,
                   aggregate_command=".venv\\Scripts\\python.exe scripts/summarize_live_validation.py",
                   latest_history_scope="Acceptance, actual provider usage, failures and interruptions; not proven multi-agent gains.")
    encoded = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)
    if "reasoning_content" in encoded or re.search(r"\bsk-[A-Za-z0-9_-]{8,}", encoded):
        raise ValueError("Private content cannot enter a public aggregate")
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(encoded + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"trials": len(trials), "batches": len(batches), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
