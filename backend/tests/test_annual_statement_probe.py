"""Independent hand-authored cases and three pinned, locally audited PDF samples.

Expected financial values below were transcribed from original PDF cells and
visually reviewed; they are never populated from the production probe output.
No network, credentials, model or runtime Agent is used by this module.
"""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import socket
from types import SimpleNamespace

import pytest

from benchmarks import annual_statement_probe as statement


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def deny(*_args, **_kwargs):
        raise AssertionError("Annual statement regressions must remain offline")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)


class HandwrittenTable:
    def __init__(self, specs, edges=(0, 100, 120, 200, 280)):
        self.values = [cells for cells, _ in specs]
        self.rows = [SimpleNamespace(bbox=(edges[0], top, edges[-1], bottom),
                                     cells=[(edges[index], top, edges[index + 1], bottom) for index in range(4)])
                     for _, (top, bottom) in specs]

    def extract(self):
        return deepcopy(self.values)


class HandwrittenPage:
    height = 600

    def __init__(self, lines, tables=()):
        self.lines = sorted(lines)
        self.tables = list(tables)

    def extract_text(self):
        return "\n".join(text for _, text in self.lines)

    def extract_text_lines(self):
        return [{"text": text, "x0": 0, "top": top, "x1": 280, "bottom": top + 8}
                for top, text in self.lines]

    def find_tables(self):
        return self.tables


class HandwrittenPdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def section_source():
    return {"report_year": 2025, "validation": {"statement_pages": {"balance_sheet": 1}}}


def basic_page(*, reversed_columns=False, parent_equity="25", total_equity=True, unit="元"):
    # Both current and previous triples balance: arithmetic alone cannot select
    # the correct year or scope. Explicit labels/column headers must decide.
    date_cells = ["2024年12月31日", "2025年12月31日"] if reversed_columns else [
        "2025年12月31日", "2024年12月31日"]
    asset_cells = ["70", "120"] if reversed_columns else ["120", "70"]
    liability_cells = ["50", "90"] if reversed_columns else ["90", "50"]
    equity_cells = ["20", "30"] if reversed_columns else ["30", "20"]
    specs = [(["项目", "附注", *date_cells], (80, 100)),
             (["资产总计", "", *asset_cells], (100, 120)),
             (["负债合计", "", *liability_cells], (120, 140)),
             (["归属于母公司所有者权益合计", "", parent_equity, "15"], (140, 160))]
    lines = [(10, "合并资产负债表"), (30, "2025年12月31日"), (50, f"单位：{unit} 币种：人民币"),
             (85, f"项目 附注 {' '.join(date_cells)}"), (105, f"资产总计 {' '.join(asset_cells)}"),
             (125, f"负债合计 {' '.join(liability_cells)}"),
             (145, f"归属于母公司所有者权益合计 {parent_equity} 15")]
    if total_equity:
        specs.append((["所有者权益（或股东权\n益）合计", "", *equity_cells], (180, 240)))
        lines.extend([(185, "所有者权益（或股东权"), (205, " ".join(equity_cells)), (225, "益）合计")])
    # A different, also balanced, parent-company table occupies the SAME page.
    # Its reversed-year header must never remap the consolidated amount column.
    parent = HandwrittenTable([(["项目", "附注", "2024年12月31日", "2025年12月31日"], (320, 340)),
                               (["资产总计", "", "400", "800"], (340, 360)),
                               (["负债合计", "", "300", "650"], (360, 380)),
                               (["所有者权益合计", "", "100", "150"], (380, 400))])
    lines.extend([(260, "母公司资产负债表"), (280, "2025年12月31日"), (300, "单位：元 币种：人民币"),
                  (325, "项目 附注 2024年12月31日 2025年12月31日"), (345, "资产总计 400 800"),
                  (365, "负债合计 300 650"), (385, "所有者权益合计 100 150")])
    return HandwrittenPage(lines, [HandwrittenTable(specs), parent])


