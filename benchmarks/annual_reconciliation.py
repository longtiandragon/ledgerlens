"""Deterministic consistency checks over located annual-report observations.

Input: the probe's located observations (not answers). Exact Decimal arithmetic
in each report's own unit. Two checks:
1. Balance identity: total assets = total liabilities + total equity, for the
   current and the prior-year column of each observed report.
2. Cross-year continuity: the prior-year column of year Y equals the current
   column of year Y-1 for the same company, after unit normalisation.
A failure means an extraction error OR a genuine restatement; it is a review
item, never an automatic accounting conclusion. No model, network or OCR.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

UNITS = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000), "百万元": Decimal(1000000)}
ITEMS = ("total_assets", "total_liabilities", "total_equity")


def _amount(text) -> Decimal | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def balance(report: dict) -> list[dict]:
    metrics = report.get("metrics") or {}
    if any(item not in metrics for item in ITEMS):
        return [{"report_id": report["id"], "column": "all", "status": "not_checkable",
                 "reason": "missing_located_total"}]
    results = []
    for column, field in (("current", "amount_reported"), ("previous", "previous_amount_reported")):
        values = {item: _amount(metrics[item].get(field)) for item in ITEMS}
        units = {metrics[item].get("unit") for item in ITEMS}
        if None in values.values() or len(units) != 1:
            results.append({"report_id": report["id"], "column": column, "status": "not_checkable",
                            "reason": "missing_amount_or_mixed_units"})
            continue
        difference = values["total_assets"] - values["total_liabilities"] - values["total_equity"]
        results.append({"report_id": report["id"], "column": column, "unit": units.pop(),
                        "status": "consistent" if difference == 0 else "needs_review",
                        "difference_reported_unit": format(difference, "f"),
                        "pages": sorted({metrics[item].get("pdf_page") for item in ITEMS})})
    return results


def continuity(reports: list[dict]) -> list[dict]:
    observed = {(r["company_code"], r["report_year"]): r for r in reports if r.get("status") == "observed"}
    results = []
    for (company, year), later in sorted(observed.items()):
        earlier = observed.get((company, year - 1))
        if earlier is None:
            continue
        for item in ITEMS:
            a, b = later["metrics"].get(item, {}), earlier["metrics"].get(item, {})
            prior, current = _amount(a.get("previous_amount_reported")), _amount(b.get("amount_reported"))
            if prior is None or current is None or a.get("unit") not in UNITS or b.get("unit") not in UNITS:
                status, difference = "not_checkable", None
            else:
                difference = prior * UNITS[a["unit"]] - current * UNITS[b["unit"]]
                status = "consistent" if difference == 0 else "needs_review"
            results.append({"company_code": company, "item": item, "later_report": later["id"],
                            "earlier_report": earlier["id"], "status": status,
                            "difference_cny": format(difference, "f") if difference is not None else None,
                            "interpretation": "restatement_or_extraction_error_to_review" if status == "needs_review" else None})
    return results


def reconcile(probe: dict) -> dict:
    reports = probe["reports"]
    observed = [r for r in reports if r.get("status") == "observed"]
    balance_rows = [row for r in observed for row in balance(r)]
    continuity_rows = continuity(reports)

    def counts(rows):
        result = {}
        for row in rows:
            result[row["status"]] = result.get(row["status"], 0) + 1
        return dict(sorted(result.items()))

    return {"schema_version": "annual-reconciliation-v1", "created_at": datetime.now(UTC).isoformat(),
            "probe_schema": probe.get("schema_version"), "probe_created_at": probe.get("created_at"),
            "parser_source_sha256": probe.get("parser_source_sha256"),
            "reports_total": len(reports), "reports_observed": len(observed),
            "balance_counts": counts(balance_rows), "continuity_counts": counts(continuity_rows),
            "balance": balance_rows, "continuity": continuity_rows, "model_requests": 0,
            "scope": "internal consistency of located totals; not audited financial truth or accuracy"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, default=Path(".local/annual-statement-probe/all-local-split-v4.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = reconcile(json.loads(args.probe.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("reports_observed", "balance_counts", "continuity_counts")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
