"""Acquire MOF implementation Q&A as versioned, evaluator-only answer pairs.

Raw pages and answers stay in ignored local storage. Runtime input contains only
the question. This collector does not implement or evaluate accounting rules.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import httpx


PARSER_VERSION = "mof-official-qa-v1"
MAX_BYTES = 5 * 1024 * 1024
ALLOWED_HOSTS = {"kjs.mof.gov.cn", "www.mof.gov.cn"}
FRAMEWORKS = {"CAS", "SCAS", "BOTH"}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("Source URL must be an uncredentialed official MOF HTTPS URL")
    return url


def load_manifest(path: Path) -> list[dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != PARSER_VERSION:
        raise ValueError("Unsupported official case manifest version")
    sources = manifest["sources"]
    identities, urls = set(), set()
    for source in sources:
        identity = source["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", identity) or identity in identities:
            raise ValueError("Case IDs must be safe and unique")
        url = validate_url(source["url"])
        if url in urls:
            raise ValueError("Duplicate URL is not a separate official case")
        if (source.get("kind") != "official_answer" or source.get("framework") not in FRAMEWORKS
                or source.get("publisher") != "财政部" or not source.get("applicable_conditions")
                or not source.get("expected_question", "").startswith("问：")):
            raise ValueError("An official case needs an explicit question, framework, publisher and conditions")
        if source.get("redistribution") != "source_manifest_only":
            raise ValueError("Original source redistribution must remain disabled")
        identities.add(identity)
        urls.add(url)
    return sources


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def parse_qa(data: bytes, source: dict) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("Official case page exceeds 5 MB")
    # Current MOF templates declare UTF-8. Reject corrupt decoding rather than
    # silently replacing characters in amounts or the official conclusion.
    html = data.decode("utf-8", errors="strict")
    if "\ufffd" in html:
        raise ValueError("Source contains replacement characters")
    soup = BeautifulSoup(html, "html.parser")
    meta = {node.get("name"): node.get("content", "") for node in soup.find_all("meta")}
    title = meta.get("ArticleTitle", "").strip()
    heading = soup.select_one(".title_con")
    body = soup.select_one(".TRS_Editor")
    if not title.startswith("问：") or not heading or not body:
        raise ValueError("Page is not a concrete official implementation question and answer")
    if normalize(title) != normalize(heading.get_text(" ", strip=True)):
        raise ValueError("Question heading and article metadata disagree")
    if normalize(title) != normalize(source["expected_question"]):
        raise ValueError("Fetched page question does not match the source manifest")
    # Older official templates leave ContentSource empty or name the ministry's
    # accounting department. Preserve that observation rather than inventing a
    # missing field; validate the official site identity separately.
    content_source = meta.get("ContentSource", "").strip()
    if (meta.get("SiteName") != "中华人民共和国财政部" or meta.get("SiteIDCode") != "bm14000001"
            or content_source not in {"", "财政部", "会计司"}):
        raise ValueError("Article publisher/site identity is not the declared official publisher")
    published_at = date.fromisoformat(meta.get("PubDate", "")[:10]).isoformat()
    for tag in body(["script", "style"]):
        tag.decompose()
    answer = body.get_text(" ", strip=True)
    if not re.match(r"^答\s*[：:]", answer) or len(normalize(answer)) < 20:
        raise ValueError("Official answer is absent, too short or not explicitly delimited")
    if len(re.findall(r"(?:^|\s)答\s*[：:]", answer)) != 1:
        raise ValueError("Multiple answer sections require a separately reviewed parser")
    return {"question": title, "answer": answer, "published_at": published_at,
            "publisher": "财政部", "original_content_source": content_source,
            "publisher_verification": "official_HTTPS_origin_and_site_metadata",
            "question_locator": "meta[name=ArticleTitle], h2.title_con",
            "answer_locator": "div.TRS_Editor"}


def fetch_page(client: httpx.Client, url: str) -> tuple[bytes, str, str]:
    for _ in range(4):
        validate_url(url)
        with client.stream("GET", url) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            response.raise_for_status()
            parts, size = [], 0
            for part in response.iter_bytes():
                size += len(part)
                if size > MAX_BYTES:
                    raise ValueError("Official case page exceeds 5 MB")
                parts.append(part)
            return b"".join(parts), url, response.headers.get("content-type", "")
    raise ValueError("Official source redirect limit exceeded")


def collect_one(source: dict, output: Path, client: httpx.Client, *, attempts: int = 3) -> dict:
    item = {**source, "status": "failed", "parser_version": PARSER_VERSION,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "attempts": []}
    for attempt in range(1, attempts + 1):
        try:
            data, final_url, content_type = fetch_page(client, source["url"])
            sha = digest(data)
            # Preserve even a bad HTTP-200 page as a failed observation; never
            # make it a question/answer pair or count it as a usable case.
            raw = output / "raw" / source["id"] / f"{sha}.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(data)
            item.update(sha256=sha, bytes=len(data), final_url=final_url, raw_path=str(raw))
            pair = parse_qa(data, source)
            common = {"id": source["id"], "schema_version": PARSER_VERSION,
                      "source_url": source["url"], "source_sha256": sha,
                      "framework": source["framework"], "published_at": pair["published_at"],
                      "applicable_conditions": source["applicable_conditions"],
                      "temporal_status": "publication_verified_effective_period_unreviewed",
                      "official_case": True, "kind": "official_implementation_qa"}
            observed = {**common, "question": pair["question"],
                        "question_locator": pair["question_locator"],
                        "question_sha256": digest(pair["question"].encode("utf-8"))}
            answer = {**common, "answer": pair["answer"], "answer_locator": pair["answer_locator"],
                      "question_sha256": observed["question_sha256"],
                      "answer_sha256": digest(pair["answer"].encode("utf-8")), "evaluator_only": True}
            observed_path = output / "observed" / source["id"] / f"{sha}.json"
            answer_path = output / "answers" / source["id"] / f"{sha}.json"
            parsed_path = output / "parsed" / source["id"] / f"{sha}.json"
            atomic_json(observed_path, observed)
            atomic_json(answer_path, answer)
            atomic_json(parsed_path, [{"page": 1, "text": pair["question"] + "\n" + pair["answer"]}])
            item["attempts"].append({"attempt": attempt, "status": "collected"})
            item.update(status="fetched", content_type=content_type, published_at=pair["published_at"],
                        original_content_source=pair["original_content_source"],
                        publisher_verification=pair["publisher_verification"],
                        observed_path=str(observed_path), answer_path=str(answer_path),
                        parsed_path=str(parsed_path), question_sha256=observed["question_sha256"],
                        answer_sha256=answer["answer_sha256"],
                        temporal_status=common["temporal_status"], accounting_validation="not_run")
            break
        except (httpx.HTTPError, ValueError, UnicodeError, KeyError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            code = "http_status" if status else type(exc).__name__
            item["attempts"].append({"attempt": attempt, "status": "failed", "code": code,
                                     "http_status": status, "message": str(exc)[:500]})
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or status in {
                429, 500, 502, 503, 504}
            if not retryable or attempt == attempts:
                item["error"] = item["attempts"][-1]
                break
            time.sleep(.25 * attempt)
    return item


def collect(manifest: Path, output: Path, *, identifiers=None, transport=None,
            workers: int = 2, trust_env: bool = True, attempts: int = 3) -> list[dict]:
    if not 1 <= workers <= 4 or not 1 <= attempts <= 3:
        raise ValueError("Collector workers must be 1..4 and attempts 1..3")
    sources = load_manifest(manifest)
    if identifiers and set(identifiers) - {source["id"] for source in sources}:
        raise ValueError("Requested official case ID is not in the manifest")
    selected = [s for s in sources if not identifiers or s["id"] in identifiers]
    output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(20, connect=8), transport=transport, trust_env=trust_env,
                      follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 LedgerLens-research/0.1"}) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            reports = list(pool.map(lambda source: collect_one(source, output, client, attempts=attempts), selected))
    report_path = output / "acquisition-report.json"
    previous = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else []
    merged = {r["id"]: r for r in previous}
    with (output / "acquisition-history.jsonl").open("a", encoding="utf-8") as history:
        for report in reports:
            merged[report["id"]] = report
            history.write(json.dumps(report, ensure_ascii=False) + "\n")
    atomic_json(report_path, list(merged.values()))
    atomic_json(output / "observed" / "manifest.json", {
        "schema_version": PARSER_VERSION, "zone": "question_only", "cases": [
            {"id": item["id"], "path": str(Path(item["observed_path"]).resolve()),
             "sha256": digest(Path(item["observed_path"]).read_bytes())}
            for item in merged.values() if item["status"] == "fetched"]})
    return reports


def load_question(path: Path) -> dict:
    """Validate the question-only contract; reject raw or evaluator material."""
    value = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"id", "schema_version", "source_url", "source_sha256", "framework", "published_at",
               "applicable_conditions", "temporal_status", "official_case", "kind", "question",
               "question_locator", "question_sha256"}
    if set(value) != allowed or value["schema_version"] != PARSER_VERSION:
        raise ValueError("Question input contains missing fields or evaluator material")
    validate_url(value["source_url"])
    if (not value["question"].startswith("问：") or value["framework"] not in FRAMEWORKS
            or not value["official_case"] or value["question_sha256"] != digest(value["question"].encode("utf-8"))):
        raise ValueError("Question contract or question hash is invalid")
    return value


def verify_local_collection(manifest: Path, output: Path) -> dict:
    """Re-read original bytes and both zones without network/model access."""
    sources = {source["id"]: source for source in load_manifest(manifest)}
    reports = json.loads((output / "acquisition-report.json").read_text(encoding="utf-8"))
    root = output.resolve()
    results = []
    for item in reports:
        if item["status"] != "fetched":
            continue
        try:
            source = sources[item["id"]]
            paths = {key: Path(item[key]).resolve() for key in ("raw_path", "observed_path", "answer_path")}
            if any(not path.is_relative_to(root) for path in paths.values()):
                raise ValueError("Artifact path is outside the declared local collection")
            original = paths["raw_path"].read_bytes()
            if digest(original) != item["sha256"]:
                raise ValueError("Original page hash differs from the acquisition record")
            pair = parse_qa(original, source)
            question = load_question(paths["observed_path"])
            answer = json.loads(paths["answer_path"].read_text(encoding="utf-8"))
            if (question["question"] != pair["question"] or answer["answer"] != pair["answer"]
                    or question["source_sha256"] != item["sha256"]
                    or answer["source_sha256"] != item["sha256"]
                    or answer["question_sha256"] != question["question_sha256"]
                    or question["question_sha256"] != item["question_sha256"]
                    or digest(answer["answer"].encode("utf-8")) != answer["answer_sha256"]
                    or answer["answer_sha256"] != item["answer_sha256"]
                    or question["published_at"] != pair["published_at"]
                    or answer.get("evaluator_only") is not True
                    or any(value["id"] != item["id"] or value["framework"] != source["framework"]
                           for value in (question, answer))):
                raise ValueError("Question/answer content, identities or provenance hashes disagree")
            results.append({"id": item["id"], "passed": True})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            results.append({"id": item["id"], "passed": False, "error": type(exc).__name__,
                            "message": str(exc)[:250]})
    return {"checked": len(results), "passed": sum(item["passed"] for item in results),
            "failed": sum(not item["passed"] for item in results), "results": results,
            "scope": "source_parser_hash_and_answer_isolation_not_accounting_accuracy"}


def coverage(reports: list[dict], *, historical_reports: list[dict] | None = None) -> dict:
    items = []
    for report in reports:
        # Publication dates and hashes, not question/answer bodies, may be
        # redistributed. A fetched pair is not a passed accounting test.
        items.append({key: report[key] for key in ("id", "title", "url", "framework", "status",
                     "applicable_conditions", "sha256", "bytes", "published_at", "parser_version",
                     "question_sha256", "answer_sha256", "accounting_validation", "retrieved_at") if key in report})
        for key in ("original_content_source", "publisher_verification"):
            if key in report:
                items[-1][key] = report[key]
        items[-1]["attempts"] = report["attempts"]
    attempt_rows = [attempt for item in (historical_reports or reports) for attempt in item["attempts"]]
    failures = [attempt for attempt in attempt_rows if attempt["status"] == "failed"]
    failure_codes = {}
    for failure in failures:
        code = str(failure.get("http_status") or failure["code"])
        failure_codes[code] = failure_codes.get(code, 0) + 1
    return {"schema_version": PARSER_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "collected_official_qa_count": sum(r["status"] == "fetched" for r in reports),
            "failed_source_count": sum(r["status"] != "fetched" for r in reports),
            "official_accounting_tests_passed": None, "executable_rule_coverage": "not_evaluated",
            "runtime_answer_indexed_count": 0, "question_answer_separation": "separate_local_directories",
            "access_control": "logical_contract_only_no_OS_permission_boundary",
            "redistribution": "metadata_and_locators_only_originals_stored_locally",
            "acquisition_attempt_history": {"attempts": len(attempt_rows), "failures": len(failures),
                                            "failure_codes": failure_codes},
            "limitations": ["These are policy Q&A, not complete ledgers or independently labelled anomaly investigations.",
                            "Publication date is recorded; effective intervals and later replacements still need review.",
                            "No official conclusion has been converted into an executable accounting rule by this collector.",
                            "Original HTML and evaluator answers must not be added to the runtime reference index."],
            "sources": items}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("docs/official-case-sources.json"))
    parser.add_argument("--output", type=Path, default=Path(".local/official-cases"))
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--direct-network", action="store_true", help="Do not use HTTP proxy environment settings")
    parser.add_argument("--verify-only", action="store_true", help="Recheck local bytes and answer separation without HTTP")
    args = parser.parse_args()
    if not args.verify_only:
        collect(args.manifest, args.output, identifiers=args.ids, workers=args.workers,
                trust_env=not args.direct_network, attempts=args.attempts)
    reports = json.loads((args.output / "acquisition-report.json").read_text(encoding="utf-8"))
    historical = [json.loads(row) for row in (args.output / "acquisition-history.jsonl").read_text(
        encoding="utf-8").splitlines() if row]
    summary = coverage(reports, historical_reports=historical)
    summary["local_artifact_verification"] = verify_local_collection(args.manifest, args.output)
    if args.coverage:
        atomic_json(args.coverage, summary)
    print(json.dumps({key: summary[key] for key in ("collected_official_qa_count", "failed_source_count",
                                                  "official_accounting_tests_passed", "local_artifact_verification")},
                     ensure_ascii=False))
    return 1 if summary["failed_source_count"] or summary["local_artifact_verification"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
