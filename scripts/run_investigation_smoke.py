"""Local API investigation acceptance, either one launch or a read-only run audit.

No application settings or .env are read here. The already running backend owns
the model credential and the per-case model budget. This script never retries
the launch POST, including an ambiguous network timeout. Supplying --run-id
only reads local API resources and never starts another investigation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_KEYS = {"reasoning_content", "api_key", "deepseek_api_key", "authorization"}
ACTIVE = {"queued", "running"}
RESULT_STATES = {"supported", "normal", "insufficient", "unsupported"}


class AcceptanceError(RuntimeError):
    def __init__(self, code: str, detail: object = None):
        super().__init__(code)
        self.code, self.detail = code, detail


def scrub(value: object) -> object:
    """Reports retain API failure meaning without credential/reasoning fields."""
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if key.lower() not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        if "reasoning_content" in value or "api_key" in value or "authorization" in value.lower():
            try:
                structured = json.loads(value)
                if isinstance(structured, (dict, list)):
                    return json.dumps(scrub(structured), ensure_ascii=False)
            except ValueError:
                pass
            # An unstructured error containing a private field cannot safely
            # distinguish public meaning from the adjacent private value.
            return "[redacted-private-error]"
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted-key]", value)
        return re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [redacted]", text)
    return value


def canonical_records(rows: object) -> tuple[str, int]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise AcceptanceError("records_protocol")
    if len(rows) >= 50_000:
        raise AcceptanceError("records_listing_may_be_truncated")
    # Compare content, independent of API ordering, retaining duplicate rows.
    serialized = sorted(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) for row in rows)
    encoded = json.dumps(serialized, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), len(rows)


def usage_of(run: dict) -> dict:
    usage = run.get("usage")
    if not isinstance(usage, dict):
        raise AcceptanceError("usage_protocol")
    result = {}
    for key in ("calls", "tokens", "tools", "reserved_tokens"):
        value = usage.get(key, 0)
        if type(value) is not int or value < 0:
            raise AcceptanceError("usage_protocol")
        result[key] = value
    if result["calls"] > 24 or result["tools"] > 60:
        raise AcceptanceError("product_budget_exceeded", result)
    return result


def _private_fields(value: object) -> bool:
    if isinstance(value, dict):
        return any(key.lower() in PRIVATE_KEYS or _private_fields(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_private_fields(item) for item in value)
    return False


def reference_checks(result: dict, snapshot: dict, rows: list[dict], evidence: list[dict]) -> tuple[dict, set[str]]:
    record_ids = snapshot.get("record_ids")
    if (not isinstance(record_ids, list) or any(not isinstance(value, str) for value in record_ids)
            or len(set(record_ids)) != len(record_ids)):
        raise AcceptanceError("snapshot_record_ids_protocol")
    frozen_ids = set(record_ids)
    # The normal API payload includes both IDs. For older payloads, the case
    # evidence endpoint supplies the record identity and exact locator.
    mapped = {row["id"]: row for row in rows if isinstance(row.get("id"), str)}
    for entry in evidence:
        identity, source = entry.get("record_id"), entry.get("record")
        if identity not in frozen_ids or not isinstance(source, dict):
            raise AcceptanceError("evidence_outside_snapshot")
        if identity not in mapped:
            matches = [row for row in rows if row.get("source_id") == entry.get("source_id")
                       and row.get("kind") == entry.get("kind")]
            if len(matches) != 1:
                raise AcceptanceError("record_identity_unresolved")
            candidate = matches[0]
            # Do not trust a source number alone if its content conflicts.
            if any(source.get(key) != value for key, value in candidate.items() if key not in {"id", "evidence_id"}):
                raise AcceptanceError("record_evidence_content_conflict")
            mapped[identity] = {**candidate, "id": identity, "evidence_id": entry.get("id")}
        elif mapped[identity].get("evidence_id") != entry.get("id"):
            raise AcceptanceError("record_evidence_identity_conflict")
    snapshot_evidence = {}
    for identity in frozen_ids:
        row = mapped.get(identity)
        if row and isinstance(row.get("evidence_id"), str):
            eid = row["evidence_id"]
            if eid in snapshot_evidence:
                raise AcceptanceError("duplicate_evidence_identity")
            snapshot_evidence[eid] = identity

    references, instances, unreferenced = set(), 0, 0
    groups = {"claims": 0, "proposed_claims": 0, "hypotheses": 0}
    for group in groups:
        items = result.get(group)
        if not isinstance(items, list):
            raise AcceptanceError("result_reference_protocol", {"group": group})
        groups[group] = len(items)
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("evidence_ids"), list):
                raise AcceptanceError("result_reference_protocol", {"group": group})
            ids = item["evidence_ids"]
            if any(not isinstance(eid, str) or not eid for eid in ids):
                raise AcceptanceError("result_reference_protocol", {"group": group})
            if not ids:
                unreferenced += 1
                if group != "hypotheses" or item.get("status") in {"supported", "refuted"}:
                    raise AcceptanceError("assertion_without_evidence", {"group": group})
            if any(eid not in snapshot_evidence for eid in ids):
                raise AcceptanceError("reference_outside_snapshot", {"group": group})
            references.update(ids)
            instances += len(ids)
    return {
        "entities": groups, "reference_instances": instances, "unique_references": len(references),
        "unreferenced_hypotheses": unreferenced,
        "snapshot_records": len(frozen_ids), "mapped_snapshot_records": len(snapshot_evidence),
        "all_references_belong_to_snapshot": True,
        "reference_scope_note": "引用存在不证明结论业务正确；无引用时不计算定位率。",
    }, references


def export_checks(
    document: dict, markdown: str, *, case_id: str, book_id: str, snapshot_id: str,
    run: dict, references: set[str], snapshot_ids: set[str], evidence_records: dict[str, str],
) -> dict:
    if _private_fields(document) or re.search(r"\bsk-[A-Za-z0-9_-]{8,}\b", json.dumps(document) + markdown):
        raise AcceptanceError("private_data_in_export")
    if (document.get("id") != case_id or document.get("book_id") != book_id
            or document.get("snapshot_id") != snapshot_id
            or document.get("snapshot", {}).get("id") != snapshot_id
            or document.get("snapshot", {}).get("book_id") != book_id
            or set(document.get("snapshot", {}).get("record_ids", [])) != snapshot_ids):
        raise AcceptanceError("export_binding_mismatch")
    runs = document.get("runs")
    run_id = run["id"]
    target = next((item for item in runs if isinstance(item, dict) and item.get("id") == run_id), None) if isinstance(runs, list) else None
    if target is None or target.get("case_id") != case_id:
        raise AcceptanceError("export_missing_run")
    if (target.get("mode") != run["mode"] or target.get("status") != "complete"
            or target.get("result") != run["result"] or target.get("usage") != run["usage"]):
        raise AcceptanceError("export_run_content_mismatch")
    evidence = document.get("evidence")
    if not isinstance(evidence, list):
        raise AcceptanceError("export_evidence_protocol")
    located = set()
    for entry in evidence:
        if (not isinstance(entry, dict) or entry.get("record_id") not in snapshot_ids
                or not isinstance(entry.get("id"), str)):
            raise AcceptanceError("export_evidence_outside_snapshot")
        if evidence_records.get(entry["id"]) != entry["record_id"]:
            raise AcceptanceError("export_evidence_identity_conflict")
        if entry.get("document_id") and isinstance(entry.get("locator"), dict) and entry["locator"]:
            located.add(entry["id"])
    if references - located:
        raise AcceptanceError("export_missing_reference_locators", {"missing_count": len(references - located)})
    if not markdown.strip() or snapshot_id not in markdown or run_id not in markdown:
        raise AcceptanceError("markdown_binding_missing")
    # Markdown embeds the full machine-readable report. Check exact evidence
    # IDs rather than trusting a successful HTTP response or a generic heading.
    if any(eid not in markdown for eid in references):
        raise AcceptanceError("markdown_missing_references")
    return {
        "json_generated": True, "markdown_generated": True, "run_in_export": True,
        "referenced_evidence_with_locators": len(references),
        "reference_location_rate": 1.0 if references else None,
        "no_references_to_check": not references,
    }


async def run_investigation_smoke(
    client: httpx.AsyncClient, *, case_id: str, mode: str, run_id: str | None = None,
    report_path: Path | None = None, timeout_seconds: float = 600, poll_seconds: float = 2,
    on_progress: Callable | None = None,
) -> dict:
    if mode not in {"single", "multi", "active"}:
        raise ValueError("Model mode must be single, multi or active")
    if not 0 < timeout_seconds <= 600 or not 0 <= poll_seconds <= 30:
        raise ValueError("Polling is bounded to at most 600 seconds")
    started = time.monotonic()
    inspect_existing = run_id is not None
    report = {
        "schema_version": "investigation-smoke-v1", "started_at": datetime.now(UTC).isoformat(),
        "case_id": case_id, "mode": mode, "status": "running", "events": [], "verifications": {},
        "execution": "inspect_existing" if inspect_existing else "launch_new",
        "launch_posts": 0, "polls": 0, "usage": {},
        "poll_timeout_seconds": timeout_seconds,
        "limits": {"model_calls": 24, "tool_calls": 60},
        "notice": "本验收检查调用闭环、引用与原账只读，不证明会计结论正确或多智能体有增益。",
        "records_hash_scope": (
            "仅验证当下验收读取期间账簿内容相同；未验证已有 run 开始前的账簿状态。"
            if inspect_existing else
            "验证调查启动请求前到本次验收结束的账簿内容；若后端复用已有 run，不证明其开始前状态。"
        ),
    }
    if inspect_existing:
        report["run_id"] = run_id
        report["historical_ledger_state_before_run_verified"] = False
    book_id, before_rows = None, None
    artifact_paths = ()
    if report_path is not None:
        artifact_paths = tuple(report_path.with_name(report_path.stem + suffix)
                               for suffix in (".workpaper.json", ".workpaper.md"))
        # Keep paid-run evidence intact. Reserve this report before even a GET;
        # a resumed audit uses --run-id and a fresh report filename.
        if any(path.exists() for path in (report_path, *artifact_paths)):
            raise FileExistsError("验收报告或底稿已存在；请使用新文件名以保留之前的运行记录")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(scrub(report), ensure_ascii=False, indent=2))

    def checkpoint() -> None:
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = report_path.with_suffix(report_path.suffix + ".tmp")
            temporary.write_text(json.dumps(scrub(report), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(report_path)

    async def emit(event: dict) -> None:
        public = scrub({"recorded_at": datetime.now(UTC).isoformat(), **event})
        report["events"].append(public)
        checkpoint()
        if on_progress:
            value = on_progress(public)
            if inspect.isawaitable(value):
                await value

    async def request(method: str, path: str, *, payload: dict | None = None, timeout: float = 30) -> httpx.Response:
        response = await client.request(method, path, json=payload, timeout=timeout, follow_redirects=False)
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = "响应未提供结构化错误"
            raise AcceptanceError("api_http", {"http_status": response.status_code, "detail": scrub(detail)})
        return response

    async def get_json(path: str, *, timeout: float = 30) -> object:
        response = await request("GET", path, timeout=timeout)
        try:
            return response.json()
        except ValueError:
            raise AcceptanceError("api_json_protocol") from None

    def bind_run(run: object) -> dict:
        if (not isinstance(run, dict) or run.get("case_id") != case_id or run.get("mode") != mode
                or (run_id is not None and run.get("id") != run_id)):
            raise AcceptanceError("run_binding_mismatch")
        if run.get("status") not in ACTIVE | {"complete", "failed", "budget_exhausted"}:
            raise AcceptanceError("run_status_protocol")
        report["usage"] = usage_of(run)
        report["run_status"] = run["status"]
        report["run_error"] = scrub(run.get("error"))
        return run

    checkpoint()
    try:
        case = await get_json(f"/api/v1/cases/{case_id}")
        if not isinstance(case, dict) or case.get("id") != case_id:
            raise AcceptanceError("case_protocol")
        book_id, snapshot_id = case.get("book_id"), case.get("snapshot_id")
        if not isinstance(book_id, str) or not isinstance(snapshot_id, str):
            raise AcceptanceError("case_binding_protocol")
        if case.get("stale") and not inspect_existing:
            raise AcceptanceError("case_stale")
        report["case_currently_stale"] = bool(case.get("stale"))
        report.update({"book_id": book_id, "snapshot_id": snapshot_id})
        snapshot = await get_json(f"/api/v1/snapshots/{snapshot_id}")
        if (not isinstance(snapshot, dict) or snapshot.get("id") != snapshot_id
                or snapshot.get("book_id") != book_id):
            raise AcceptanceError("snapshot_binding_mismatch")
        before_rows = await get_json(f"/api/v1/books/{book_id}/records")
        before_hash, before_count = canonical_records(before_rows)
        report["records_before"] = {"sha256": before_hash, "count": before_count}
        await emit({"event": "preflight_complete", "record_count": before_count})
        if inspect_existing:
            # Only inspect the requested identity, even when it failed or is
            # still running. Do not fall back to a new paid investigation.
            run = bind_run(await get_json(f"/api/v1/runs/{run_id}"))
        else:
            # Exactly one attempt: a timeout may already have started a paid run.
            report["launch_posts"] = 1
            checkpoint()
            run = bind_run((await request("POST", f"/api/v1/cases/{case_id}/investigate", payload={"mode": mode})).json())
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise AcceptanceError("run_identity_missing")
        report["run_id"] = run_id
        await emit({"event": "existing_run_loaded" if inspect_existing else "investigation_launched",
                    "run_id": run_id, "status": run["status"],
                    "usage": report["usage"]})
        deadline, seen_steps = time.monotonic() + timeout_seconds, 0
        while True:
            steps = run.get("steps", [])
            if not isinstance(steps, list) or len(steps) < seen_steps:
                raise AcceptanceError("run_events_protocol")
            for step in steps[seen_steps:]:
                if not isinstance(step, dict):
                    raise AcceptanceError("run_events_protocol")
                # Deliberately omit prompts, reasoning, arguments and outputs.
                event = step.get("event", "unrecognized_event")
                if not isinstance(event, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,100}", event):
                    event = "unrecognized_event"
                detail = {"event": event, "usage": report["usage"]}
                if isinstance(step.get("usage"), dict):
                    detail["usage"] = usage_of({"usage": step["usage"]})
                await emit(detail)
            seen_steps = len(steps)
            if run["status"] not in ACTIVE:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report["timeout_does_not_cancel_run"] = True
                raise AcceptanceError("poll_timeout", {"actual_status": run["status"]})
            await asyncio.sleep(min(poll_seconds, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report["timeout_does_not_cancel_run"] = True
                raise AcceptanceError("poll_timeout", {"actual_status": run["status"]})
            run = bind_run(await get_json(f"/api/v1/runs/{run_id}", timeout=min(30, remaining)))
            report["polls"] += 1
            await emit({"event": "run_polled", "status": run["status"], "usage": report["usage"]})
        if run["status"] != "complete":
            raise AcceptanceError("terminal_run_failed", {"status": run["status"], "error": scrub(run.get("error"))})
        result = run.get("result")
        if not isinstance(result, dict) or result.get("status") not in RESULT_STATES or result.get("mode") != mode:
            raise AcceptanceError("result_protocol")
        if report["usage"]["calls"] < 1:
            raise AcceptanceError("model_mode_without_model_calls")
        if usage_of({"usage": result.get("usage")}) != report["usage"]:
            raise AcceptanceError("result_usage_mismatch")
        fresh_case = await get_json(f"/api/v1/cases/{case_id}")
        if (not isinstance(fresh_case, dict) or fresh_case.get("book_id") != book_id
                or fresh_case.get("snapshot_id") != snapshot_id
                or bool(fresh_case.get("stale")) != bool(case.get("stale"))
                or (fresh_case.get("stale") and not inspect_existing)):
            raise AcceptanceError("case_binding_changed")
        evidence = await get_json(f"/api/v1/cases/{case_id}/evidence")
        if not isinstance(evidence, list) or any(not isinstance(entry, dict) for entry in evidence):
            raise AcceptanceError("evidence_protocol")
        checks, refs = reference_checks(result, snapshot, before_rows, evidence)
        report["verifications"]["references"] = checks
        document = await get_json(f"/api/v1/exports/cases/{case_id}?format=json")
        markdown = (await request("GET", f"/api/v1/exports/cases/{case_id}?format=markdown")).text
        if not isinstance(document, dict):
            raise AcceptanceError("export_json_protocol")
        export = export_checks(document, markdown, case_id=case_id, book_id=book_id, snapshot_id=snapshot_id,
                               run=run, references=refs, snapshot_ids=set(snapshot["record_ids"]),
                               evidence_records={entry["id"]: entry["record_id"] for entry in evidence})
        report["verifications"]["exports"] = export
        report["verifications"]["binding"] = {"run_case_matches": True, "case_snapshot_unchanged": True,
                                                "snapshot_book_matches": True}
        if report_path is not None:
            json_path, markdown_path = artifact_paths
            with json_path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(document, ensure_ascii=False, indent=2))
            with markdown_path.open("x", encoding="utf-8") as output:
                output.write(markdown)
            report["artifacts"] = {"json": str(json_path), "markdown": str(markdown_path)}
        report["status"] = "passed"
    except AcceptanceError as error:
        report["status"] = "failed"
        report["error"] = {"code": error.code, "detail": scrub(error.detail)}
    except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
        report["status"] = "failed"
        report["error"] = {"code": "api_transport", "detail": "连接失败；不重试调查启动请求"}
    except Exception:
        report["status"] = "failed"
        report["error"] = {"code": "unexpected_error", "detail": "未保存任意异常原文或服务私有内容"}
    finally:
        # Also check read-only behavior after a terminal failure or timeout.
        if book_id is not None and before_rows is not None and "records_before" in report:
            try:
                after_rows = await get_json(f"/api/v1/books/{book_id}/records")
                after_hash, after_count = canonical_records(after_rows)
                report["records_after"] = {"sha256": after_hash, "count": after_count}
                unchanged = report["records_before"] == report["records_after"]
                report["verifications"]["original_records_unchanged"] = unchanged
                if not unchanged:
                    report["status"] = "failed"
                    report.setdefault("error", {"code": "original_records_changed"})
            except Exception:
                report["status"] = "failed"
                report["verifications"]["original_records_unchanged"] = None
                report.setdefault("error", {"code": "records_after_check_failed"})
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        checkpoint()
    await emit({"event": "acceptance_finished", "status": report["status"], "usage": report["usage"],
                "error": report.get("error")})
    return scrub(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch one real investigation, or read-only audit an existing run and its workpaper")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--mode", required=True, choices=["single", "multi", "active"])
    parser.add_argument("--run-id", help="Read-only audit this existing run; never POST or start model calls")
    parser.add_argument("--live", action="store_true", help="Explicitly opt into the backend's real model requests")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report", type=Path, default=ROOT / "runtime" / "investigation-live-smoke.json")
    parser.add_argument("--timeout-seconds", type=float, default=600)
    args = parser.parse_args(argv)
    if not args.live and args.run_id is None:
        parser.error("--live is required to launch real model requests")
    endpoint = urlparse(args.base_url)
    if (endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        parser.error("--base-url must be a local HTTP endpoint without credentials, query, or fragment")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", args.case_id):
        parser.error("Invalid case identity")
    if args.run_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", args.run_id):
        parser.error("Invalid run identity")
    if not 0 < args.timeout_seconds <= 600:
        parser.error("--timeout-seconds must be greater than 0 and at most 600")

    async def execute() -> dict:
        async with httpx.AsyncClient(base_url=args.base_url, follow_redirects=False) as client:
            return await run_investigation_smoke(
                client, case_id=args.case_id, mode=args.mode, report_path=args.report,
                run_id=args.run_id,
                timeout_seconds=args.timeout_seconds,
                on_progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
            )
    try:
        report = asyncio.run(execute())
    except FileExistsError as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], "run_id": report.get("run_id"),
                      "usage": report["usage"], "error": report.get("error"),
                      "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