@pytest.fixture
def local_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(statement, "ROOT", tmp_path)
    # Transport bytes are unrelated to any financial expectation. Unit tests
    # substitute an independently handwritten PDF representation after hashing.
    content = b"%PDF-1.7\nIndependent test transport only\n%%EOF"
    raw = tmp_path / ".local/annual-reports/raw/fixture/2025/source.pdf"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(content)
    source = {"id": "annual-002024-2025", "company_code": "002024", "company_name": "手写测试公司",
              "report_year": 2025, "url": "https://example.invalid/not-requested.pdf",
              "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
              "local_relative_path": "raw/fixture/2025/source.pdf", "status": "verified_full_report",
              "validation": {"statement_pages": {"balance_sheet": 1}}}

    def run(pages, selected_source=None):
        monkeypatch.setattr(statement.pdfplumber, "open", lambda *_args: HandwrittenPdf(pages))
        return statement.probe(selected_source or source, "independent-coverage-digest")

    return SimpleNamespace(source=source, raw=raw, run=run)


@pytest.mark.parametrize(("rows", "error"), [
    (["合并资产负债表", "2025年12月31日", "母公司资产负债表", "单位：元 币种：人民币"],
     "monetary_unit_not_unique"),
    (["合并资产负债表", "单位：元 币种：人民币", "母公司资产负债表", "2025年12月31日"],
     "year_end_statement_date_not_confirmed"),
])
def test_parent_company_date_or_unit_cannot_supply_missing_consolidated_context(rows, error):
    page = HandwrittenPage([(index * 20, text) for index, text in enumerate(rows)])
    with pytest.raises(statement.ProbeRejected, match=error):
        statement._section(HandwrittenPdf([page]), section_source())


@pytest.mark.parametrize(("prefix", "body", "error"), [
    ("2025年12月31日", "单位：元 币种：人民币", "year_end_statement_date_not_confirmed"),
    ("单位：元 币种：人民币", "2025年12月31日", "monetary_unit_not_unique"),
])
def test_context_before_consolidated_title_cannot_be_borrowed(prefix, body, error):
    page = HandwrittenPage([(0, prefix), (20, "合并资产负债表"), (40, body), (80, "母公司资产负债表")])
    with pytest.raises(statement.ProbeRejected, match=error):
        statement._section(HandwrittenPdf([page]), section_source())


@pytest.mark.parametrize(("changed", "error"), [
    ("单位：万元", "monetary_unit_changed_inside_statement"),
    ("单位：股", "unsupported_explicit_monetary_unit"),
    ("单位：元 币种：美元", "currency_changed_inside_statement"),
    ("币种：美元", "currency_changed_inside_statement"),
    ("2024年12月31日", "statement_date_changed_inside_section"),
])
def test_continuation_context_conflicts_are_explicitly_rejected(changed, error):
    first = HandwrittenPage([(0, "合并资产负债表"), (20, "2025年12月31日"), (40, "单位：元 币种：人民币")])
    following = HandwrittenPage([(0, changed), (80, "母公司资产负债表")])
    with pytest.raises(statement.ProbeRejected, match=error):
        statement._section(HandwrittenPdf([first, following]), section_source())


def test_parent_currency_and_date_after_stop_do_not_pollute_consolidated_context():
    page = HandwrittenPage([(0, "合并资产负债表"), (20, "2025年12月31日"), (40, "单位：元 币种：人民币"),
                            (80, "母公司资产负债表"), (100, "币种：美元"), (120, "2024年12月31日")])
    pages, context = statement._section(HandwrittenPdf([page]), section_source())
    assert pages[0]["bottom"] == 80 and context["currency"] == "CNY"
    assert context["statement_date"]["line"] == 2 and context["unit_source"]["line"] == 3


def test_symbolic_period_columns_cannot_borrow_parent_date_after_stop():
    table = HandwrittenTable([(["资产", "附注", "期末余额", "上年年末余额"], (40, 60))])
    page = HandwrittenPage([(45, "资产 附注 期末余额 上年年末余额"), (100, "2025年12月31日")], [table])
    part = {"page": page, "pdf_page": 1, "top": 0, "bottom": 80, "lines": statement._lines(page)}
    with pytest.raises(statement.ProbeRejected, match="symbolic_period_columns_without_explicit_current_date"):
        statement._column_header([part], 2025)


