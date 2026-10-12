"""Acquire the real BPI2019 XES and a reproducible complete-trace subset.

Only metadata/statistics are public. Event traces are not journals, currencies
or anomaly truth; source fields retain their original names and string values.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from xml.etree.ElementTree import ParseError

import httpx

from backend.app.connectors import read_bpi_xes


SCHEMA_VERSION = "bpi2019-complete-traces-v1"
ARTICLE_ID = 12715853
FILE_ID = 24072995
DOI = "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
EXPECTED_MD5 = "4eb909242351193a61e1c15b9c3cc814"
EXPECTED_BYTES = 728558522
MAX_BYTES = 800_000_000
MAX_METADATA_BYTES = 2_000_000
FILE_NAME = "BPI_Challenge_2019.xes"
METADATA_URLS = (
    "https://data.4tu.nl/v2/articles/12715853",
    "https://api.figshare.com/v2/articles/12715853",
)
LIMITATIONS = [
    "This is a real anonymised purchase-to-pay event log, not a general ledger.",
    "Only the first requested complete traces in source order are selected; this is not random sampling.",
    "No accounting framework, currency, journals or anomaly root-cause labels are inferred.",
    "Trace parsing is component validation, not Chinese accounting accuracy or business impact validation.",
]


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def preserve_attempt(directory: Path, report: dict) -> None:
    """Keep successful and failed acquisition attempts in ignored local data."""
    identity = hashlib.sha256(report["started_at"].encode()).hexdigest()[:24]
    atomic_json(directory / "attempts" / (identity + ".json"), report)


def file_hashes(path: Path) -> dict:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)  # published integrity checksum, not authentication
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return {"bytes": size, "sha256": sha256.hexdigest(), "md5": md5.hexdigest()}


def validate_metadata(value: object) -> dict:
    if not isinstance(value, dict) or value.get("id") != ARTICLE_ID:
        raise ValueError("BPI2019 article identity mismatch")
    if value.get("title") != "BPI Challenge 2019" or value.get("doi") != DOI:
        raise ValueError("BPI2019 title or DOI mismatch")
    license_info = value.get("license")
    if not isinstance(license_info, dict) or license_info.get("name") != "CC BY 4.0" or (
            license_info.get("url") != "https://creativecommons.org/licenses/by/4.0/"):
        raise ValueError("BPI2019 license must be explicitly CC BY 4.0")
    files = value.get("files")
    if not isinstance(files, list):
        raise ValueError("BPI2019 file list missing")
    matches = [item for item in files if isinstance(item, dict) and item.get("id") == FILE_ID]
    if len(matches) != 1:
        raise ValueError("BPI2019 XES file identity ambiguous or missing")
    item = matches[0]
    if item.get("name") != FILE_NAME or type(item.get("size")) is not int or item["size"] != EXPECTED_BYTES:
        raise ValueError("BPI2019 published file name or byte count changed")
    if item.get("computed_md5") != EXPECTED_MD5:
        raise ValueError("BPI2019 published computed checksum changed")
    # Figshare omits is_incomplete; record that as unknown. Byte count and
    # official MD5 are still mandatory and checked after the full transfer.
    if item.get("is_link_only") is not False or ("is_incomplete" in item and item["is_incomplete"] is not False):
        raise ValueError("BPI2019 source file is incomplete or link-only")
    url = item.get("download_url")
    if not isinstance(url, str):
        raise ValueError("BPI2019 download URL missing")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"data.4tu.nl", "ndownloader.figshare.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("BPI2019 download URL is not an uncredentialed official HTTPS source")
    if parsed.hostname == "ndownloader.figshare.com" and parsed.path != f"/files/{FILE_ID}":
        raise ValueError("BPI2019 downloader file identity mismatch")
    return {"article_id": ARTICLE_ID, "file_id": FILE_ID, "title": value["title"], "doi": DOI,
            "license": {"name": license_info["name"], "url": license_info["url"]},
            "name": FILE_NAME, "expected_bytes": item["size"], "expected_md5": item["computed_md5"],
            "download_url": url, "version": value.get("version"), "published_date": value.get("published_date"),
            "published_file_complete": None if "is_incomplete" not in item else not item["is_incomplete"],
            "creators": [a["full_name"] for a in value.get("authors", [])
                         if isinstance(a, dict) and isinstance(a.get("full_name"), str)]}


def read_metadata(client: httpx.Client, url: str) -> tuple[dict, bytes]:
    if url not in METADATA_URLS:
        raise ValueError("Unknown official metadata endpoint")
    chunks, size = [], 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_METADATA_BYTES:
                raise ValueError("BPI2019 metadata exceeds byte limit")
            chunks.append(chunk)
    raw = b"".join(chunks)
    value = json.loads(raw.decode("utf-8"))
    return validate_metadata(value), raw


def download_file(client: httpx.Client, metadata: dict, directory: Path, report: dict,
                  persist, *, max_bytes: int = MAX_BYTES, deadline_seconds: float = 900) -> Path:
    """Bounded stream with byte-exact resumable ranges, then integrity checks.

    A part file is never a successful raw source. Resume requires a matching
    Content-Range; a server ignoring Range starts a new download from zero.
    """
    if type(max_bytes) is not int or max_bytes <= 0 or metadata["expected_bytes"] > max_bytes:
        raise ValueError("Published XES exceeds configured download byte limit")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / FILE_NAME
    partial = directory / (FILE_NAME + ".part")
    expected_size = metadata["expected_bytes"]
    if destination.exists():
        facts = file_hashes(destination)
        if facts["bytes"] != expected_size or facts["md5"] != metadata["expected_md5"]:
            raise ValueError("Existing raw XES does not match the official integrity checksum")
        report["download"] = {**facts, "status": "verified_existing", "md5_matches_official": True}
        persist()
        return destination
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected_size or offset > max_bytes:
        raise ValueError("Partial XES exceeds expected size")
    if offset == expected_size:
        facts = file_hashes(partial)
        if facts["md5"] != metadata["expected_md5"]:
            raise ValueError("Complete partial file fails official MD5")
        partial.replace(destination)
        report["download"] = {**facts, "status": "verified_existing", "md5_matches_official": True}
        persist()
        return destination
    report["download"] = {"status": "downloading", "bytes": offset, "md5_matches_official": False,
                          "source_url": metadata["download_url"], "byte_limit": max_bytes}
    persist()
    start = time.monotonic()
    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        with client.stream("GET", metadata["download_url"], headers=headers) as response:
            response.raise_for_status()
            if response.status_code == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", ""))
                if not match or tuple(map(int, match.groups())) != (offset, expected_size - 1, expected_size):
                    raise ValueError("Download range does not match the complete remaining source")
            elif response.status_code == 200:
                offset = 0
            else:
                raise ValueError("Unexpected download response status")
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) != expected_size - offset:
                raise ValueError("Download declared length differs from official metadata")
            if response.headers.get("content-encoding", "identity") not in {"identity", ""}:
                raise ValueError("Compressed transfer prevents byte-exact source checksum verification")
            with partial.open("ab" if offset else "wb") as handle:
                size, last_persisted = offset, offset
                for chunk in response.iter_bytes(1024 * 1024):
                    if time.monotonic() - start > deadline_seconds:
                        raise TimeoutError("BPI2019 download deadline exceeded")
                    if size + len(chunk) > max_bytes or size + len(chunk) > expected_size:
                        raise ValueError("Stream exceeded the published or configured byte limit")
                    handle.write(chunk)
                    size += len(chunk)
                    report["download"]["bytes"] = size
                    if size - last_persisted >= 32 * 1024 * 1024:
                        handle.flush()
                        persist()
                        last_persisted = size
            if size != expected_size:
                raise ValueError("XES transfer ended before the published byte count")
        facts = file_hashes(partial)
        if facts["bytes"] != expected_size or facts["md5"] != metadata["expected_md5"]:
            raise ValueError("Downloaded XES does not match the official MD5")
        partial.replace(destination)
        report["download"] = {**report["download"], **facts, "status": "verified",
                              "md5_matches_official": True}
        persist()
        return destination
    except Exception as exc:
        report["download"]["status"] = "failed"
        report["download"]["bytes"] = partial.stat().st_size if partial.exists() else 0
        report["download"]["md5_matches_official"] = False
        if isinstance(exc, httpx.HTTPStatusError):
            report["download"]["http_status"] = exc.response.status_code
        persist()
        raise


def trace_statistics(traces: list[dict], case_limit: int) -> dict:
    if type(case_limit) is not int or not 1 <= case_limit <= 10000:
        raise ValueError("Trace limit must be between 1 and 10000")
    if len(traces) != case_limit:
        raise ValueError("Source does not contain the requested number of complete traces")
    identities = set()
    event_counts = []
    for trace in traces:
        identity = trace.get("case_id")
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError("Trace identity is missing or duplicated")
        identities.add(identity)
        if trace.get("complete_trace") is not True or trace.get("source") != "BPI2019":
            raise ValueError("Subset contains an incomplete or foreign trace")
        if trace.get("accounting_framework") is not None:
            raise ValueError("Accounting framework must not be invented")
        if not isinstance(trace.get("attributes"), dict) or not isinstance(trace.get("events"), list):
            raise ValueError("Trace attributes or events missing")
        for fields in [trace["attributes"], *trace["events"]]:
            if not isinstance(fields, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                   for k, v in fields.items()):
                raise ValueError("Source XES attribute keys and values must remain explicit strings")
        if trace["attributes"].get("concept:name") != identity:
            raise ValueError("Trace identity disagrees with its source attributes")
        event_counts.append(len(trace["events"]))
    return {"complete_traces": len(traces), "events": sum(event_counts),
            "minimum_events_per_trace": min(event_counts), "maximum_events_per_trace": max(event_counts),
            "selection": "first_complete_traces_in_source_order", "requested_traces": case_limit,
            "accounting_framework": None, "currency_inferred": False, "journals_inferred": False,
            "anomaly_truth_available": False}


def write_subset(path: Path, output: Path, case_limit: int) -> dict:
    if type(case_limit) is not int or not 1 <= case_limit <= 10000:
        raise ValueError("Trace limit must be between 1 and 10000")
    traces = list(read_bpi_xes(path, case_limit=case_limit))
    statistics = trace_statistics(traces, case_limit)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for trace in traces:
            handle.write(json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(output)
    return {**statistics, **file_hashes(output), "parser": "backend.app.connectors.read_bpi_xes"}


def verify_local(directory: Path, coverage: Path) -> dict:
    report = json.loads(coverage.read_text(encoding="utf-8"))
    if report.get("schema_version") != SCHEMA_VERSION or report.get("status") != "collected":
        raise ValueError("Coverage is not a completed BPI2019 acquisition")
    metadata = []
    for source in report["metadata_sources"]:
        if source["status"] != "fetched":
            continue
        if source["url"] not in METADATA_URLS:
            raise ValueError("Unknown recorded metadata source")
        raw = (directory / ("metadata-4tu.json" if source["url"] == METADATA_URLS[0]
                            else "metadata-figshare.json")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("Saved official metadata hash changed")
        metadata.append(validate_metadata(json.loads(raw)))
    if not metadata:
        raise ValueError("No verified official metadata")
    raw_facts = file_hashes(directory / FILE_NAME)
    if raw_facts["bytes"] != EXPECTED_BYTES or raw_facts["md5"] != EXPECTED_MD5 or (
            raw_facts["sha256"] != report["download"]["sha256"]):
        raise ValueError("Raw XES does not match verified acquisition")
    subset_path = directory / "subset.jsonl"
    subset_facts = file_hashes(subset_path)
    if any(subset_facts[key] != report["subset"][key] for key in ("sha256", "bytes", "md5")):
        raise ValueError("Saved trace subset hash changed")
    traces = [json.loads(line) for line in subset_path.read_text(encoding="utf-8").splitlines()]
    statistics = trace_statistics(traces, report["subset"]["requested_traces"])
    original = list(read_bpi_xes(directory / FILE_NAME, case_limit=len(traces)))
    if traces != original or any(statistics[k] != report["subset"][k] for k in statistics):
        raise ValueError("Subset does not exactly preserve the first complete source traces")
    return {"status": "verified", "metadata_sources": len(metadata), "raw_md5_matches_official": True,
            "raw_sha256_matches": True, "subset_sha256_matches": True, **statistics}


def collect(directory: Path, coverage: Path, *, case_limit: int = 1000, client=None) -> dict:
    if type(case_limit) is not int or not 1 <= case_limit <= 10000:
        raise ValueError("Trace limit must be between 1 and 10000")
    directory.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": SCHEMA_VERSION, "started_at": utc_now(), "status": "collecting",
              "article_id": ARTICLE_ID, "file_id": FILE_ID, "doi": DOI, "title": "BPI Challenge 2019",
              "metadata_sources": [], "limitations": LIMITATIONS,
              "redistribution": "metadata_statistics_only_raw_and_subset_in_ignored_local_storage"}
    def persist():
        atomic_json(coverage, report)
    persist()
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(40, connect=30), follow_redirects=True)
    verified = []
    try:
        for url in METADATA_URLS:
            try:
                metadata, raw = read_metadata(client, url)
                path = directory / ("metadata-4tu.json" if url == METADATA_URLS[0] else "metadata-figshare.json")
                path.write_bytes(raw)
                report["metadata_sources"].append({"url": url, "status": "fetched", "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(), "retrieved_at": utc_now()})
                verified.append(metadata)
            except (httpx.HTTPError, ValueError) as exc:
                failure = {"url": url, "status": "failed", "error_type": type(exc).__name__}
                if isinstance(exc, httpx.HTTPStatusError):
                    failure["http_status"] = exc.response.status_code
                report["metadata_sources"].append(failure)
            persist()
        if not verified:
            raise ValueError("No usable official BPI2019 metadata")
        # Prefer Figshare's file endpoint only when it was verified from the
        # official Figshare metadata; do not silently invent a mirror URL.
        metadata = next((m for m in verified if urlparse(m["download_url"]).hostname == "ndownloader.figshare.com"),
                        verified[0])
        report["source"] = metadata
        raw_path = download_file(client, metadata, directory, report, persist)
        report["subset"] = write_subset(raw_path, directory / "subset.jsonl", case_limit)
        report["status"] = "collected"
    except (httpx.HTTPError, ValueError, OSError, TimeoutError, ParseError) as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["subset"] = None
    finally:
        report["finished_at"] = utc_now()
        persist()
        preserve_attempt(directory, report)
        if own:
            client.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".local/bpi2019"))
    parser.add_argument("--coverage", type=Path, default=Path("docs/bpi2019-coverage.json"))
    parser.add_argument("--case-limit", type=int, default=1000)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(Path(__file__).resolve().parent.parent / ".local"):
        parser.error("Raw XES and subsets must stay in the project's ignored .local directory")
    if args.verify_only:
        result = verify_local(args.output, args.coverage)
    else:
        result = collect(args.output, args.coverage, case_limit=args.case_limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] not in {"collected", "verified"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
