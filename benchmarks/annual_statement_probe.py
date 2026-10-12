"""Read-only local-report feasibility probe; not runtime extraction or test truth.

Only existing local PDF bytes are read. No network, model, OCR or Agent calls.
Reported financial values use Decimal and leave this probe only as strings.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import re

import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IDS = ("annual-002024-2025", "annual-600588-2025", "annual-688111-2025")
VERSION = "annual-statement-readonly-probe-v1"
MAX_SECTION_PAGES = 5
LABELS = {
    "total_assets": {"资产总计"},
    "total_liabilities": {"负债合计"},
    "total_equity": {"所有者权益合计", "所有者权益（或股东权益）合计", "股东权益合计"},
}
NUMBER = re.compile(r"^(?:-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\))$")
CONSOLIDATED = re.compile(r"^(?:\d+[、.．])?合并资产负债表(?:[（(]续[）)])?$")
OTHER_STATEMENT = re.compile(
    r"^(?:\d+[、.．])?(?:母公司|公司|合并)(?:资产负债表|利润表|现金流量表|股东权益变动表|所有者权益变动表)"
    r"(?:[（(]续[）)])?$"
)


class ProbeRejected(ValueError):
    pass


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _amount(value: str | None) -> Decimal:
    if value is None:
        raise ProbeRejected("missing_numeric_cell")
    value = _compact(value).replace("−", "-").replace("－", "-").replace("（", "(").replace("）", ")")
    if not NUMBER.fullmatch(value):
        raise ProbeRejected("unrecognized_numeric_cell")
    if value.startswith("("):
        value = "-" + value[1:-1]
    return Decimal(value.replace(",", ""))


def _metric(label: str | None) -> str | None:
    compact = _compact(label or "")
    return next((key for key, aliases in LABELS.items() if compact in aliases), None)


def _physical_columns(row: list, cells: list) -> list[tuple[int, str | None, tuple]]:
    """Ignore only PDF grid slots with no physical cell and no text.

    A missing VALUE inside a real cell is retained and rejected by _amount;
    it is never shifted away or replaced by another column. Original indices
    survive in evidence locators even when merged cells create grid holes.
    """
    if len(row) != len(cells):
        raise ProbeRejected("unsupported_row_column_layout")
    physical = []
    for index, (text, cell) in enumerate(zip(row, cells)):
        if cell is None:
            if text not in (None, ""):
                raise ProbeRejected("text_in_cell_without_geometry")
            continue
        physical.append((index, text, cell))
    if len(physical) != 4:
        raise ProbeRejected("unsupported_row_column_layout")
    return physical


def _split_equity_row(rows: list, geometries: list, index: int) -> dict | None:
    """Recognize one bounded PDF layout, without searching unrelated rows.

    A split label occupies two immediately adjacent cells in column 1. The
    other four semantic cells span both fragments; two narrow, empty padding
    cells surround the label. Amounts come only from those original cells.
    Parent-attributable equity, subtotals and combined totals never match the
    exact full-equity aliases. Any unsupported geometry remains unobserved.
    """
    first = rows[index]
    if len(first) != 6 or index + 1 >= len(rows):
        return None
    if any(first[slot] not in (None, "") for slot in (0, 2, 3)):
        return None
    prefix = _compact(first[1] or "")
    if not prefix or not any(alias.startswith(prefix) and alias != prefix for alias in LABELS["total_equity"]):
        return None
    second = rows[index + 1]
    if len(second) != 6 or not second[1] or any(second[slot] not in (None, "") for slot in (0, 2, 3, 4, 5)):
        return None
    label = first[1] + "\n" + second[1]
    if _metric(label) != "total_equity":
        return None
    first_cells, second_cells = geometries[index].cells, geometries[index + 1].cells
    if (len(first_cells) != 6 or len(second_cells) != 6 or any(cell is None for cell in first_cells)
            or second_cells[1] is None or any(second_cells[slot] is not None for slot in (0, 2, 3, 4, 5))):
        return None
    left, top_label, right, note, current, previous = first_cells
    bottom_label = second_cells[1]
    # The two fragments must share an x interval and touch vertically. Both
    # original amount cells must physically span that complete label interval.
    if (any(abs(top_label[axis] - bottom_label[axis]) > 1 for axis in (0, 2))
            or abs(top_label[3] - bottom_label[1]) > 1
            or top_label[3] <= top_label[1] or bottom_label[3] <= bottom_label[1]):
        return None
    y_top, y_bottom = top_label[1], bottom_label[3]
    for cell in (left, right, note, current, previous):
        if abs(cell[1] - y_top) > 1 or abs(cell[3] - y_bottom) > 1:
            return None
    if any(not 0 < cell[2] - cell[0] <= 8 for cell in (left, right)):
        return None
    for before, after in zip((left, top_label, right, note, current), (top_label, right, note, current, previous)):
        if abs(before[2] - after[0]) > 1:
            return None
    bbox = geometries[index].bbox
    if (abs(bbox[0] - left[0]) > 1 or abs(bbox[2] - previous[2]) > 1
            or abs(bbox[1] - y_top) > 1 or abs(bbox[3] - y_bottom) > 1):
        return None
    return {"label": label, "bbox": bbox,
            "physical": [(1, label, (left[0], y_top, right[2], y_bottom)),
                         (3, first[3], note), (4, first[4], current), (5, first[5], previous)],
            "label_fragments": [{"table_row": index + 1, "table_column_zero_based": 1,
                                 "text": first[1], "bbox": list(top_label)},
                                {"table_row": index + 2, "table_column_zero_based": 1,
                                 "text": second[1], "bbox": list(bottom_label)}],
            "table_rows": [index + 1, index + 2]}


def _lines(page) -> list[dict]:
    lines = page.extract_text_lines()
    # Freeze the meaning of line numbers: extract_text().splitlines(), one-based.
    if [line["text"] for line in lines] != (page.extract_text() or "").splitlines():
        raise ProbeRejected("text_line_geometry_disagrees_with_text_order")
    return [{"line": index + 1, "text": line["text"],
             "bbox": [line["x0"], line["top"], line["x1"], line["bottom"]]}
            for index, line in enumerate(lines)]


def _section(pdf, source: dict) -> tuple[list[dict], dict]:
    start = source["validation"]["statement_pages"]["balance_sheet"]
    title_page = pdf.pages[start - 1]
    title_lines = _lines(title_page)
    title = next((line for line in title_lines if CONSOLIDATED.fullmatch(_compact(line["text"]))), None)
    if title is None:
        raise ProbeRejected("consolidated_balance_sheet_title_not_confirmed")
    first_stop = next((line["bbox"][1] for line in title_lines
                       if line["bbox"][1] > title["bbox"][1]
                       and OTHER_STATEMENT.fullmatch(_compact(line["text"]))
                       and not CONSOLIDATED.fullmatch(_compact(line["text"]))), title_page.height)
    scoped_title_lines = [line for line in title_lines
                          if line["bbox"][1] >= title["bbox"][1] and line["bbox"][3] <= first_stop]
    period = f"{source['report_year']}年12月31日"
    period_line = next((line for line in scoped_title_lines if _compact(line["text"]) == period), None)
    if period_line is None or period_line["line"] < title["line"]:
        raise ProbeRejected("year_end_statement_date_not_confirmed")
    unit_lines = [line for line in scoped_title_lines if "单位" in _compact(line["text"]) and "人民币" in line["text"]]
    if len(unit_lines) != 1:
        raise ProbeRejected("monetary_unit_not_unique")
    unit_line = unit_lines[0]
    unit_match = re.search(r"(?:单位[:：]|金额单位(?:均)?为)(?:人民币)?(千元|元)", _compact(unit_line["text"]))
    if unit_match is None:
        raise ProbeRejected("unsupported_or_unconfirmed_monetary_unit")
    unit = unit_match.group(1)
    pages = []
    stop = None
    for number in range(start, min(len(pdf.pages) + 1, start + MAX_SECTION_PAGES)):
        page = pdf.pages[number - 1]
        lines = title_lines if number == start else _lines(page)
        lower = title["bbox"][1] if number == start else 0
        upper = page.height
        for line in lines:
            compact = _compact(line["text"])
            if line["bbox"][1] < lower:
                continue
            if OTHER_STATEMENT.fullmatch(compact) and not CONSOLIDATED.fullmatch(compact):
                upper = line["bbox"][1]
                stop = {"pdf_page": number, **line}
                break
        pages.append({"pdf_page": number, "page": page, "lines": lines, "top": lower, "bottom": upper})
        for line in lines:
            compact_line = _compact(line["text"])
            if lower <= line["bbox"][1] < upper and "币种" in compact_line:
                if not re.search(r"币种[:：]人民币(?=$|(?:金额)?单位[:：])", compact_line):
                    raise ProbeRejected("currency_changed_inside_statement")
            if lower <= line["bbox"][1] < upper and re.fullmatch(r"20\d{2}年\d{1,2}月\d{1,2}日", compact_line):
                if compact_line != period:
                    raise ProbeRejected("statement_date_changed_inside_section")
            if lower <= line["bbox"][1] < upper and "单位" in line["text"]:
                explicit = re.search(r"(?:单位[:：]|金额单位(?:均)?为)(?:人民币)?(百万元|万元|千元|元)", compact_line)
                if explicit is not None and explicit.group(1) != unit:
                    raise ProbeRejected("monetary_unit_changed_inside_statement")
                if explicit is None and ("金额单位" in compact_line or compact_line.startswith("单位")):
                    raise ProbeRejected("unsupported_explicit_monetary_unit")
        if stop is not None:
            break
    if stop is None:
        raise ProbeRejected("section_end_not_confirmed_within_five_pages")
    context = {"statement_title": {"pdf_page": start, **title},
               "statement_date": {"pdf_page": start, **period_line},
               "unit": unit, "currency": "CNY", "unit_factor_cny": "1000" if unit == "千元" else "1",
               "unit_source": {"pdf_page": start, **unit_line}, "section_stop": stop}
    return pages, context


def _column_header(pages: list[dict], year: int, *, allow_missing=False) -> dict | None:
    current_date, previous_date = f"{year}年12月31日", f"{year - 1}年12月31日"
    candidates = []
    first = pages[0]
    for table in first["page"].find_tables():
        for index, row in enumerate(table.extract()):
            header_bbox = table.rows[index].bbox
            if header_bbox[1] < first["top"] or header_bbox[3] > first["bottom"]:
                continue
            if len(row) != 4 or not row[0] or _compact(row[0]) not in {"项目", "资产", "负债和所有者权益"}:
                continue
            normalized = [_compact(cell or "") for cell in row]
            if current_date in normalized and previous_date in normalized:
                current, previous = normalized.index(current_date), normalized.index(previous_date)
            elif normalized[2:] == ["期末余额", "上年年末余额"]:
                if not any(_compact(line["text"]) == current_date
                           and line["bbox"][1] >= first["top"] and line["bbox"][3] <= first["bottom"]
                           for line in first["lines"]):
                    raise ProbeRejected("symbolic_period_columns_without_explicit_current_date")
                current, previous = 2, 3
            else:
                raise ProbeRejected("unsupported_explicit_period_column_header")
            if current < 2 or previous < 2 or current == previous:
                continue
            cells = table.rows[index].cells
            if cells[current] is None or cells[previous] is None:
                raise ProbeRejected("column_header_geometry_not_confirmed")
            candidates.append({"pdf_page": first["pdf_page"], "cells": row,
                               "current_column_zero_based": current, "previous_column_zero_based": previous,
                               "current_column_bbox": list(cells[current]),
                               "previous_column_bbox": list(cells[previous]),
                               "table_header_bbox": list(table.rows[index].bbox)})
    if not candidates and allow_missing:
        return None
    if len(candidates) != 1:
        raise ProbeRejected("current_previous_column_header_not_unique")
    return candidates[0]


def probe(source: dict, coverage_hash: str) -> dict:
    result = {"id": source["id"], "company_code": source["company_code"],
              "company_name": source["company_name"], "report_year": source["report_year"],
              "url": source["url"], "raw_sha256": source["sha256"], "raw_bytes": source["bytes"],
              "local_relative_path": source["local_relative_path"], "coverage_manifest_sha256": coverage_hash,
              "status": "rejected", "metrics": {}, "independent_ground_truth_checked": False}
    try:
        raw_root = ROOT / ".local/annual-reports"
        path = (raw_root / source["local_relative_path"]).resolve()
        if not path.is_relative_to((raw_root / "raw").resolve()):
            raise ProbeRejected("original_outside_local_raw_tree")
        content = path.read_bytes()
        if (len(content) != source["bytes"] or hashlib.sha256(content).hexdigest() != source["sha256"]
                or source["status"] != "verified_full_report"):
            raise ProbeRejected("original_snapshot_hash_or_status_mismatch")
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages, context = _section(pdf, source)
            header = _column_header(pages, source["report_year"])
            current, previous = header["current_column_zero_based"], header["previous_column_zero_based"]
            result.update(context=context, column_header=header, scanned_pdf_pages=[p["pdf_page"] for p in pages])
            candidates = {key: [] for key in LABELS}
            inherited_header = header
            for part in pages:
                # Repeated headers can change column widths across pages. A
                # fresh explicit header takes precedence over inherited geometry.
                page_header = _column_header([part], source["report_year"], allow_missing=True) or inherited_header
                inherited_header = page_header
                current, previous = (page_header["current_column_zero_based"],
                                     page_header["previous_column_zero_based"])
                for table_number, table in enumerate(part["page"].find_tables(), 1):
                    rows = table.extract()
                    for row_number, row in enumerate(rows, 1):
                        key = _metric(row[0]) if row else None
                        split = _split_equity_row(rows, table.rows, row_number - 1) if key is None else None
                        if split is not None:
                            key = "total_equity"
                        if key is None:
                            continue
                        geometry = table.rows[row_number - 1]
                        bbox = geometry.bbox
                        if bbox[1] < part["top"] or bbox[3] > part["bottom"]:
                            continue
                        physical = split["physical"] if split is not None else _physical_columns(row, geometry.cells)
                        current_index, current_text, current_bbox = physical[current]
                        previous_index, previous_text, previous_bbox = physical[previous]
                        for cell, reference in ((current_bbox, page_header["current_column_bbox"]),
                                                (previous_bbox, page_header["previous_column_bbox"])):
                            if abs(cell[0] - reference[0]) > 3 or abs(cell[2] - reference[2]) > 3:
                                raise ProbeRejected("continuation_amount_column_geometry_changed")
                        amount, prior = _amount(current_text), _amount(previous_text)
                        normalized_cny = amount * Decimal(context["unit_factor_cny"])
                        if normalized_cny != normalized_cny.quantize(Decimal("0.01")):
                            raise ProbeRejected("normalized_cny_would_require_rounding")
                        evidence = [line for line in part["lines"]
                                    if line["bbox"][1] >= bbox[1] - 1 and line["bbox"][3] <= bbox[3] + 1]
                        visible = _compact("\n".join(line["text"] for line in evidence))
                        if _compact(current_text or "") not in visible:
                            raise ProbeRejected("table_current_cell_not_confirmed_in_text_lines")
                        if _compact(previous_text or "") not in visible:
                            raise ProbeRejected("table_previous_cell_not_confirmed_in_text_lines")
                        if split is not None:
                            for fragment in split["label_fragments"]:
                                cell = fragment["bbox"]
                                fragment_lines = [line["text"] for line in evidence
                                                  if line["bbox"][1] >= cell[1] - 1
                                                  and line["bbox"][3] <= cell[3] + 1]
                                if _compact(fragment["text"]) not in _compact("\n".join(fragment_lines)):
                                    raise ProbeRejected("split_label_fragment_not_confirmed_in_text_lines")
                        observation = {"amount_reported": str(amount), "previous_amount_reported": str(prior),
                                                "unit": context["unit"], "currency": "CNY",
                                                "normalized_cny": format(normalized_cny, ".2f"),
                                                "pdf_page": part["pdf_page"], "table": table_number, "table_row": row_number,
                                                "column_header_pdf_page": page_header["pdf_page"],
                                                "current_column_header": page_header["cells"][current],
                                                "label_cell": split["label"] if split is not None else row[0],
                                                "current_cell": current_text, "previous_cell": previous_text,
                                                "current_table_column_zero_based": current_index,
                                                "previous_table_column_zero_based": previous_index,
                                                "row_bbox": list(bbox), "current_cell_bbox": list(current_bbox),
                                                "previous_cell_bbox": list(previous_bbox),
                                                "text_lines": evidence, "table_cell_visible_in_text_lines": True}
                        if split is not None:
                            observation.update(label_fragments=split["label_fragments"], table_rows=split["table_rows"])
                        candidates[key].append(observation)
            if any(len(values) != 1 for values in candidates.values()):
                raise ProbeRejected("required_total_missing_or_duplicate")
            result["metrics"] = {key: values[0] for key, values in candidates.items()}
            a, liabilities, equity = (Decimal(result["metrics"][key]["amount_reported"])
                                      for key in ("total_assets", "total_liabilities", "total_equity"))
            difference = a - liabilities - equity
            result.update(status="observed", balance_check={"difference_reported_unit": str(difference),
                                                           "equal_as_reported": difference == 0,
                                                           "scope": "internal arithmetic only; not extraction ground truth"})
    except (ProbeRejected, OSError, ValueError, KeyError) as exc:
        result.update(status="rejected", error_code=str(exc) if isinstance(exc, ProbeRejected) else type(exc).__name__)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--report-id", action="append", choices=DEFAULT_IDS)
    selection.add_argument("--all-local", action="store_true",
                           help="Attempt every report in the local coverage manifest; keep all explicit rejections")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/annual-statement-probe/report.json")
    args = parser.parse_args(argv)
    if not args.output.resolve().is_relative_to((ROOT / ".local/annual-statement-probe").resolve()):
        raise ProbeRejected("Probe output must remain in the ignored local probe tree")
    if args.output.exists():
        raise ProbeRejected("Existing probe report retained; choose a fresh output name")
    coverage_path = ROOT / "docs/annual-report-coverage.json"
    coverage_bytes = coverage_path.read_bytes()
    coverage = json.loads(coverage_bytes)
    sources = {source["id"]: source for source in coverage["reports"]}
    if len(sources) != len(coverage["reports"]):
        raise ProbeRejected("Duplicate source identities in coverage manifest")
    requested = list(sources) if args.all_local else list(dict.fromkeys(args.report_id or DEFAULT_IDS))
    coverage_hash = hashlib.sha256(coverage_bytes).hexdigest()
    reports = [probe(sources[identity], coverage_hash) for identity in requested]
    document = {"schema_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "parser_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "coverage_manifest_sha256": coverage_hash, "requested_ids": requested,
                "pdfplumber_version": importlib.metadata.version("pdfplumber"), "reports_attempted": len(reports),
                "reports_observed": sum(row["status"] == "observed" for row in reports),
                "reports_rejected": sum(row["status"] == "rejected" for row in reports),
                "total_available_reports": len(sources), "unprobed_reports": len(sources) - len(reports),
                "runtime_agent_used": False, "network_or_model_calls": 0, "ocr_used": False,
                "rule_coverage_evidence": False, "financial_accuracy_benchmark": False, "reports": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"observed": sum(row["status"] == "observed" for row in reports),
                      "rejected": sum(row["status"] == "rejected" for row in reports),
                      "available_but_unprobed": document["unprobed_reports"], "output": str(args.output)}, ensure_ascii=True))
    return 0 if all(row["status"] == "observed" for row in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