@pytest.mark.parametrize("reversed_columns", [False, True])
def test_column_headers_select_current_year_even_when_previous_year_is_left(local_fixture, reversed_columns):
    result = local_fixture.run([basic_page(reversed_columns=reversed_columns)])
    assert result["status"] == "observed"
    expected = {"total_assets": ("120", "70"), "total_liabilities": ("90", "50"), "total_equity": ("30", "20")}
    for key, (current, previous) in expected.items():
        metric = result["metrics"][key]
        assert metric["amount_reported"] == current and metric["previous_amount_reported"] == previous
        assert metric["column_header_pdf_page"] == 1
    assert result["column_header"]["current_column_zero_based"] == (3 if reversed_columns else 2)
    assert result["context"]["section_stop"]["text"] == "母公司资产负债表"


@pytest.mark.parametrize("parent_equity", ["25", "30"])
def test_full_equity_label_wins_even_when_parent_attributable_value_is_equal(local_fixture, parent_equity):
    result = local_fixture.run([basic_page(parent_equity=parent_equity)])
    equity = result["metrics"]["total_equity"]
    assert equity["amount_reported"] == "30" and equity["previous_amount_reported"] == "20"
    assert equity["label_cell"] == "所有者权益（或股东权\n益）合计"
    assert [line["line"] for line in equity["text_lines"]] == [8, 9, 10]
    assert equity["table_row"] == 5


def test_parent_attributable_equity_cannot_replace_absent_total_equity(local_fixture):
    result = local_fixture.run([basic_page(parent_equity="30", total_equity=False)])
    assert result["status"] == "rejected" and result["error_code"] == "required_total_missing_or_duplicate"
    assert result["metrics"] == {}


@pytest.mark.parametrize("label", ["流动负债合计", "非流动负债合计", "归属于母公司所有者权益合计",
                                   "归属于母公司所有者权益（或股东权益）合计", "负债和所有者权益总计"])
def test_subtotals_parent_attributable_equity_and_combined_total_are_not_targets(label):
    assert statement._metric(label) is None


def test_duplicate_target_is_not_resolved_by_first_match(local_fixture):
    page = basic_page()
    duplicate = HandwrittenTable([(["资产总计", "", "999", "888"], (160, 180))])
    page.tables.append(duplicate)
    page.lines.append((165, "资产总计 999 888"))
    page.lines.sort()
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "required_total_missing_or_duplicate"


def test_blank_current_cell_cannot_fall_back_to_previous_year(local_fixture):
    page = basic_page()
    page.tables[0].values[1][2] = None
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "missing_numeric_cell"


def test_thousands_normalization_preserves_strings_and_original_reported_unit(local_fixture):
    result = local_fixture.run([basic_page(unit="千元")])
    assert result["status"] == "observed" and result["context"]["unit_factor_cny"] == "1000"
    expected = {"total_assets": "120000.00", "total_liabilities": "90000.00", "total_equity": "30000.00"}
    for key, converted in expected.items():
        metric = result["metrics"][key]
        assert metric["normalized_cny"] == converted and metric["unit"] == "千元"
        assert type(metric["amount_reported"]) is str and type(metric["normalized_cny"]) is str


def test_yuan_amount_requiring_rounding_is_rejected(local_fixture):
    page = basic_page()
    page.tables[0].values[1][2] = "120.001"
    page.lines = [(top, text.replace("资产总计 120 70", "资产总计 120.001 70")) for top, text in page.lines]
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "normalized_cny_would_require_rounding"


@pytest.mark.parametrize(("cell", "expected"), [("1,234.56", "1234.56"), ("123456789012.34", "123456789012.34"),
                                                 ("(1,234.56)", "-1234.56"), ("（1,234）", "-1234"),
                                                 ("－1,234", "-1234"), ("0", "0")])
