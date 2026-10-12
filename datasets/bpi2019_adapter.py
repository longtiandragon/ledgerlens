"""Offline BPI2019 verified-subset adaptation; no network or ledger writes."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from backend.app.process_events import adapt_traces, quality_report
from backend.app.process_store import (
    list_process_snapshots, load_process_snapshot, read_source_traces, save_process_snapshot,
)
from datasets.bpi2019 import file_hashes, verify_local


def import_verified_subset(directory: Path, coverage: Path, output: Path) -> dict:
    """Reverify original XES/metadata/subset before producing any success result."""
    verification = verify_local(directory, coverage)
    if verification.get("status") != "verified":
        raise ValueError("Source verification did not succeed")
    report = json.loads(coverage.read_text(encoding="utf-8"))
    subset_path = directory / "subset.jsonl"
    facts = file_hashes(subset_path)
    if facts["sha256"] != report["subset"]["sha256"]:
        raise ValueError("Source subset changed after verification")
    traces = read_source_traces(subset_path.read_bytes())
    snapshot = adapt_traces(traces, raw_sha256=report["download"]["sha256"],
        subset_sha256=facts["sha256"], observed_at=datetime.now(UTC),
        selection="first_complete_traces_in_source_order",
        source_verification="verified_offline_against_official_metadata_and_raw_xes")
    saved = save_process_snapshot(output, snapshot, subset_path)
    loaded = load_process_snapshot(output, saved["snapshot_id"])
    return {"status": saved["status"], "directory": saved["directory"],
            "source_verification": verification, "quality_report": quality_report(loaded)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(".local/bpi2019"))
    parser.add_argument("--coverage", type=Path, default=Path("docs/bpi2019-coverage.json"))
    parser.add_argument("--output", type=Path, default=Path(".local/bpi2019/process-events"))
    parser.add_argument("--list", action="store_true", dest="list_snapshots")
    parser.add_argument("--verify-snapshot", metavar="SNAPSHOT_ID")
    args = parser.parse_args()
    local_root = Path(__file__).resolve().parent.parent / ".local"
    if not args.output.resolve().is_relative_to(local_root.resolve()):
        parser.error("Process snapshots with source identifiers must remain under the ignored .local directory")
    try:
        if args.list_snapshots and args.verify_snapshot:
            parser.error("Choose either --list or --verify-snapshot")
        if args.list_snapshots:
            result = {"status": "listed_verified_snapshots", "snapshots": list_process_snapshots(args.output)}
        elif args.verify_snapshot:
            snapshot = load_process_snapshot(args.output, args.verify_snapshot)
            result = {"status": "verified_snapshot", "quality_report": quality_report(snapshot)}
        else:
            result = import_verified_subset(args.source, args.coverage, args.output)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "detail": str(exc)},
                         ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
