"""Collect FinQA dev only; answer-bearing originals never become model inputs.

This module validates acquisition and lossless field projection. It does not
evaluate numeric reasoning, accounting rules or real-business usefulness.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import httpx


SCHEMA_VERSION = "finqa-dev-acquisition-v1"
DEV_URL = "https://raw.githubusercontent.com/czyssrs/FinQA/main/dataset/dev.json"
LICENSE_URL = "https://raw.githubusercontent.com/czyssrs/FinQA/main/LICENSE"
REPOSITORY_URL = "https://github.com/czyssrs/FinQA"
MAX_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 10_000
OBSERVED_RECORD_KEYS = {"id", "question", "pre_text", "post_text", "table", "locators"}
OBSERVED_KEYS = {"schema_version", "zone", "split", "source_url", "source_sha256", "records"}
REQUIRED_QA_KEYS = {"question", "program", "program_re", "gold_inds", "exe_ans"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                      allow_nan=False).encode("utf-8")


def write_json(path: Path, value: Any) -> str:
    data = json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return sha256(data)


def _object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("Non-finite JSON numbers are unsupported")


def read_json(data: bytes) -> Any:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("FinQA JSON is empty or exceeds the size bound")
    text = data.decode("utf-8", errors="strict")
    if "\ufffd" in text:
        raise ValueError("FinQA JSON contains replacement characters")
    return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant,
                      object_pairs_hook=_object)


def _text(value: Any, field: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or "\ufffd" in value or (nonempty and not value.strip()):
        raise ValueError(f"Invalid FinQA {field}")
    return value


def _texts(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"Invalid FinQA {field} list")
    return [_text(item, field) for item in value]


def _table(value: Any) -> list[list[str]]:
    if not isinstance(value, list) or not value or any(not isinstance(row, list) for row in value):
        raise ValueError("Invalid FinQA table")
    widths = {len(row) for row in value}
    if len(widths) != 1 or not next(iter(widths)):
        raise ValueError("Empty or ragged FinQA table requires a reviewed parser")
    return [[_text(cell, "table cell") for cell in row] for row in value]


def _exact_json(value: Any) -> Any:
    # Preserve the original numeric lexeme as a decimal string in the evaluator
    # zone. Neither acquisition nor projection rounds through a binary float.
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite evaluator number")
        return str(value)
    if isinstance(value, list):
        return [_exact_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _exact_json(item) for key, item in value.items()}
    return value


def project_dev(data: bytes) -> tuple[dict, dict]:
    rows = read_json(data)
    if not isinstance(rows, list) or not rows or len(rows) > MAX_RECORDS:
        raise ValueError("FinQA dev must contain a bounded nonempty record list")
    observed, answers, identities = [], [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("Invalid FinQA record")
        identity = _text(row.get("id"), "id", nonempty=True)
        if identity in identities:
            raise ValueError("Duplicate FinQA record id")
        identities.add(identity)
        qa = row.get("qa")
        if not isinstance(qa, dict) or not REQUIRED_QA_KEYS.issubset(qa):
            raise ValueError("FinQA dev record lacks its official evaluator fields")
        question = _text(qa["question"], "question", nonempty=True)
        _text(qa["program"], "program", nonempty=True)
        _text(qa["program_re"], "program_re", nonempty=True)
        if not isinstance(qa["gold_inds"], dict):
            raise ValueError("Invalid FinQA gold evidence map")
        answer = qa["exe_ans"]
        if isinstance(answer, bool) or not isinstance(answer, (int, Decimal, str)):
            raise ValueError("Invalid FinQA execution answer")
        if isinstance(answer, str) and not answer.strip():
            raise ValueError("Empty FinQA execution answer")
        observed.append({"id": identity, "question": question,
                         "pre_text": _texts(row.get("pre_text"), "pre_text"),
                         "post_text": _texts(row.get("post_text"), "post_text"),
                         "table": _table(row.get("table")),
                         "locators": {"question": f"$[{index}].qa.question",
                                      "pre_text": f"$[{index}].pre_text",
                                      "post_text": f"$[{index}].post_text",
                                      "table": f"$[{index}].table"}})
        # All annotations, gold facts, gold/retrieved model inputs, explanations
        # and auxiliary source fields stay out of the observed whitelist.
        answers.append({"id": identity, "source_index": index,
                        "qa": _exact_json({key: value for key, value in qa.items() if key != "question"}),
                        "auxiliary": _exact_json({key: value for key, value in row.items()
                                                  if key not in {"id", "qa", "pre_text", "post_text", "table"}})})
    common = {"schema_version": SCHEMA_VERSION, "split": "dev", "source_url": DEV_URL,
              "source_sha256": sha256(data)}
    return ({**common, "zone": "observed_question_document", "records": observed},
            {**common, "zone": "evaluator_only", "numeric_encoding": "original_decimal_lexeme_as_string",
             "records": answers})


def load_observed(path: Path) -> dict:
    payload = read_json(path.read_bytes())
    if not isinstance(payload, dict) or set(payload) != OBSERVED_KEYS:
        raise ValueError("Observed input cannot contain evaluator fields or raw file paths")
    if (payload["schema_version"] != SCHEMA_VERSION or payload["split"] != "dev"
            or payload["zone"] != "observed_question_document" or payload["source_url"] != DEV_URL
            or not isinstance(payload["source_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", payload["source_sha256"])):
        raise ValueError("Invalid observed FinQA provenance or zone")
    records = payload["records"]
    if not isinstance(records, list) or not records or len(records) > MAX_RECORDS:
        raise ValueError("Invalid observed record count")
    identities = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != OBSERVED_RECORD_KEYS:
            raise ValueError("Observed record contains evaluator or unknown fields")
        identity = _text(record["id"], "id", nonempty=True)
        if identity in identities:
            raise ValueError("Duplicate observed record id")
        identities.add(identity)
        _text(record["question"], "question", nonempty=True)
        _texts(record["pre_text"], "pre_text")
        _texts(record["post_text"], "post_text")
        _table(record["table"])
        expected = {"question": f"$[{index}].qa.question", "pre_text": f"$[{index}].pre_text",
                    "post_text": f"$[{index}].post_text", "table": f"$[{index}].table"}
        if record["locators"] != expected:
            raise ValueError("Invalid observed source locator")
    return payload


def fetch(client: httpx.Client, url: str, *, maximum: int = MAX_BYTES) -> bytes:
    if url not in {DEV_URL, LICENSE_URL}:
        raise ValueError("Only the official FinQA dev file and LICENSE may be fetched")
    with client.stream("GET", url, follow_redirects=False) as response:
        if response.is_redirect:
            raise ValueError("Official FinQA source redirected; review the new source before use")
        response.raise_for_status()
        length = response.headers.get("content-length")
        if length is not None and int(length) > maximum:
            raise ValueError("FinQA download exceeds size bound")
        parts, size = [], 0
        for part in response.iter_bytes():
            size += len(part)
            if size > maximum:
                raise ValueError("FinQA download exceeds size bound")
            parts.append(part)
        if not size:
            raise ValueError("Empty official FinQA response")
        return b"".join(parts)


def validate_license(data: bytes) -> None:
    text = data.decode("utf-8", errors="strict")
    if (not text.startswith("MIT License") or "Copyright (c) 2021 Zhiyu Chen" not in text
            or "The above copyright notice and this permission notice shall be included" not in text):
        raise ValueError("Official FinQA LICENSE does not match the reviewed MIT notice")


def collect(output: Path, *, transport: httpx.BaseTransport | None = None,
            trust_env: bool = True) -> dict:
    output = output.resolve()
    report = {"schema_version": SCHEMA_VERSION, "status": "failed", "split": "dev",
              "repository_url": REPOSITORY_URL, "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "sources": [{"role": "dataset", "url": DEV_URL}, {"role": "license", "url": LICENSE_URL}],
              "source_revision": "main_unpinned_content_pinned_by_sha256",
              "attempts": [], "accounting_validation": None}
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=20), transport=transport, trust_env=trust_env,
                          headers={"User-Agent": "LedgerLens-FinQA-dev-collector/1.0"}) as client:
            for source in report["sources"]:
                data = fetch(client, source["url"], maximum=MAX_BYTES if source["role"] == "dataset" else 32_768)
                digest = sha256(data)
                suffix = ".json" if source["role"] == "dataset" else ".txt"
                raw = output / "raw" / f"{source['role']}-{digest}{suffix}"
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(data)
                source.update(sha256=digest, bytes=len(data), raw_path=str(raw))
                report["attempts"].append({"source_role": source["role"], "status": "downloaded"})
            raw_data = Path(report["sources"][0]["raw_path"]).read_bytes()
            validate_license(Path(report["sources"][1]["raw_path"]).read_bytes())
            observed, evaluator = project_dev(raw_data)
            observed_path, evaluator_path = output / "observed" / "dev.json", output / "evaluator" / "dev.json"
            report["observed"] = {"path": str(observed_path), "sha256": write_json(observed_path, observed)}
            report["evaluator"] = {"path": str(evaluator_path), "sha256": write_json(evaluator_path, evaluator)}
            report.update(status="collected", record_count=len(observed["records"]), license="MIT",
                          input_projection="whitelist_question_pre_text_post_text_table",
                          original_data_zone="raw_answer_bearing_evaluator_access_only",
                          isolation="logical_filesystem_zones_not_OS_permission_enforcement")
            # Only this explicit no-framework manifest may be passed to the
            # accounting knowledge collector. It contains no text/raw paths.
            write_json(output / "knowledge-source-manifest.json", [
                {"id": "finqa-dev", "status": "fetched", "framework": None,
                 "kind": "component_benchmark", "url": DEV_URL}])
    except (ValueError, UnicodeError, httpx.HTTPError, OSError) as error:
        # Do not write exception text: transport messages can disclose proxy
        # credentials. Failed artifacts remain observations, never usable input.
        report["failure"] = {"type": type(error).__name__, "stage": "source_acquisition_or_projection"}
    write_json(output / "acquisition-report.json", report)
    return report


def _checked_path(value: dict, output: Path, zone: str) -> Path:
    path = Path(value["path"] if "path" in value else value["raw_path"]).resolve()
    if not path.is_relative_to((output / zone).resolve()):
        raise ValueError("FinQA artifact path escaped its declared isolation zone")
    if sha256(path.read_bytes()) != value["sha256"]:
        raise ValueError("FinQA artifact hash mismatch")
    return path


def verify(output: Path) -> dict:
    output = output.resolve()
    report = read_json((output / "acquisition-report.json").read_bytes())
    if (not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION
            or report.get("status") != "collected"
            or report.get("split") != "dev" or report.get("repository_url") != REPOSITORY_URL):
        raise ValueError("FinQA acquisition is absent, failed or unsupported")
    sources = report["sources"]
    if (len(sources) != 2 or [item["url"] for item in sources] != [DEV_URL, LICENSE_URL]
            or [item["role"] for item in sources] != ["dataset", "license"]):
        raise ValueError("Unsupported FinQA acquisition sources")
    raw = _checked_path(sources[0], output, "raw").read_bytes()
    license_data = _checked_path(sources[1], output, "raw").read_bytes()
    validate_license(license_data)
    observed_path = _checked_path(report["observed"], output, "observed")
    evaluator_path = _checked_path(report["evaluator"], output, "evaluator")
    observed = load_observed(observed_path)
    evaluator = read_json(evaluator_path.read_bytes())
    expected_observed, expected_evaluator = project_dev(raw)
    if observed != expected_observed or evaluator != expected_evaluator:
        raise ValueError("FinQA projected fields differ from the immutable downloaded source")
    if report["record_count"] != len(observed["records"]):
        raise ValueError("FinQA record count does not match verified artifacts")
    manifest = read_json((output / "knowledge-source-manifest.json").read_bytes())
    if manifest != [{"id": "finqa-dev", "status": "fetched", "framework": None,
                     "kind": "component_benchmark", "url": DEV_URL}]:
        raise ValueError("FinQA knowledge exclusion manifest was modified")
    return {"status": "verified", "records": len(observed["records"]),
            "source_hashes": "passed", "projection_fidelity": "passed",
            "observed_field_allowlist": "passed", "answer_zone_separation": "passed",
            "knowledge_manifest_exclusion": "passed", "accounting_validation": None}


def coverage(report: dict, verification: dict | None) -> dict:
    status = "verification_failed" if verification and verification["status"] == "failed" else report["status"]
    return {"schema_version": SCHEMA_VERSION, "status": status,
            "split": "dev", "repository_url": REPOSITORY_URL,
            "retrieved_at": report["retrieved_at"], "source_revision": report["source_revision"],
            "record_count": report.get("record_count", 0),
            "license": report.get("license"),
            "sources": [{key: source[key] for key in ("role", "url", "sha256", "bytes") if key in source}
                        for source in report["sources"]],
            "verification": verification, "failure": report.get("failure"),
            "runtime_input_fields": ["id", "question", "pre_text", "post_text", "table", "locators"],
            "evaluator_only_fields": "all_qa_fields_except_question_and_all_other_top_level_fields",
            "raw_files_redistributed": False, "runtime_policy_knowledge_registered": False,
            "isolation": "logical_filesystem_zones_not_OS_permission_enforcement",
            "component_accuracy_measured": None, "accounting_investigation_accuracy_measured": None,
            "limitations": ["Financial-report numeric reasoning data; not complete Chinese accounting books.",
                            "Acquisition and field projection checks do not prove numerical answers or investigation accuracy.",
                            "Official public/private test splits are not acquired; dev is not a sealed acceptance set.",
                            "Repository main can change; these downloaded bytes are pinned by SHA-256.",
                            "Repo MIT notice retained locally; report excerpts can have separate original rights."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".local/finqa"))
    parser.add_argument("--coverage", type=Path, default=Path("docs/finqa-coverage.json"))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--direct-network", action="store_true", help="Ignore configured HTTP proxy variables")
    args = parser.parse_args()
    if args.verify_only:
        report = read_json((args.output / "acquisition-report.json").read_bytes())
    else:
        report = collect(args.output, trust_env=not args.direct_network)
    verification = None
    if report["status"] == "collected":
        try:
            verification = verify(args.output)
        except (ValueError, UnicodeError, OSError, KeyError, TypeError) as error:
            verification = {"status": "failed", "failure_type": type(error).__name__,
                            "accounting_validation": None}
        write_json(args.output / "verification-report.json", verification)
    write_json(args.coverage, coverage(report, verification))
    print(json.dumps({"status": report["status"], "records": report.get("record_count", 0),
                      "verification": verification, "failure": report.get("failure")}, ensure_ascii=False))
    return 0 if verification and verification["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