def test_money_parser_is_decimal_and_preserves_explicit_sign_and_precision(cell, expected):
    value = statement._amount(cell)
    assert type(value) is Decimal and str(value) == expected


@pytest.mark.parametrize("cell", [None, "", "-", "－", "12,34", "12345,678", "1.2.3"])
def test_missing_or_malformed_cell_is_never_zero(cell):
    with pytest.raises(statement.ProbeRejected):
        statement._amount(cell)


@pytest.mark.parametrize("column", [2, 3])
def test_header_missing_column_geometry_is_an_explicit_rejection(column):
    page = basic_page()
    page.tables[0].rows[0].cells[column] = None
    part = {"page": page, "pdf_page": 1, "top": 0, "bottom": 260, "lines": statement._lines(page)}
    with pytest.raises(statement.ProbeRejected, match="column_header_geometry_not_confirmed"):
        statement._column_header([part], 2025)


def test_changed_original_is_rejected_before_pdf_parser(local_fixture, monkeypatch):
    local_fixture.raw.write_bytes(b"modified bytes")
    monkeypatch.setattr(statement.pdfplumber, "open", lambda *_: pytest.fail("Changed bytes reached PDF parser"))
    result = statement.probe(local_fixture.source, "digest")
    assert result["status"] == "rejected" and result["error_code"] == "original_snapshot_hash_or_status_mismatch"


def test_original_path_outside_raw_tree_is_rejected_before_parser(local_fixture, monkeypatch):
    source = {**local_fixture.source, "local_relative_path": "../outside.pdf"}
    monkeypatch.setattr(statement.pdfplumber, "open", lambda *_: pytest.fail("Out-of-tree path reached parser"))
    result = statement.probe(source, "digest")
    assert result["status"] == "rejected" and result["error_code"] == "original_outside_local_raw_tree"


def test_textless_pdf_is_rejected_without_ocr_or_imputed_amounts(local_fixture):
    result = local_fixture.run([HandwrittenPage([])])
    assert result["status"] == "rejected" and result["error_code"] == "consolidated_balance_sheet_title_not_confirmed"
    assert result["metrics"] == {}


def test_merged_pdf_grid_holes_preserve_physical_columns_and_original_indices(local_fixture):
    page = basic_page()
    for index in (2, 4):
        original_row = page.tables[0].values[index]
        original_cells = page.tables[0].rows[index].cells
        page.tables[0].values[index] = [original_row[0], None, None, *original_row[1:]]
        page.tables[0].rows[index].cells = [original_cells[0], None, None, *original_cells[1:]]
    result = local_fixture.run([page])
    assert result["status"] == "observed"
    for key, expected in (("total_liabilities", "90"), ("total_equity", "30")):
        metric = result["metrics"][key]
        assert metric["amount_reported"] == expected and metric["current_table_column_zero_based"] == 4
        assert metric["previous_table_column_zero_based"] == 5 and metric["previous_cell_bbox"] == [200, *(
            (120, 280, 140) if key == "total_liabilities" else (180, 280, 240))]


def test_missing_value_in_real_cell_is_not_removed_to_shift_the_other_year(local_fixture):
    page = basic_page()
    page.tables[0].values[1][2] = None
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "missing_numeric_cell"
    assert result["metrics"] == {}


def test_text_in_nonphysical_grid_slot_is_not_silently_dropped(local_fixture):
    page = basic_page()
    page.tables[0].values[1].insert(1, "999")
    page.tables[0].rows[1].cells.insert(1, None)
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "text_in_cell_without_geometry"


def test_previous_year_column_geometry_must_match_its_header(local_fixture):
    page = basic_page()
    page.tables[0].rows[1].cells[3] = (100, 100, 200, 120)
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "continuation_amount_column_geometry_changed"


