"""Independently follow every process JSON pointer to its stored source field."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.process_store import list_process_snapshots, load_process_snapshot  # noqa: E402


def pointer_value(source, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('Source locator requires a JSON pointer')
    value = source
    for token in pointer[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def verify(root: Path, snapshot_id: str) -> dict:
    snapshot = load_process_snapshot(root, snapshot_id)
    raw = (root / snapshot_id / 'source.jsonl').read_bytes()
    sources = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    counts = {'trace_locators': 0, 'event_locators': 0, 'field_pointers': 0}
    for items, attribute_field, count_field in ((snapshot.traces, 'trace_attributes', 'trace_locators'),
                                               (snapshot.events, 'event_attributes', 'event_locators')):
        for item in items:
            locator = item.locator
            source = sources[locator.jsonl_line - 1]
            if source['case_id'] != locator.source_trace_id or locator.jsonl_line != locator.source_trace_ordinal:
                raise ValueError('Source trace locator mismatch')
            attributes = getattr(item, attribute_field)
            if attributes.keys() != locator.field_pointers.keys():
                raise ValueError('Locator does not cover every preserved source field')
            for field, pointer in locator.field_pointers.items():
                if pointer_value(source, pointer) != attributes[field]:
                    raise ValueError('Source field locator mismatch')
                counts['field_pointers'] += 1
            if attribute_field == 'event_attributes':
                original = source['events'][locator.source_event_ordinal - 1]
                if attributes != original or item.source_event_ordinal != locator.source_event_ordinal:
                    raise ValueError('Original event ordinal or field coverage mismatch')
            elif attributes != source['attributes']:
                raise ValueError('Original trace field coverage mismatch')
            counts[count_field] += 1
    return {'status': 'verified', 'verified_at': datetime.now(UTC).isoformat(),
            'dataset_scope_id': snapshot.dataset.dataset_scope_id, 'snapshot_id': snapshot_id, **counts,
            'source_subset_sha256': hashlib.sha256(raw).hexdigest(), 'new_model_requests': 0,
            'scope': 'All stored source fields and JSONL locators; not accounting or anomaly correctness.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT / '.local/bpi2019/process-events')
    parser.add_argument('--snapshot-id')
    parser.add_argument('--report', type=Path, default=ROOT / 'docs/bpi2019-adapter-validation.json')
    args = parser.parse_args()
    identities = [args.snapshot_id] if args.snapshot_id else [item['snapshot_id']
                                                           for item in list_process_snapshots(args.root)]
    if not identities:
        raise SystemExit('No imported process snapshot exists')
    result = {'status': 'verified', 'snapshots': [verify(args.root, identity) for identity in identities]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
