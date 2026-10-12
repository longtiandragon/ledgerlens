"""Official A-share annual-report acquisition, separate from Agent execution.

Discovery writes an explicit source manifest before collection is allowed.
Only public CNINFO HTTPS endpoints are used; no login, CAPTCHA workarounds or
model calls are provided. Original PDF bytes remain in the ignored local tree.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import httpx
import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
PARSER_VERSION = "annual-report-pages-v2"
YEARS = (2023, 2024, 2025)
COMPANIES = (
    ("002697", "成都红旗连锁股份有限公司", "红旗连锁", "retail"),
    ("002024", "苏宁易购集团股份有限公司", "苏宁易购", "retail"),
    ("601933", "永辉超市股份有限公司", "永辉超市", "retail"),
    ("600859", "王府井集团股份有限公司", "王府井", "retail"),
    ("600729", "重庆百货大楼股份有限公司", "重庆百货", "retail"),
    ("600415", "浙江中国小商品城集团股份有限公司", "小商品城", "retail_trade_services"),
    ("600588", "用友网络科技股份有限公司", "用友网络", "software_services"),
    ("600570", "恒生电子股份有限公司", "恒生电子", "software_services"),
    ("688111", "北京金山办公软件股份有限公司", "金山办公", "software_services"),
    ("002410", "广联达科技股份有限公司", "广联达", "software_services"),
)
PORTAL = "https://www.cninfo.com.cn"
QUERY_URL = PORTAL + "/new/hisAnnouncement/query"
STOCK_URL = PORTAL + "/new/data/szse_stock.json"
PDF_HOST = "static.cninfo.com.cn"
USER_AGENT = "LedgerLens-research/0.1 (explicit public annual reports; local reproducibility)"
MAX_BYTES = 35 * 1024 * 1024
MAX_PAGES = 600
STATEMENTS = {"balance_sheet": "合并资产负债表", "income_statement": "合并利润表",
              "cash_flow_statement": "合并现金流量表"}
STATEMENT_ROWS = {
    "balance_sheet": ("货币资金", "应收账款", "流动资产合计"),
    "income_statement": ("营业收入", "营业成本", "净利润"),
    "cash_flow_statement": ("销售商品", "购买商品", "经营活动现金流入小计"),
}
AMOUNT_CELL = re.compile(r"(?<!\S)[(（]?[-−－]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[)）]?(?!\S)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _published_on(milliseconds: int) -> str:
    # CNINFO dates are displayed in China Standard Time. Its midnight epoch is
    # the preceding UTC day; using the UTC date would misdate every announcement.
    return datetime.fromtimestamp(milliseconds / 1000, timezone(timedelta(hours=8))).date().isoformat()


def _json_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _safe_pdf_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != PDF_HOST or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.query or parsed.fragment
            or not re.fullmatch(r"/finalpage/20\d{2}-\d{2}-\d{2}/\d+\.pdf", parsed.path, re.I)):
        raise ValueError("PDF URL is outside the explicit official HTTPS allowlist")
    return url


def _base_source(code, company, short_name, sector, year) -> dict:
    return {"id": f"annual-{code}-{year}", "company_code": code, "company_name": company,
            "company_short_name": short_name, "sector": sector, "report_year": year,
            "document_kind": "full_annual_report", "official_source": "CNINFO",
            "license_status": "unspecified", "discovery_status": "unresolved"}


def discover(manifest: Path, output: Path, *, transport=None) -> dict:
    """Query a fixed ten-company scope and persist metadata before PDF collection."""
    output.mkdir(parents=True, exist_ok=True)
    sources = []
    with httpx.Client(timeout=30, follow_redirects=False, transport=transport,
                      headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(STOCK_URL)
        response.raise_for_status()
        stock_data = response.json()
        _json_write(output / "metadata" / "cninfo-stock-identifiers.json", stock_data)
        org_ids = {str(row["code"]): str(row["orgId"]) for row in stock_data.get("stockList", [])}
        for code, company, short_name, sector in COMPANIES:
            company_sources = [_base_source(code, company, short_name, sector, year) for year in YEARS]
            try:
                if code not in org_ids:
                    raise ValueError("Official organization identifier not found")
                column = "sse" if code.startswith("6") else "szse"
                parameters = {"pageNum": "1", "pageSize": "30", "column": column, "tabName": "fulltext",
                              "stock": f"{code},{org_ids[code]}", "searchkey": "年度报告", "secid": "",
                              "plate": "sh" if column == "sse" else "sz", "category": "category_ndbg_szsh;",
                              "trade": "", "seDate": "2024-01-01~2026-10-08", "sortName": "time",
                              "sortType": "desc", "isHLtitle": "false"}
                response = client.post(QUERY_URL, data=parameters)
                response.raise_for_status()
                data = response.json()
                _json_write(output / "metadata" / f"cninfo-{code}-query.json", {
                    "retrieved_at": _now(), "query_url": QUERY_URL, "parameters": parameters, "response": data})
                announcements = data.get("announcements") or []
                for source in company_sources:
                    year = source["report_year"]
                    candidates = []
                    for announcement in announcements:
                        title = re.sub(r"<[^>]+>", "", str(announcement.get("announcementTitle", "")))
                        compact = re.sub(r"\s+", "", title)
                        if (str(announcement.get("secCode")) == code and f"{year}年年度报告" in compact
                                and not any(term in compact for term in ("摘要", "英文", "更正公告", "取消", "审计"))):
                            candidates.append((announcement, title))
                    if not candidates:
                        source["error_code"] = "full_report_not_found_in_official_query"
                        continue
                    announcement, title = max(candidates, key=lambda pair: (
                        pair[0].get("announcementTime", 0), str(pair[0].get("announcementId", ""))))
                    url = _safe_pdf_url(urljoin("https://static.cninfo.com.cn/", announcement["adjunctUrl"]))
                    published = _published_on(announcement["announcementTime"])
                    announcement_id = str(announcement["announcementId"])
                    source.update(discovery_status="official_pdf_identified", announcement_title=title,
                                  published_on=published, announcement_id=announcement_id, url=url,
                                  publication_date_timezone="UTC+08:00",
                                  discovered_at=_now(), official_query_url=QUERY_URL,
                                  query_metadata_path=f".local/annual-reports/metadata/cninfo-{code}-query.json",
                                  official_announcement_url=PORTAL + "/new/disclosure/detail?" +
                                  f"stockCode={code}&announcementId={announcement_id}",
                                  full_report_selection="official annual report title; summary/English notices excluded")
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                for source in company_sources:
                    source["error_code"] = "official_query_failed"
                    source["error_type"] = type(exc).__name__
            sources.extend(company_sources)
    document = {"schema_version": "annual-report-sources-v1", "created_at": _now(),
                "scope": {"companies": 10, "years": list(YEARS), "target_reports": 30,
                          "selection": "deliberate retail/trade and software/services convenience sample",
                          "representative_industry_sample": False},
                "rights": {"license_status": "unspecified", "redistribution_permission_confirmed": False,
                           "original_pdfs_in_git": False}, "sources": sources}
    _json_write(manifest, document)
    return document


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _statement_title(page_text: str, key: str) -> tuple[int, str] | None:
    heading = STATEMENTS[key].replace("合并", r"合并(?:及(?:公司|母公司))?")
    title_pattern = re.compile(
        rf"^(?:[一二三四五六七八九十\d]+[、.．])?"
        rf"(?:20\d{{2}}年(?:度|\d{{1,2}}月\d{{1,2}}日)?)?{heading}(?:[（(]续[）)])?$"
    )
    return next(((index, _compact(line)) for index, line in enumerate(page_text.splitlines())
                 if title_pattern.fullmatch(_compact(line))), None)


def _statement_evidence(page_text: str, key: str, year: int, continuation_text: str = "") -> dict | None:
    """Recognize a standalone table heading and multiple numeric table rows.

    A combined consolidated/company table is accepted because its consolidated
    columns are explicit. Integer amounts (yuan or thousands of yuan) are valid
    numeric cells; narrative mentions and contents entries are not table titles.
    This helper locates evidence only and never interprets financial amounts.
    """
    title = _statement_title(page_text, key)
    if title is None:
        return None
    title_index, title_text = title
    lines = page_text.splitlines()[title_index:] + continuation_text.splitlines()
    # Do not borrow numeric rows from a following parent-company/other table.
    for index, line in enumerate(lines[1:], 1):
        compact_line = _compact(line)
        if re.fullmatch(r"(?:\d+[、.．])?(?:母公司|公司|合并)(?:资产负债表|利润表|现金流量表|股东权益变动表|所有者权益变动表)",
                        compact_line):
            lines = lines[:index]
            break
    compact = _compact("\n".join(lines))
    if str(year) not in compact or "单位" not in compact or "元" not in compact:
        return None
    # PDF extraction can put wrapped labels and amount cells on adjacent lines.
    # A three-line window stays within the actual row rather than using arbitrary
    # dates, percentages or prose elsewhere on the page as numeric evidence.
    rows = []
    for marker in STATEMENT_ROWS[key]:
        if any(marker in _compact(line) and len(AMOUNT_CELL.findall("\n".join(lines[index:index + 3]))) >= 2
               for index, line in enumerate(lines)):
            rows.append(marker)
    if len(rows) < 2:
        return None
    return {"heading": title_text, "numeric_row_markers": rows,
            "integer_or_decimal_cells": True, "amount_values_verified": False}


def validate_pdf(path: Path, source: dict) -> dict:
    """Locate cover and actual basic financial-statement pages, not just a TOC.

    This is document identity/completeness screening. It does not verify any
    financial amount, accounting rule, audit opinion or model answer.
    """
    with pdfplumber.open(path) as pdf:
        count = len(pdf.pages)
        if not 40 <= count <= MAX_PAGES:
            return {"valid": False, "error_code": "not_full_report_page_count", "pages": count}
        cache = {}

        def text(index):
            if index not in cache:
                cache[index] = pdf.pages[index].extract_text() or ""
            return cache[index]

        first = "\n".join(text(index) for index in range(min(6, count)))
        cover = _compact("\n".join(text(index) for index in range(min(3, count))))
        company_ok = _compact(source["company_name"]) in cover
        year_ok = f"{source['report_year']}年年度报告" in cover
        summary = "年度报告摘要" in _compact(text(0))
        if not company_ok or not year_ok or summary:
            return {"valid": False, "error_code": "cover_company_year_or_full_title_mismatch", "pages": count,
                    "company_confirmed": company_ok, "year_confirmed": year_ok, "summary_cover": summary}
        # TOC printed page numbers normally follow the financial-report section.
        # They are only search hints; acceptance requires statement headings plus
        # period and multiple numeric amounts on the actual PDF pages.
        hints = [int(value) for value in re.findall(r"财务报告[^\d\n]{0,40}(\d{1,3})", first)]
        windows = []
        for hint in hints:
            if 1 <= hint <= count:
                windows.extend(range(max(6, hint - 5), min(count, hint + 42)))
        if not windows:
            windows.extend(range(max(6, count // 4), min(count, count // 4 + 65)))
        found = {}
        evidence = {}

        def inspect(indices):
            for index in indices:
                page_text = text(index)
                for key in STATEMENTS:
                    proof = _statement_evidence(page_text, key, source["report_year"]) if key not in found else None
                    checked_pages = [index + 1]
                    if key not in found and proof is None and _statement_title(page_text, key) is not None:
                        continuation = []
                        # A title at a page bottom may have all rows on the next
                        # page. Never scan an unbounded section under that title.
                        for next_index in range(index + 1, min(count, index + 3)):
                            continuation.append(text(next_index))
                            checked_pages.append(next_index + 1)
                            proof = _statement_evidence(page_text, key, source["report_year"], "\n".join(continuation))
                            if proof is not None:
                                break
                    if proof is not None:
                        found[key] = index + 1
                        evidence[key] = {**proof, "checked_pdf_pages": checked_pages}
                if len(found) == len(STATEMENTS):
                    return

        inspect(dict.fromkeys(windows))
        if len(found) != len(STATEMENTS):
            # Missing or atypical TOCs are not treated as success. Inspect the
            # remaining pages with a finite 600-page bound and explicit outcome.
            inspect(index for index in range(6, count) if index not in cache)
        return {"valid": len(found) == len(STATEMENTS), "pages": count,
                "company_confirmed": company_ok, "year_confirmed": year_ok, "summary_cover": summary,
                "statement_pages": found, "statement_evidence": evidence, "pages_text_checked": len(cache),
                "error_code": None if len(found) == len(STATEMENTS) else "basic_statement_pages_not_confirmed",
                "validation_scope": "cover/company/year plus standalone consolidated statement headings and numeric rows",
                "amounts_or_rule_accuracy_verified": False}


def _validated_sources(manifest: dict) -> list[dict]:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ValueError("Manifest sources must be a list")
    ids, identities = set(), set()
    for source in sources:
        identity = source.get("id", "")
        code, year = source.get("company_code"), source.get("report_year")
        if (not isinstance(identity, str) or not re.fullmatch(r"annual-\d{6}-20\d{2}", identity)
                or identity != f"annual-{code}-{year}" or identity in ids or (code, year) in identities
                or not isinstance(source.get("company_name"), str)
                or source.get("document_kind") != "full_annual_report"
                or type(year) is not int or year not in YEARS):
            raise ValueError("Manifest identifiers and full-report scope must be unique and safe")
        ids.add(identity)
        identities.add((code, year))
        if source.get("discovery_status") == "official_pdf_identified":
            _safe_pdf_url(source["url"])
    return sources


def _cached_original(output: Path, previous: dict | None, source: dict) -> Path | None:
    """Reuse transferred bytes only when identity, length and digest still match."""
    if not previous or previous.get("status") not in {"verified_full_report", "downloaded_unverified", "pending"}:
        return None
    if any(previous.get(key) != source.get(key) for key in (
            "id", "url", "company_code", "company_name", "report_year", "document_kind")):
        return None
    relative = previous.get("local_relative_path")
    if not isinstance(relative, str) or not relative:
        return None
    raw = (output / relative).resolve()
    if not raw.is_relative_to((output / "raw").resolve()) or not raw.is_file():
        return None
    size = raw.stat().st_size
    if size != previous.get("bytes") or not 8 <= size <= MAX_BYTES:
        return None
    content = raw.read_bytes()
    if (hashlib.sha256(content).hexdigest() != previous.get("sha256")
            or not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]):
        return None
    return raw


def collect(manifest_path: Path, output: Path, coverage_path: Path, *, workers=2, transport=None,
            offline=False) -> dict:
    if type(workers) is not int or not 1 <= workers <= 2:
        raise ValueError("At most two download workers are permitted")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = _validated_sources(manifest)  # Validate all paths/URLs before any download.
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "acquisition-report.json"
    prior = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    previous = {row["id"]: row for row in prior.get("reports", [])}
    history_path = output / "acquisition-history.jsonl"
    results = {}

    def acquire(source):
        item = {**source, "acquisition_started_at": _now(), "status": "failed", "parser_version": PARSER_VERSION,
                "pdfplumber_version": importlib.metadata.version("pdfplumber"), "sha256": None, "bytes": None}
        if source.get("discovery_status") != "official_pdf_identified":
            item["error_code"] = source.get("error_code", "official_full_pdf_unresolved")
            return item
        old = previous.get(source["id"])
        folder = output / "raw" / source["company_code"] / str(source["report_year"])
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "download.part"
        try:
            cached = _cached_original(output, old, source)
            if cached is not None:
                if (old.get("status") == "verified_full_report" and old.get("parser_version") == PARSER_VERSION
                        and old.get("validation", {}).get("valid") is True):
                    return {**old, **source, "status": "verified_full_report",
                            "cache_reused_at": _now(), "reused_verified_bytes": True,
                            "network_requested": False}
                item.update({key: old[key] for key in ("sha256", "bytes", "downloaded_at", "final_url",
                                                      "content_type", "local_relative_path", "http_status")
                             if key in old})
                item.update(status="downloaded_unverified", reused_downloaded_bytes=True,
                            cache_reused_at=_now(), network_requested=False)
                validation = validate_pdf(cached, source)
                item.update(validation=validation, validated_at=_now(),
                            error_code=None if validation["valid"] else validation["error_code"])
                if validation["valid"]:
                    item["status"] = "verified_full_report"
                return item
            if offline:
                item.update(error_code="local_original_missing_or_changed", network_requested=False)
                return item
            url = _safe_pdf_url(source["url"])
            item["network_requested"] = True
            with httpx.Client(timeout=60, follow_redirects=False, transport=transport,
                              headers={"User-Agent": USER_AGENT}) as client:
                for _ in range(4):
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = _safe_pdf_url(urljoin(url, response.headers["location"]))
                            continue
                        item["http_status"] = response.status_code
                        response.raise_for_status()
                        digest = hashlib.sha256()
                        size = 0
                        with temporary.open("wb") as destination:
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > MAX_BYTES:
                                    raise ValueError("Source exceeds the fixed 35 MB bound")
                                destination.write(chunk)
                                digest.update(chunk)
                        content_type = response.headers.get("content-type", "")
                        break
                else:
                    raise ValueError("Official redirect limit exceeded")
            with temporary.open("rb") as stream:
                if not stream.read(8).startswith(b"%PDF-"):
                    item["error_code"] = "response_is_not_pdf"
                    return item
                stream.seek(max(0, size - 2048))
                if b"%%EOF" not in stream.read():
                    item["error_code"] = "pdf_transfer_not_complete"
                    return item
            sha = digest.hexdigest()
            raw = folder / f"{sha}.pdf"
            temporary.replace(raw)
            item.update(status="downloaded_unverified", sha256=sha, bytes=size, downloaded_at=_now(),
                        final_url=url, content_type=content_type,
                        local_relative_path=raw.relative_to(output).as_posix())
            validation = validate_pdf(raw, source)
            item["validation"] = validation
            item["validated_at"] = _now()
            if validation["valid"]:
                item["status"] = "verified_full_report"
                item["error_code"] = None
            else:
                item["error_code"] = validation["error_code"]
        except httpx.HTTPStatusError as exc:
            item.update(error_code="official_http_error", http_status=exc.response.status_code)
        except httpx.HTTPError as exc:
            item.update(error_code="official_transport_error", error_type=type(exc).__name__)
        except (ValueError, KeyError, OSError) as exc:
            item.update(error_code="acquisition_or_validation_rejected", error_type=type(exc).__name__)
        except Exception as exc:
            # Bounded parser diagnostics never emit raw documents or credentials.
            item.update(error_code="pdf_parser_failure", error_type=type(exc).__name__)
        finally:
            if temporary.exists():
                temporary.unlink()
        return item

    def persist():
        # An interrupted resume must retain prior byte identity for the next run.
        rows = [results.get(source["id"], {**source, **previous.get(source["id"], {}), "status": "pending"})
                for source in sources]
        report = {"schema_version": "annual-report-acquisition-v1", "updated_at": _now(),
                  "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                  "max_download_workers": workers, "offline": offline, "reports": rows}
        _json_write(report_path, report)
        return report

    persist()  # Pending entries survive an interrupted download or parser run.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        jobs = {executor.submit(acquire, source): source for source in sources}
        for future in as_completed(jobs):
            item = future.result()
            results[item["id"]] = item
            with history_path.open("a", encoding="utf-8") as history:
                history.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            persist()
            print(json.dumps({"id": item["id"], "status": item["status"], "bytes": item.get("bytes"),
                              "pages": item.get("validation", {}).get("pages"),
                              "error_code": item.get("error_code")}, ensure_ascii=True), flush=True)
    report = persist()
    coverage = coverage_summary(report, manifest)
    _json_write(coverage_path, coverage)
    return coverage


def coverage_summary(report: dict, manifest: dict) -> dict:
    rows = report["reports"]
    complete = [row for row in rows if row.get("status") == "verified_full_report"]
    fields = {"id", "company_code", "company_name", "company_short_name", "sector", "report_year", "status",
              "url", "final_url", "official_announcement_url", "announcement_title", "published_on", "downloaded_at",
              "sha256", "bytes", "license_status", "parser_version", "pdfplumber_version", "validation", "error_code",
              "http_status", "error_type", "local_relative_path", "reused_verified_bytes",
              "reused_downloaded_bytes", "network_requested", "validated_at", "cache_reused_at"}
    fields.add("publication_date_timezone")
    companies = []
    for code, name, short_name, sector in COMPANIES:
        company_rows = [row for row in rows if row["company_code"] == code]
        companies.append({"code": code, "company_name": name, "short_name": short_name, "sector": sector,
                          "verified_years": sorted(row["report_year"] for row in company_rows
                                                   if row.get("status") == "verified_full_report"),
                          "missing_or_unverified_years": sorted(row["report_year"] for row in company_rows
                                                                if row.get("status") != "verified_full_report")})
    return {"schema_version": "annual-report-coverage-v1", "updated_at": report["updated_at"],
            "scope": manifest.get("scope"), "target_reports": len(rows), "verified_full_reports": len(complete),
            "downloaded_but_unverified": sum(row.get("status") == "downloaded_unverified" for row in rows),
            "failed_or_pending": sum(row.get("status") in {"failed", "pending"} for row in rows),
            "total_verified_bytes": sum(row["bytes"] for row in complete),
            "total_verified_pdf_pages": sum(row["validation"]["pages"] for row in complete),
            "complete_three_year_companies": sum(len(company["verified_years"]) == 3 for company in companies),
            "source_manifest_sha256": report["source_manifest_sha256"], "max_download_workers": report["max_download_workers"],
            "offline": report.get("offline", False),
            "reports_requested_in_this_run": sum(row.get("network_requested", False) for row in rows),
            "license_status": "unspecified", "original_pdf_location": ".local/annual-reports/raw",
            "original_pdfs_in_git": False, "financial_amount_accuracy_verified": False,
            "accounting_rules_or_models_evaluated": False,
            "limitations": ["Convenience sample, not industry-representative",
                            "Only PDF identity, full-report title and actual basic-statement pages screened",
                            "No professional accounting-rule, amount, anomaly-cause or model-accuracy validation",
                            "Public disclosure does not establish redistribution permission"],
            "companies": companies, "reports": [{key: value for key, value in row.items() if key in fields} for row in rows]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("discover", "collect"))
    parser.add_argument("--manifest", type=Path, default=ROOT / "docs/annual-report-sources.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/annual-reports")
    parser.add_argument("--coverage", type=Path, default=ROOT / "docs/annual-report-coverage.json")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--offline", action="store_true", help="Validate cached originals; do not request PDFs")
    args = parser.parse_args(argv)
    expected_root = (ROOT / ".local/annual-reports").resolve()
    if not args.output.resolve().is_relative_to(expected_root):
        raise ValueError("CLI originals must stay inside the workspace .local/annual-reports tree")
    if args.action == "discover":
        if args.offline:
            parser.error("Discovery queries the official portal and cannot run offline")
        data = discover(args.manifest, args.output)
        summary = {"targets": len(data["sources"]), "official_pdf_identified": sum(
            source["discovery_status"] == "official_pdf_identified" for source in data["sources"])}
    else:
        data = collect(args.manifest, args.output, args.coverage, workers=args.workers, offline=args.offline)
        summary = {key: data[key] for key in ("target_reports", "verified_full_reports", "downloaded_but_unverified",
                                             "failed_or_pending", "total_verified_bytes")}
    print(json.dumps(summary, ensure_ascii=True))
    if args.action == "collect":
        return 0 if data["verified_full_reports"] == data["target_reports"] else 2
    return 0 if summary["official_pdf_identified"] == summary["targets"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