def split_equity_page(prefix="所有者权益（或股东权", suffix="益）合计"):
    """Independent 120/90/30 fixture with two physically split label cells."""
    page = basic_page(total_equity=False)
    table = page.tables[0]
    table.values.extend([["", prefix, "", "", "30", "20"], [None, suffix, None, None, None, None]])
    table.rows.extend([
        SimpleNamespace(bbox=(0, 180, 280, 220), cells=[
            (0, 180, 5, 220), (5, 180, 95, 200), (95, 180, 100, 220),
            (100, 180, 120, 220), (120, 180, 200, 220), (200, 180, 280, 220)]),
        SimpleNamespace(bbox=(5, 200, 95, 220), cells=[None, (5, 200, 95, 220), None, None, None, None]),
    ])
    page.lines.extend([(185, prefix), (195, "30 20"), (205, suffix)])
    page.lines.sort()
    return page


def test_split_equity_label_requires_two_adjacent_cells_and_original_spanning_amounts(local_fixture):
    result = local_fixture.run([split_equity_page()])
    assert result["status"] == "observed", result.get("error_code")
    equity = result["metrics"]["total_equity"]
    assert equity["amount_reported"] == "30" and equity["previous_amount_reported"] == "20"
    assert equity["label_cell"] == "所有者权益（或股东权\n益）合计"
    assert equity["table_row"] == 5 and equity["table_rows"] == [5, 6]
    assert equity["current_table_column_zero_based"] == 4 and equity["previous_table_column_zero_based"] == 5
    assert equity["current_cell_bbox"] == [120, 180, 200, 220]
    assert equity["label_fragments"] == [
        {"table_row": 5, "table_column_zero_based": 1, "text": "所有者权益（或股东权", "bbox": [5, 180, 95, 200]},
        {"table_row": 6, "table_column_zero_based": 1, "text": "益）合计", "bbox": [5, 200, 95, 220]}]
    assert [line["text"] for line in equity["text_lines"]] == ["所有者权益（或股东权", "30 20", "益）合计"]


@pytest.mark.parametrize(("prefix", "suffix"), [
    ("归属于母公司所有者权益（或股东权", "益）合计"),
    ("所有者权益（或股东权", "益）小计"),
    ("负债和所有者权益（或股东权", "益）总计"),
    ("流动负债", "合计"),
])
def test_split_parent_subtotal_or_combined_label_cannot_supply_full_equity(local_fixture, prefix, suffix):
    result = local_fixture.run([split_equity_page(prefix, suffix)])
    assert result["status"] == "rejected" and result["error_code"] == "required_total_missing_or_duplicate"
    assert result["metrics"] == {}


@pytest.mark.parametrize("mutation", [
    "different_column", "different_x_interval", "vertical_gap", "vertical_overlap", "not_adjacent_row",
    "amount_does_not_span", "previous_does_not_span", "next_has_amount", "next_has_physical_amount",
    "padding_has_text", "padding_too_wide", "broken_padding_boundary", "note_has_text",
])
def test_split_label_unsupported_geometry_and_unrelated_cells_remain_rejected(local_fixture, mutation):
    page = split_equity_page()
    table = page.tables[0]
    first, second = table.rows[-2:]
    if mutation == "different_column":
        table.values[-1][2], table.values[-1][1] = table.values[-1][1], None
        second.cells[2], second.cells[1] = second.cells[1], None
    elif mutation == "different_x_interval":
        second.cells[1] = (7, 200, 97, 220)
    elif mutation == "vertical_gap":
        second.cells[1] = (5, 202, 95, 220)
    elif mutation == "vertical_overlap":
        second.cells[1] = (5, 198, 95, 220)
    elif mutation == "not_adjacent_row":
        table.values.insert(-1, [None] * 6)
        table.rows.insert(-1, SimpleNamespace(bbox=(5, 200, 95, 220), cells=[None] * 6))
    elif mutation == "amount_does_not_span":
        first.cells[4] = (120, 180, 200, 200)
    elif mutation == "previous_does_not_span":
        first.cells[5] = (200, 180, 280, 200)
    elif mutation == "next_has_amount":
        table.values[-1][4] = "999"
    elif mutation == "next_has_physical_amount":
        second.cells[4] = (120, 200, 200, 220)
    elif mutation == "padding_has_text":
        table.values[-2][0] = "其他"
    elif mutation == "padding_too_wide":
        first.cells[0] = (-5, 180, 5, 220)
    elif mutation == "broken_padding_boundary":
        first.cells[2] = (92, 180, 100, 220)
    else:
        table.values[-2][3] = "附注注释"
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "required_total_missing_or_duplicate"
    assert result["metrics"] == {}


