"""Immutable local process snapshots with verified source locators (Apache-2.0).

Only fixed filenames beneath a configured root are read. The manifest binds
source subset bytes and output bytes; loading also replays the pure adapter to
validate evidence membership, source values, IDs and observations against that
stored subset. This is local integrity validation, not publisher authentication.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from pydantic import Field

from .process_events import (
    ADAPTER_VERSION, OBSERVATION_VERSION, ProcessModel, ProcessSnapshot, adapt_traces,
    quality_report, snapshot_identity,
)


STORE_SCHEMA_VERSION = "process-snapshot-store-v1"
MAX_SNAPSHOT_BYTES = 150_000_000
MAX_SUBSET_BYTES = 50_000_000
MAX_TRACES = 10_000
SNAPSHOT_NAME = re.compile(r"process_snapshot_[0-9a-f]{64}")
# Fully verified snapshots keyed by the exact bytes of all four files. A later
# read still hashes every file; any byte change misses and replays in full.
_VERIFIED: dict[tuple, ProcessSnapshot] = {}
_VERIFIED_LIMIT = 4


class ProcessManifest(ProcessModel):
    schema_version: str
    dataset_scope_id: str
    snapshot_id: str
    title: str
    source_raw_sha256: str
    source_subset_sha256: str
    adapter_version: str
    observation_version: str
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_jsonl_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    trace_count: int = Field(ge=1)
    event_count: int = Field(ge=0)
    selection: str
    source_verification: str
    status: str


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bytes_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError("Process snapshot path escapes the configured root")
    return path


def _read_bounded(path: Path, limit: int) -> bytes:
    if path.stat().st_size > limit:
        raise ValueError("Process snapshot file exceeds its read budget")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Process snapshot file exceeds its read budget")
    return data


def read_source_traces(data: bytes) -> list[dict]:
    """Preserve exact JSONL line-to-trace membership; blank lines are rejected."""
    if len(data) > MAX_SUBSET_BYTES:
        raise ValueError("Process subset exceeds its read budget")
    lines = data.decode("utf-8").splitlines()
    if not 1 <= len(lines) <= MAX_TRACES or any(not line.strip() for line in lines):
        raise ValueError("Source JSONL must contain 1..10000 trace lines without blank lines")
    return [json.loads(line) for line in lines]


def _read_manifest(root: Path, snapshot_id: str) -> ProcessManifest:
    if not isinstance(snapshot_id, str) or not SNAPSHOT_NAME.fullmatch(snapshot_id):
        raise ValueError("Invalid process snapshot identifier")
    manifest = ProcessManifest.model_validate_json(_read_bounded(
        _safe_path(root, snapshot_id, "manifest.json"), 100_000))
    if manifest.schema_version != STORE_SCHEMA_VERSION or manifest.adapter_version != ADAPTER_VERSION or (
            manifest.observation_version != OBSERVATION_VERSION):
        raise ValueError("Unsupported process snapshot schema or mapping version")
    scope, derived_id = snapshot_identity(manifest.source_raw_sha256, manifest.source_subset_sha256)
    if derived_id != snapshot_id or manifest.snapshot_id != snapshot_id or manifest.dataset_scope_id != scope:
        raise ValueError("Process manifest content identity does not match its directory")
    if manifest.source_jsonl_sha256 != manifest.source_subset_sha256:
        raise ValueError("Process manifest source subset hash is inconsistent")
    return manifest


def load_process_snapshot(root: Path, snapshot_id: str) -> ProcessSnapshot:
    """Verify all bytes and independently reconstruct fields before returning.

    Replay is skipped only when all four files are byte-identical (SHA256) to a
    snapshot that already passed this full verification in this process.
    """
    manifest = _read_manifest(root, snapshot_id)
    manifest_raw = _read_bounded(_safe_path(root, snapshot_id, "manifest.json"), 100_000)
    if ProcessManifest.model_validate_json(manifest_raw) != manifest:
        raise ValueError("Process manifest changed while it was being read")
    artifact = _read_bounded(_safe_path(root, snapshot_id, "snapshot.json"), MAX_SNAPSHOT_BYTES)
    source = _read_bounded(_safe_path(root, snapshot_id, "source.jsonl"), MAX_SUBSET_BYTES)
    report_raw = _read_bounded(_safe_path(root, snapshot_id, "quality-report.json"), 200_000)
    if _hash(artifact) != manifest.snapshot_sha256 or _hash(source) != manifest.source_jsonl_sha256 or (
            _hash(report_raw) != manifest.quality_report_sha256):
        raise ValueError("Process snapshot/source/report file hash mismatch")
    key = (str(root.resolve()), snapshot_id, _hash(manifest_raw), _hash(artifact), _hash(source), _hash(report_raw))
    if key in _VERIFIED:
        return _VERIFIED[key]
    snapshot = _verify_replay(manifest, artifact, source, report_raw)
    if len(_VERIFIED) >= _VERIFIED_LIMIT:
        _VERIFIED.pop(next(iter(_VERIFIED)))
    _VERIFIED[key] = snapshot
    return snapshot


def _verify_replay(manifest: ProcessManifest, artifact: bytes, source: bytes, report_raw: bytes) -> ProcessSnapshot:
    snapshot = ProcessSnapshot.model_validate_json(artifact)
    expected = adapt_traces(read_source_traces(source), raw_sha256=manifest.source_raw_sha256,
        subset_sha256=manifest.source_subset_sha256, observed_at=manifest.observed_at,
        selection=manifest.selection, source_verification=manifest.source_verification)
    if snapshot != expected:
        raise ValueError("Stored process evidence does not match independently replayed source adaptation")
    report = quality_report(expected)
    if json.loads(report_raw) != report or manifest.status != report["status"] or manifest.title != expected.dataset.title or (
            manifest.trace_count != expected.dataset.trace_count or manifest.event_count != expected.dataset.event_count):
        raise ValueError("Process manifest/report counts or quality observations are inconsistent")
    return snapshot


def list_process_snapshots(root: Path) -> list[dict]:
    """List validated manifests only; a corrupt snapshot is an error, not hidden."""
    root = root.resolve()
    if not root.exists():
        return []
    results = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if not SNAPSHOT_NAME.fullmatch(path.name):
            continue  # Incomplete import directories are never published snapshots.
        load_process_snapshot(root, path.name)
        results.append(_read_manifest(root, path.name).model_dump(mode="json"))
    return results


def save_process_snapshot(root: Path, snapshot: ProcessSnapshot, subset_path: Path) -> dict:
    """Atomically append an immutable snapshot; identical identity reuses it.

    Existing snapshots keep their first observation time and are never rewritten.
    A source/mapping change produces a new snapshot ID. Failed staging directories
    remain unpublished for inspection.
    """
    root = root.resolve()
    snapshot_id = snapshot.dataset.snapshot_id
    if not SNAPSHOT_NAME.fullmatch(snapshot_id):
        raise ValueError("Invalid process snapshot identifier")
    destination = _safe_path(root, snapshot_id)
    if destination.exists():
        existing = load_process_snapshot(root, snapshot_id)
        return {"status": "existing_verified", "snapshot_id": snapshot_id,
                "directory": str(destination), "dataset": existing.dataset.model_dump(mode="json")}
    source = _read_bounded(subset_path, MAX_SUBSET_BYTES)
    if _hash(source) != snapshot.dataset.source_subset_sha256:
        raise ValueError("Source subset changed before snapshot persistence")
    expected = adapt_traces(read_source_traces(source), raw_sha256=snapshot.dataset.source_raw_sha256,
        subset_sha256=snapshot.dataset.source_subset_sha256, observed_at=snapshot.dataset.observed_at,
        selection=snapshot.dataset.selection, source_verification=snapshot.dataset.source_verification)
    if snapshot != expected:
        raise ValueError("Cannot save process evidence that disagrees with the source subset")
    artifact = _bytes_json(snapshot.model_dump(mode="json"))
    if len(artifact) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Adapted process snapshot exceeds its storage budget")
    report = quality_report(snapshot)
    report_raw = _bytes_json(report)
    manifest = ProcessManifest(schema_version=STORE_SCHEMA_VERSION,
        dataset_scope_id=snapshot.dataset.dataset_scope_id, snapshot_id=snapshot_id,
        title=snapshot.dataset.title,
        source_raw_sha256=snapshot.dataset.source_raw_sha256,
        source_subset_sha256=snapshot.dataset.source_subset_sha256,
        adapter_version=snapshot.dataset.adapter_version, observation_version=snapshot.dataset.observation_version,
        snapshot_sha256=_hash(artifact), source_jsonl_sha256=_hash(source), quality_report_sha256=_hash(report_raw),
        observed_at=snapshot.dataset.observed_at, trace_count=snapshot.dataset.trace_count,
        event_count=snapshot.dataset.event_count, selection=snapshot.dataset.selection,
        source_verification=snapshot.dataset.source_verification, status=report["status"])
    root.mkdir(parents=True, exist_ok=True)
    staging = _safe_path(root, ".pending-" + uuid4().hex)
    staging.mkdir()
    (staging / "source.jsonl").write_bytes(source)
    (staging / "snapshot.json").write_bytes(artifact)
    (staging / "quality-report.json").write_bytes(report_raw)
    (staging / "manifest.json").write_bytes(_bytes_json(manifest.model_dump(mode="json")))
    try:
        staging.rename(destination)
    except FileExistsError:
        # Concurrent identical imports must reuse the winner, not replace it.
        existing = load_process_snapshot(root, snapshot_id)
        return {"status": "existing_verified", "snapshot_id": snapshot_id,
                "directory": str(destination), "dataset": existing.dataset.model_dump(mode="json")}
    return {"status": "created", "snapshot_id": snapshot_id, "directory": str(destination),
            "dataset": snapshot.dataset.model_dump(mode="json")}
