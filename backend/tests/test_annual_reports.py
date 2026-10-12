"""Independent offline checks for full-report acquisition and identity screening."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from datasets import annual_reports as annual


def source(code="002697", year=2024):
    return {**annual._base_source(code, "成都红旗连锁股份有限公司", "红旗连锁", "retail", year),
            "discovery_status": "official_pdf_identified",
            "url": f"https://static.cninfo.com.cn/finalpage/2025-04-19/{year}{code}.PDF"}


def manifest(tmp_path, sources):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"scope": {"target_reports": len(sources)}, "sources": sources}), encoding="utf-8")
    return path


def valid_screen():
    return {"valid": True, "pages": 139, "company_confirmed": True, "year_confirmed": True,
            "statement_pages": {"balance_sheet": 65, "income_statement": 69, "cash_flow_statement": 72}}


def test_official_publication_date_uses_china_timezone_not_utc_day():
    assert annual._published_on(1711987200000) == "2024-04-02"


@pytest.mark.parametrize("url", [
    "http://static.cninfo.com.cn/finalpage/2025-04-19/123.PDF",
    "https://static.cninfo.com.cn.attacker.test/finalpage/2025-04-19/123.PDF",
    "https://user:password@static.cninfo.com.cn/finalpage/2025-04-19/123.PDF",
    "https://static.cninfo.com.cn:8443/finalpage/2025-04-19/123.PDF",
    "https://static.cninfo.com.cn/finalpage/2025-04-19/123.PDF?token=secret",
    "https://static.cninfo.com.cn/directory.html",
])
def test_only_explicit_official_pdf_urls_are_accepted(url):
    with pytest.raises(ValueError, match="allowlist"):
        annual._safe_pdf_url(url)


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.append(deepcopy(rows[0])),
    lambda rows: rows[0].update(id="../escaped"),
    lambda rows: rows[0].update(company_code="../escaped"),
    lambda rows: rows[0].update(document_kind="annual_report_summary"),
    lambda rows: rows[0].update(url="https://attacker.test/source.PDF"),
])
def test_invalid_manifest_is_rejected_before_output_or_network(tmp_path, mutation):
    rows = [source()]
    mutation(rows)
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500))
    output = tmp_path / "originals"
    with pytest.raises(ValueError):
        annual.collect(manifest(tmp_path, rows), output, tmp_path / "coverage.json", transport=transport)
    assert not calls and not output.exists()


def test_discovery_selects_full_report_and_persists_official_query_before_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(annual, "COMPANIES", (("002697", "成都红旗连锁股份有限公司", "红旗连锁", "retail"),))

    def handler(request):
        if request.url.path.endswith("szse_stock.json"):
            return httpx.Response(200, json={"stockList": [{"code": "002697", "orgId": "9900023218"}]})
        assert request.method == "POST" and "9900023218" in request.content.decode()
        announcements = []
        for year in annual.YEARS:
            for suffix, identifier in (("摘要", str(year) + "1"), ("", str(year) + "2")):
                announcements.append({"secCode": "002697", "announcementTitle": f"{year}年年度报告{suffix}",
                                      "announcementId": identifier, "announcementTime": 1711987200000,
                                      "adjunctUrl": f"finalpage/2024-04-02/{identifier}.PDF"})
        return httpx.Response(200, json={"announcements": announcements})

    output = tmp_path / "local"
    data = annual.discover(tmp_path / "sources.json", output, transport=httpx.MockTransport(handler))
    assert len(data["sources"]) == 3
    assert all(row["discovery_status"] == "official_pdf_identified" for row in data["sources"])
    assert all("摘要" not in row["announcement_title"] for row in data["sources"])
    assert all(row["published_on"] == "2024-04-02" and row["license_status"] == "unspecified"
               for row in data["sources"])
    assert (tmp_path / "sources.json").is_file()
    assert (output / "metadata/cninfo-002697-query.json").is_file()
    assert not list(output.rglob("*.pdf"))


class FakePage:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class FakePdf:
    def __init__(self, pages):
        self.pages = [FakePage(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def handwritten_pages():
    pages = [""] * 40
    pages[0] = "成都红旗连锁股份有限公司\n2024 年年度报告"
    pages[3] = "第十节 财务报告 ............. 20\n合并资产负债表 1\n合并利润表 2\n合并现金流量表 3"
    # Independently authored table rows, not parser-produced financial truth.
    tables = (
        "货币资金 1,234.56 789.01\n应收账款 900.00 800.00",
        "营业收入 1,234.56 789.01\n营业成本 900.00 800.00",
        "销售商品、提供劳务收到的现金 1,234.56 789.01\n购买商品、接受劳务支付的现金 900.00 800.00",
    )
    for index, heading, rows in zip((21, 23, 25), annual.STATEMENTS.values(), tables):
        pages[index] = f"{heading}\n2024年度\n单位：元\n{rows}"
    return pages


def test_full_pdf_screen_requires_real_statement_pages_beyond_contents(monkeypatch):
    pages = handwritten_pages()
    monkeypatch.setattr(annual.pdfplumber, "open", lambda _: FakePdf(pages))
    result = annual.validate_pdf(SimpleNamespace(), source())
    assert result["valid"] is True
    assert result["statement_pages"] == {"balance_sheet": 22, "income_statement": 24, "cash_flow_statement": 26}
    assert result["amounts_or_rule_accuracy_verified"] is False
    pages[21] = pages[23] = pages[25] = ""
    result = annual.validate_pdf(SimpleNamespace(), source())
    assert result["valid"] is False and result["error_code"] == "basic_statement_pages_not_confirmed"


@pytest.mark.parametrize("cover", ["成都红旗连锁股份有限公司 2024 年年度报告摘要",
                                   "成都红旗连锁股份有限公司 2023 年年度报告",
                                   "另一家公司 2024 年年度报告"])
def test_summary_wrong_company_or_year_is_not_full_report(monkeypatch, cover):
    pages = handwritten_pages()
    pages[0] = cover
    monkeypatch.setattr(annual.pdfplumber, "open", lambda _: FakePdf(pages))
    assert annual.validate_pdf(SimpleNamespace(), source())["valid"] is False


def test_downloaded_pdf_hash_size_and_failures_survive_resume(tmp_path, monkeypatch):
    payload = b"%PDF-1.7\nHandwritten transport fixture\n%%EOF"
    monkeypatch.setattr(annual, "validate_pdf", lambda *_: valid_screen())
    one, two = source(), source("002024")

    def handler(request):
        return (httpx.Response(403) if "002024" in request.url.path
                else httpx.Response(200, content=payload, headers={"content-type": "application/pdf"}))

    path = manifest(tmp_path, [one, two])
    output = tmp_path / "local"
    coverage = annual.collect(path, output, tmp_path / "coverage.json", transport=httpx.MockTransport(handler))
    assert coverage["verified_full_reports"] == 1 and coverage["failed_or_pending"] == 1
    downloaded = next(row for row in coverage["reports"] if row["status"] == "verified_full_report")
    assert downloaded["sha256"] == hashlib.sha256(payload).hexdigest() and downloaded["bytes"] == len(payload)
    assert (output / downloaded["local_relative_path"]).read_bytes() == payload
    calls = []
    again = annual.collect(path, output, tmp_path / "coverage.json", transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(403)))
    assert len(calls) == 1 and again["verified_full_reports"] == 1 and again["failed_or_pending"] == 1
    assert len((output / "acquisition-history.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    assert again["license_status"] == "unspecified" and again["accounting_rules_or_models_evaluated"] is False


@pytest.mark.parametrize("payload,error", [(b"<html>Access Denied</html>", "response_is_not_pdf"),
                                           (b"%PDF-1.7\ntruncated", "pdf_transfer_not_complete")])
def test_http_200_nonpdf_and_truncated_body_remain_failed(tmp_path, payload, error):
    coverage = annual.collect(manifest(tmp_path, [source()]), tmp_path / "local", tmp_path / "coverage.json",
                              transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload)))
    assert coverage["verified_full_reports"] == 0
    assert coverage["reports"][0]["error_code"] == error
    assert not list((tmp_path / "local").rglob("*.pdf"))


def test_official_redirect_cannot_leave_allowlist(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://attacker.test/file.PDF"})

    coverage = annual.collect(manifest(tmp_path, [source()]), tmp_path / "local", tmp_path / "coverage.json",
                              transport=httpx.MockTransport(handler))
    assert len(calls) == 1 and coverage["verified_full_reports"] == 0


def test_parser_failure_retains_original_but_cannot_be_reported_as_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(annual, "validate_pdf", lambda *_: (_ for _ in ()).throw(ValueError("unreadable PDF")))
    coverage = annual.collect(manifest(tmp_path, [source()]), tmp_path / "local", tmp_path / "coverage.json",
                              transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"%PDF-1.7\n%%EOF")))
    assert coverage["downloaded_but_unverified"] == 1 and coverage["verified_full_reports"] == 0
    assert len(list((tmp_path / "local").rglob("*.pdf"))) == 1


def test_more_than_two_workers_are_rejected_before_download(tmp_path):
    with pytest.raises(ValueError, match="two"):
        annual.collect(manifest(tmp_path, [source()]), tmp_path / "local", tmp_path / "coverage.json", workers=3)


@pytest.mark.parametrize("heading", ["合并资产负债表", "2024年12月31日合并及公司资产负债表"])
def test_integer_thousands_and_combined_consolidated_columns_are_valid_evidence(heading):
    text = f"{heading}\n2024年度\n金额单位为人民币千元\n货币资金 12,345 67,890\n应收账款 (1,234) -5,678"
    proof = annual._statement_evidence(text, "balance_sheet", 2024)
    assert proof and proof["numeric_row_markers"] == ["货币资金", "应收账款"]
    assert proof["amount_values_verified"] is False


def test_wrapped_cash_flow_rows_still_reference_actual_numeric_table():
    text = ("合并现金流量表\n2024年度\n单位：元\n销售商品、提供劳务收到的\n"
            "12,345 67,890\n现金\n经营活动现金流入小计 23,456 78,901")
    assert annual._statement_evidence(text, "cash_flow_statement", 2024) is not None


def test_statement_title_at_page_bottom_uses_bounded_next_page_evidence(monkeypatch):
    pages = handwritten_pages()
    pages[25] = "合并现金流量表\n2024年度\n单位：元"
    pages[26] = "销售商品收到的现金 12,345 67,890\n购买商品支付的现金 23,456 78,901"
    monkeypatch.setattr(annual.pdfplumber, "open", lambda _: FakePdf(pages))
    result = annual.validate_pdf(SimpleNamespace(), source())
    assert result["valid"] is True and result["statement_pages"]["cash_flow_statement"] == 26
    assert result["statement_evidence"]["cash_flow_statement"]["checked_pdf_pages"] == [26, 27]


def test_consolidated_title_cannot_borrow_parent_company_table_rows():
    title = "合并现金流量表\n2024年度\n单位：元"
    parent = "母公司现金流量表\n销售商品收到的现金 12,345 67,890\n购买商品支付的现金 23,456 78,901"
    assert annual._statement_evidence(title, "cash_flow_statement", 2024, parent) is None


def test_statement_rows_three_pages_after_title_are_not_accepted(monkeypatch):
    pages = handwritten_pages()
    pages[25] = "合并现金流量表\n2024年度\n单位：元"
    pages[28] = "销售商品收到的现金 12,345 67,890\n购买商品支付的现金 23,456 78,901"
    monkeypatch.setattr(annual.pdfplumber, "open", lambda _: FakePdf(pages))
    assert annual.validate_pdf(SimpleNamespace(), source())["valid"] is False


@pytest.mark.parametrize("title", ["本公司合并资产负债表的情况", "合并资产负债表 .......... 110"])
def test_narrative_and_contents_cannot_impersonate_statement_heading(title):
    text = f"{title}\n2024年度\n单位：元\n货币资金 1,234.56 789.01\n应收账款 900.00 800.00"
    assert annual._statement_evidence(text, "balance_sheet", 2024) is None


def test_unverified_download_is_revalidated_offline_without_retransfer(tmp_path, monkeypatch):
    payload = b"%PDF-1.7\nIndependent transfer fixture\n%%EOF"
    rejected = {**valid_screen(), "valid": False, "error_code": "basic_statement_pages_not_confirmed"}
    monkeypatch.setattr(annual, "validate_pdf", lambda *_: rejected)
    path, output = manifest(tmp_path, [source()]), tmp_path / "local"
    first = annual.collect(path, output, tmp_path / "coverage.json", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=payload)))
    assert first["downloaded_but_unverified"] == 1
    monkeypatch.setattr(annual, "validate_pdf", lambda *_: valid_screen())
    calls = []
    second = annual.collect(path, output, tmp_path / "coverage.json", offline=True,
                            transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))
    assert not calls and second["verified_full_reports"] == 1
    assert second["reports_requested_in_this_run"] == 0
    assert second["reports"][0]["reused_downloaded_bytes"] is True
    assert second["reports"][0]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_parser_version_change_revalidates_cached_bytes_and_preserves_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(annual, "validate_pdf", lambda *_: valid_screen())
    path, output = manifest(tmp_path, [source()]), tmp_path / "local"
    annual.collect(path, output, tmp_path / "coverage.json", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"%PDF-1.7\n%%EOF")))
    monkeypatch.setattr(annual, "PARSER_VERSION", "independent-regression-new-version")
    monkeypatch.setattr(annual, "validate_pdf", lambda *_: {
        **valid_screen(), "valid": False, "error_code": "basic_statement_pages_not_confirmed"})
    result = annual.collect(path, output, tmp_path / "coverage.json", offline=True)
    assert result["verified_full_reports"] == 0 and result["downloaded_but_unverified"] == 1
    assert result["reports"][0]["error_code"] == "basic_statement_pages_not_confirmed"


def test_changed_cached_original_fails_offline_and_makes_no_network_request(tmp_path, monkeypatch):
    monkeypatch.setattr(annual, "validate_pdf", lambda *_: valid_screen())
    path, output = manifest(tmp_path, [source()]), tmp_path / "local"
    first = annual.collect(path, output, tmp_path / "coverage.json", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"%PDF-1.7\n%%EOF")))
    (output / first["reports"][0]["local_relative_path"]).write_bytes(b"changed original")
    calls = []
    result = annual.collect(path, output, tmp_path / "coverage.json", offline=True,
                            transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))
    assert not calls and result["verified_full_reports"] == 0
    assert result["reports"][0]["error_code"] == "local_original_missing_or_changed"


def test_missing_original_offline_and_cli_incomplete_return_explicit_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(annual, "ROOT", tmp_path)
    output = tmp_path / ".local/annual-reports"
    path = manifest(tmp_path, [source()])
    code = annual.main(["collect", "--offline", "--manifest", str(path), "--output", str(output),
                        "--coverage", str(tmp_path / "coverage.json")])
    assert code == 2
    row = json.loads((output / "acquisition-report.json").read_text(encoding="utf-8"))["reports"][0]
    assert row["status"] == "failed" and row["error_code"] == "local_original_missing_or_changed"