@pytest.mark.parametrize("column", [4, 5])
def test_split_label_real_missing_numeric_cell_is_not_reconstructed_by_balance(local_fixture, column):
    page = split_equity_page()
    page.tables[0].values[-2][column] = None
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "missing_numeric_cell"
    assert result["metrics"] == {}


def test_split_label_fragment_must_appear_inside_its_own_vertical_evidence_interval(local_fixture):
    page = split_equity_page()
    page.lines = [(top, text) for top, text in page.lines if top != 205]
    page.lines.append((245, "益）合计"))
    page.lines.sort()
    result = local_fixture.run([page])
    assert result["status"] == "rejected" and result["error_code"] == "split_label_fragment_not_confirmed_in_text_lines"
    assert result["metrics"] == {}


def test_cli_rejections_are_not_counted_as_observed_or_reported_as_success(tmp_path, monkeypatch):
    monkeypatch.setattr(statement, "ROOT", tmp_path)
    coverage = tmp_path / "docs/annual-report-coverage.json"
    coverage.parent.mkdir()
    coverage.write_text(json.dumps({"reports": [{"id": value} for value in statement.DEFAULT_IDS]}), encoding="utf-8")
    statuses = {"annual-002024-2025": "observed", "annual-600588-2025": "rejected", "annual-688111-2025": "observed"}
    monkeypatch.setattr(statement, "probe", lambda source, _: {"id": source["id"], "status": statuses[source["id"]]})
    assert statement.main([]) == 2
    result = json.loads((tmp_path / ".local/annual-statement-probe/report.json").read_text(encoding="utf-8"))
    assert result["reports_attempted"] == 3 and result["reports_observed"] == 2 and result["reports_rejected"] == 1
    assert result["runtime_agent_used"] is False and result["financial_accuracy_benchmark"] is False


def test_cli_all_local_attempts_manifest_entries_without_filtering_rejections(tmp_path, monkeypatch):
    monkeypatch.setattr(statement, "ROOT", tmp_path)
    coverage = tmp_path / "docs/annual-report-coverage.json"
    coverage.parent.mkdir()
    rows = [{"id": identity} for identity in [*statement.DEFAULT_IDS, "independent-local-example"]]
    original = json.dumps({"reports": rows}).encode()
    coverage.write_bytes(original)
    seen = []

    def run(source, digest):
        assert digest == hashlib.sha256(original).hexdigest()
        seen.append(source["id"])
        return {"id": source["id"], "status": "rejected" if source["id"] == rows[-1]["id"] else "observed"}

    monkeypatch.setattr(statement, "probe", run)
    assert statement.main(["--all-local"]) == 2
    report = json.loads((tmp_path / ".local/annual-statement-probe/report.json").read_text(encoding="utf-8"))
    assert seen == [r["id"] for r in rows] == report["requested_ids"]
    assert report["reports_attempted"] == 4 and report["reports_observed"] == 3 and report["reports_rejected"] == 1
    assert report["unprobed_reports"] == 0 and report["network_or_model_calls"] == 0
    assert report["coverage_manifest_sha256"] == hashlib.sha256(original).hexdigest()
    assert report["parser_source_sha256"] == hashlib.sha256(statement.Path(statement.__file__).read_bytes()).hexdigest()
    assert coverage.read_bytes() == original


def test_cli_rejects_duplicate_manifest_identities_before_any_pdf_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(statement, "ROOT", tmp_path)
    coverage = tmp_path / "docs/annual-report-coverage.json"
    coverage.parent.mkdir()
    coverage.write_text(json.dumps({"reports": [{"id": statement.DEFAULT_IDS[0]}] * 2}), encoding="utf-8")
    monkeypatch.setattr(statement, "probe", lambda *_: pytest.fail("Ambiguous manifest must not run a probe"))
    with pytest.raises(statement.ProbeRejected, match="Duplicate source identities"):
        statement.main(["--all-local"])
    assert not (tmp_path / ".local/annual-statement-probe/report.json").exists()


def test_cli_preserves_existing_failed_report_without_any_pdf_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(statement, "ROOT", tmp_path)
    report = tmp_path / ".local/annual-statement-probe/report.json"
    report.parent.mkdir(parents=True)
    original = b'{"status":"rejected","reason":"independent previous failure"}'
    report.write_bytes(original)
    monkeypatch.setattr(statement, "probe", lambda *_: pytest.fail("Existing report must not rerun PDF work"))
    with pytest.raises(statement.ProbeRejected, match="Existing probe report retained"):
        statement.main([])
    assert report.read_bytes() == original


# Pinned observations were transcribed from original page images and checked by
# a separate read-only extraction. These literals are not refreshed by running
# the probe. A new PDF/hash requires independent re-audit, not fixture rewriting.
REAL_CASES = (
    {"id": "annual-002024-2025", "sha256": "2ab6216190ce0602fdf17526b5178a74d5d2372127ea47efc4b81a0487bde0b6",
     "unit": "千元", "title_page": 100, "date_line": 3, "unit_line": 4,
     "totals": (("total_assets", "109846105", "119042939", 100, [51], 47),
                ("total_liabilities", "99165556", "107883826", 101, [43], 39),
                ("total_equity", "10680549", "11159113", 101, [58], 54))},
    {"id": "annual-600588-2025", "sha256": "04af3511b61d5c22cad4cfcca68bc9da88eb5144d6c22c6ee87e79c32f5c1052",
     "unit": "元", "title_page": 89, "date_line": 4, "unit_line": 6,
     "totals": (("total_assets", "21631280327", "24156835132", 90, [9], 8),
                ("total_liabilities", "12383322023", "14357357009", 90, [49], 48),
                ("total_equity", "9247958304", "9799478123", 91, [18, 19, 20], 15))},
    {"id": "annual-688111-2025", "sha256": "bf6ade124527896edb1c1be6af04a7d6b4c0eb964de42a96a77db7dbc6cee281",
     "unit": "元", "title_page": 105, "date_line": 4, "unit_line": 6,
     "totals": (("total_assets", "18155801690.14", "15912888756.23", 106, [8], 7),
                ("total_liabilities", "5302354608.80", "4476319129.69", 106, [48], 47),
                ("total_equity", "12853447081.34", "11436569626.54", 107, [17, 18, 19], 14))},
)


@pytest.mark.parametrize("case", REAL_CASES, ids=[case["id"] for case in REAL_CASES])
def test_pinned_real_report_cells_period_unit_evidence_and_immutable_original(case):
    coverage_path = statement.ROOT / "docs/annual-report-coverage.json"
    if not coverage_path.is_file():
        pytest.skip("Local annual-report coverage manifest is absent; no download is attempted")
    coverage_bytes = coverage_path.read_bytes()
    sources = {row["id"]: row for row in json.loads(coverage_bytes)["reports"]}
    if case["id"] not in sources:
        pytest.skip("Selected local report metadata is absent; no download is attempted")
    source = sources[case["id"]]
    raw = statement.ROOT / ".local/annual-reports" / source["local_relative_path"]
    if not raw.is_file():
        pytest.skip("Selected original PDF is absent; no download is attempted")
    assert source["sha256"] == case["sha256"], "Snapshot changed; independent cell re-audit is required"
    original = raw.read_bytes()
    assert hashlib.sha256(original).hexdigest() == case["sha256"]
    result = statement.probe(source, hashlib.sha256(coverage_bytes).hexdigest())
    assert result["status"] == "observed", result.get("error_code")
    assert result["context"]["unit"] == case["unit"] and result["context"]["currency"] == "CNY"
    assert result["context"]["statement_date"]["text"] == "2025年12月31日"
    assert result["context"]["statement_date"]["line"] == case["date_line"]
    assert result["context"]["unit_source"]["line"] == case["unit_line"]
    assert result["context"]["statement_title"]["pdf_page"] == case["title_page"]
    for key, current, previous, page, lines, table_row in case["totals"]:
        observation = result["metrics"][key]
        assert observation["amount_reported"] == current and observation["previous_amount_reported"] == previous
        assert observation["pdf_page"] == page and observation["table_row"] == table_row
        assert [line["line"] for line in observation["text_lines"]] == lines
        assert observation["current_cell_bbox"] and observation["table_cell_visible_in_text_lines"] is True
    assert result["raw_sha256"] == case["sha256"] and result["independent_ground_truth_checked"] is False
    assert raw.read_bytes() == original, "The read-only probe changed original bytes"


def test_pinned_yonghui_2023_split_label_original_cells_and_both_fragment_locations():
    identity = "annual-601933-2023"
    expected_hash = "e42dc09dd459be4b3b3f5b99915add27be9ea0b0c94cb55bf82b590538b5669e"
    coverage_path = statement.ROOT / "docs/annual-report-coverage.json"
    if not coverage_path.is_file():
        pytest.skip("Local annual-report coverage manifest is absent; no download is attempted")
    coverage_bytes = coverage_path.read_bytes()
    source = next((row for row in json.loads(coverage_bytes)["reports"] if row["id"] == identity), None)
    if source is None:
        pytest.skip("Selected local report metadata is absent; no download is attempted")
    raw = statement.ROOT / ".local/annual-reports" / source["local_relative_path"]
    if not raw.is_file():
        pytest.skip("Selected original PDF is absent; no download is attempted")
    original = raw.read_bytes()
    assert source["sha256"] == expected_hash and hashlib.sha256(original).hexdigest() == expected_hash
    # Hand transcribed after viewing Poppler renders of original pages 118-120;
    # the 2023/2022 all-equity totals differ from the parent-attributable row.
    expected = {
        "total_assets": ("52052037800.14", "62143216081.54", 119, 17, [18]),
        "total_liabilities": ("46117300682.58", "54486316175.91", 120, 14, [15]),
        "total_equity": ("5934737117.56", "7656899905.63", 120, 30, [32, 33, 34]),
    }
    result = statement.probe(source, hashlib.sha256(coverage_bytes).hexdigest())
    assert result["status"] == "observed", result.get("error_code")
    assert result["context"]["statement_title"]["pdf_page"] == 118
    assert result["context"]["statement_date"]["text"] == "2023年12月31日"
    assert result["context"]["statement_date"]["line"] == 10
    assert result["context"]["unit_source"]["line"] == 12
    assert result["context"]["unit"] == "元" and result["context"]["currency"] == "CNY"
    for key, (current, previous, page, row, lines) in expected.items():
        metric = result["metrics"][key]
        assert metric["amount_reported"] == current and metric["previous_amount_reported"] == previous
        assert metric["pdf_page"] == page and metric["table_row"] == row
        assert [line["line"] for line in metric["text_lines"]] == lines
    equity = result["metrics"]["total_equity"]
    assert equity["current_cell"] == "5,934,737,117.56" and equity["previous_cell"] == "7,656,899,905.63"
    assert equity["label_cell"] == "所有者权益（或股东权\n益）合计"
    assert equity["table_rows"] == [30, 31]
    assert equity["current_table_column_zero_based"] == 4 and equity["previous_table_column_zero_based"] == 5
    assert equity["label_fragments"] == [
        {"table_row": 30, "table_column_zero_based": 1, "text": "所有者权益（或股东权",
         "bbox": [89.904, 687.3541223529411, 226.244, 701.26]},
        {"table_row": 31, "table_column_zero_based": 1, "text": "益）合计",
         "bbox": [89.904, 701.26, 226.244, 715.0591022222221]},
    ]
    assert equity["amount_reported"] != "5939070640.55"  # Parent-attributable equity.
    assert equity["previous_amount_reported"] != "7465571332.26"
    assert result["independent_ground_truth_checked"] is False  # Probe output is not evaluation truth.
    assert raw.read_bytes() == original
