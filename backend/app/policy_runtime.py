"""Stored, source-bound policy checks, separate from the legacy core rule run.

Human confirmation is a local review assertion, not authenticated professional
approval or proof that a condition was known at the historical source cutoff.
Source visibility is frozen by the ledger snapshot; annotation versions are
frozen independently in each policy check's binding.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update

from .db import (Book, Document, ImportBatch, Record, Snapshot, PolicyObservationVersion,
                 PolicyCheckRun, Audit, uid, now, serialize)
from .policy_contract import PolicyCheckContext, ConditionEvidence, PolicyObservation
from .policy_consistency import evaluate_policy, policy_rule_identity
from .schemas import LedgerInput
from .imports import parse_file


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def value_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('Timezone missing')
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(409, 'Source timestamp is absent or invalid') from exc


def scoped_snapshot(s, book_id, snapshot_id, *, current=False):
    book = s.get(Book, book_id)
    snap = s.get(Snapshot, snapshot_id)
    if book is None or snap is None or snap.book_id != book_id:
        raise HTTPException(404, 'Book or scoped snapshot not found')
    if current and snap.revision != book.revision:
        raise HTTPException(409, 'Ledger snapshot is stale; create a new observation on the latest snapshot')
    return book, snap


def lock_book(s, book_id):
    s.execute(update(Book).where(Book.id == book_id).values(revision=Book.revision))
    book = s.get(Book, book_id)
    if book is None:
        raise HTTPException(404, 'Book not found')
    s.refresh(book)
    return book


def source_context(s, snap):
    """Never accept scope, framework, time or evidence membership from clients."""
    ids = snap.record_ids
    if (not isinstance(ids, list) or any(not isinstance(item, str) or not item or len(item) > 200 for item in ids)
            or len(ids) != len(set(ids)) or len(ids) > 50000):
        raise HTTPException(409, 'Invalid snapshot membership')
    records = []
    for offset in range(0, len(ids), 800):
        records.extend(s.scalars(select(Record).where(
            Record.book_id == snap.book_id, Record.id.in_(ids[offset:offset + 800]))).all())
    if len(records) != len(ids):
        raise HTTPException(409, 'Snapshot records are missing or outside the book')
    evidence, sources = [], {}
    cutoff = stamp(snap.created_at)
    for record in records:
        payload = record.payload
        if (not isinstance(payload, dict) or payload.get('id') != record.id
                or payload.get('evidence_id') != record.evidence_id
                or payload.get('book_id', snap.book_id) != snap.book_id
                or payload.get('kind') != record.kind
                or payload.get('source_id') != record.source_id):
            raise HTTPException(409, 'Record and evidence identities disagree')
        observed = stamp(payload.get('observed_at'))
        if observed > cutoff:
            raise HTTPException(409, 'Future source evidence is outside the snapshot')
        original = {k: v for k, v in payload.items() if k in LedgerInput.model_fields}
        # Import fingerprints use the original normalized source fields, not
        # generated IDs or observation timestamps, and default JSON spacing.
        fingerprint = hashlib.sha256(json.dumps(original, sort_keys=True,
                                               ensure_ascii=False).encode()).hexdigest()
        if fingerprint != record.fingerprint:
            raise HTTPException(409, 'Original normalized record fingerprint changed')
        if record.evidence_id in sources:
            raise HTTPException(409, 'Duplicate source evidence identity')
        try:
            evidence.append(ConditionEvidence(evidence_id=record.evidence_id, book_id=snap.book_id,
                                             snapshot_id=snap.id, known_at=observed))
        except ValueError as exc:
            raise HTTPException(409, 'Stored source evidence contract is invalid') from exc
        sources[record.evidence_id] = record
    try:
        context = PolicyCheckContext(book_id=snap.book_id, snapshot_id=snap.id,
                                     framework=snap.policy['framework'], period=snap.policy['period'],
                                     as_of=cutoff, evidence=evidence)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(409, 'Snapshot policy or evidence context is invalid') from exc
    # Verify the original snapshot manifest too, using the existing canonical
    # format, without changing its identity or calling the core calculation.
    manifest = {'records': [(r.id, r.fingerprint) for r in sorted(records, key=lambda r: r.id)],
                'policy': snap.policy}
    if hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest() != snap.digest:
        raise HTTPException(409, 'Ledger snapshot digest changed')
    return context, sources


def observation_refs(observation):
    refs = set(observation.beneficiary_evidence_ids + observation.treatment_evidence_ids)
    for condition in observation.conditions.values():
        refs.update(condition.evidence_ids)
    return sorted(refs)


def source_locators(s, observation, sources):
    output, checked_documents = [], {}
    for ref in observation_refs(observation):
        record = sources.get(ref)
        if record is None:
            raise HTTPException(422, 'Observation references evidence outside the selected snapshot')
        doc = s.get(Document, record.document_id)
        if doc is None or doc.book_id != observation.book_id:
            raise HTTPException(409, 'Evidence source document is missing or outside the book')
        if doc.id not in checked_documents:
            path = Path(doc.storage_path)
            try:
                with path.open('rb') as stream:
                    raw = stream.read(20 * 1024 * 1024 + 1)
            except OSError as exc:
                raise HTTPException(409, 'Original source file is unavailable') from exc
            if len(raw) > 20 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != doc.sha256:
                raise HTTPException(409, 'Original source file content changed')
            batches = s.scalars(select(ImportBatch).where(ImportBatch.book_id == observation.book_id,
                ImportBatch.document_id == doc.id, ImportBatch.status == 'confirmed')).all()
            if not batches:
                raise HTTPException(409, 'Source has no confirmed import provenance')
            parsed_versions = []
            for batch in batches:
                try:
                    rows, errors, _, _, _ = parse_file(raw, doc.name, batch.mapping)
                except (ValueError, OSError, TypeError) as exc:
                    raise HTTPException(409, 'Original source cannot reproduce confirmed import') from exc
                if errors:
                    raise HTTPException(409, 'Original source import now has parsing errors')
                parsed_versions.append((batch.id, rows))
            checked_documents[doc.id] = parsed_versions
        matched_batches = []
        for batch_id, rows in checked_documents[doc.id]:
            candidates = [row for row in rows if row['record'].get('kind') == record.kind
                          and row['record'].get('source_id') == record.source_id]
            fingerprints = {hashlib.sha256(json.dumps(row['record'], sort_keys=True,
                ensure_ascii=False).encode()).hexdigest() for row in candidates}
            if len(fingerprints) > 1:
                raise HTTPException(409, 'Original source has conflicting records for this identity')
            # confirm_import retains the first row of an identical duplicate
            # source ID. A later equal-value row is not its original locator.
            if (candidates and candidates[0]['locator'] == record.locator
                    and fingerprints == {record.fingerprint}):
                matched_batches.append(batch_id)
        if not matched_batches:
            raise HTTPException(409, 'Source row locator does not reproduce the normalized record')
        output.append({'evidence_id': ref, 'record_id': record.id, 'source_id': record.source_id,
                       'document_id': doc.id, 'document_name': doc.name,
                       'document_sha256': doc.sha256, 'synthetic': doc.synthetic,
                       'locator': record.locator, 'record_fingerprint': record.fingerprint,
                       'confirmed_import_ids': sorted(set(matched_batches)),
                       'source_known_at': record.payload['observed_at']})
    return output


def verified_versions(s, book_id, snapshot_id):
    rows = s.scalars(select(PolicyObservationVersion).where(
        PolicyObservationVersion.book_id == book_id,
        PolicyObservationVersion.snapshot_id == snapshot_id)).all()
    groups = {}
    for row in rows:
        checked_observation(row)
        groups.setdefault(row.logical_key, []).append(row)
    for group in groups.values():
        previous = None
        for number, row in enumerate(sorted(group, key=lambda row: row.version), 1):
            if row.version != number or row.parent_version_id != previous:
                raise HTTPException(409, 'Stored annotation version chain is inconsistent')
            previous = row.id
    return rows


def latest_version(s, row):
    group = [item for item in verified_versions(s, row.book_id, row.snapshot_id)
             if item.logical_key == row.logical_key]
    if not group:
        raise HTTPException(409, 'Stored annotation version chain is missing')
    return max(group, key=lambda item: item.version)


def read_observation(s, row):
    checked_observation(row)
    book, snap = scoped_snapshot(s, row.book_id, row.snapshot_id)
    latest = latest_version(s, row)
    return {**serialize(row), 'latest': latest.id == row.id,
            'stale': snap.revision != book.revision or latest.id != row.id,
            'confirmation_notice': 'Local human assertion; not authenticated professional verification',
            'source_cutoff': snap.created_at, 'annotation_recorded_at': row.created_at}


def create_observation(s, book_id, body):
    lock_book(s, book_id)
    _, snap = scoped_snapshot(s, book_id, body.snapshot_id, current=True)
    context, sources = source_context(s, snap)
    group = [row for row in verified_versions(s, book_id, snap.id)
             if row.logical_key == body.logical_observation_key]
    existing = max(group, key=lambda row: row.version) if group else None
    if (existing is None and body.parent_version_id is not None
            or existing is not None and body.parent_version_id != existing.id):
        raise HTTPException(409, 'Observation parent is stale or does not match this version chain')
    identity = uid()
    values = body.model_dump(mode='python', exclude={
        'snapshot_id', 'logical_observation_key', 'parent_version_id', 'human_confirmation', 'comment'})
    observation = PolicyObservation(observation_id=identity, book_id=book_id, snapshot_id=snap.id, **values)
    source_locators(s, observation, sources)
    # Engine enforces topic/source scope; normal/insufficient/unsupported are
    # valid observations, not reasons to discard user-entered source conditions.
    evaluate_policy(observation, context)
    payload = observation.model_dump(mode='json')
    row = PolicyObservationVersion(id=identity, created_at=now(), book_id=book_id, snapshot_id=snap.id,
                                  logical_key=body.logical_observation_key,
                                  version=1 if existing is None else existing.version + 1,
                                  parent_version_id=body.parent_version_id, payload=payload,
                                  digest='', comment=body.comment)
    row.digest = observation_digest(row)
    s.add(row)
    s.add(Audit(book_id=book_id, action='policy.observation.appended',
                details={'observation_id': identity, 'snapshot_id': snap.id, 'digest': row.digest}))
    s.flush()
    s.commit()
    return read_observation(s, row)


def observation_digest(row):
    return value_digest({name: getattr(row, name) for name in (
        'id', 'created_at', 'book_id', 'snapshot_id', 'logical_key', 'version',
        'parent_version_id', 'payload', 'comment')})


def checked_observation(row):
    if observation_digest(row) != row.digest:
        raise HTTPException(409, 'Stored observation content changed')
    try:
        observation = PolicyObservation.model_validate(row.payload)
    except ValueError as exc:
        raise HTTPException(409, 'Stored observation is invalid') from exc
    if (observation.observation_id != row.id or observation.book_id != row.book_id
            or observation.snapshot_id != row.snapshot_id):
        raise HTTPException(409, 'Stored observation scope changed')
    return observation


def check_observation(s, row):
    lock_book(s, row.book_id)
    _, snap = scoped_snapshot(s, row.book_id, row.snapshot_id, current=True)
    if latest_version(s, row).id != row.id:
        raise HTTPException(409, 'Observation version was superseded')
    observation = checked_observation(row)
    context, sources = source_context(s, snap)
    locators = source_locators(s, observation, sources)
    identity = policy_rule_identity()
    key = f"{identity['version']}:{identity['source_sha256']}"
    # Integrity-check every historical result for this exact annotation before
    # deciding a different pack identity justifies a fresh execution.
    for saved in s.scalars(select(PolicyCheckRun).where(PolicyCheckRun.observation_id == row.id)):
        read_check(s, saved)
    existing = s.scalar(select(PolicyCheckRun).where(
        PolicyCheckRun.observation_id == row.id, PolicyCheckRun.pack_key == key))
    if existing:
        if existing.observation_digest != row.digest:
            raise HTTPException(409, 'Saved check observation digest is inconsistent')
        return read_check(s, existing)
    result = evaluate_policy(observation, context)
    binding = {'ledger_snapshot_id': snap.id, 'ledger_snapshot_digest': snap.digest,
               'book_revision': snap.revision, 'framework': context.framework, 'period': context.period,
               'source_cutoff': snap.created_at, 'observation_version': row.version,
               'observation_id': row.id, 'observation_digest': row.digest,
               'annotation_recorded_at': row.created_at, 'policy_rule_identity': identity,
               'observation': observation.model_dump(mode='json'), 'evidence_locators': locators,
               'annotation_envelope': {name: getattr(row, name) for name in (
                   'id', 'created_at', 'book_id', 'snapshot_id', 'logical_key', 'version',
                   'parent_version_id', 'payload', 'comment')},
               'result_sha256': value_digest(result),
               'confirmation_scope': 'Local user assertion about frozen source records; not historical knowledge or professional approval'}
    run = PolicyCheckRun(id=uid(), created_at=now(), book_id=row.book_id, snapshot_id=snap.id,
                         observation_id=row.id, pack_key=key, observation_digest=row.digest,
                         artifact_digest='', binding=binding, result=result)
    run.artifact_digest = artifact_digest(run)
    s.add(run)
    s.flush()
    s.add(Audit(book_id=row.book_id, action='policy.check.completed',
                details={'check_id': run.id, 'observation_id': row.id, 'pack_key': key}))
    s.commit()
    return read_check(s, run)


def artifact_digest(run):
    return value_digest({name: getattr(run, name) for name in (
        'id', 'created_at', 'book_id', 'snapshot_id', 'observation_id', 'pack_key',
        'observation_digest', 'binding', 'result')})


def read_check(s, run):
    if artifact_digest(run) != run.artifact_digest:
        raise HTTPException(409, 'Saved policy check artifact content changed')
    row = s.get(PolicyObservationVersion, run.observation_id)
    if row is None or row.book_id != run.book_id or row.snapshot_id != run.snapshot_id:
        raise HTTPException(409, 'Saved policy check annotation is missing or cross scoped')
    checked_observation(row)
    binding = run.binding
    if (binding.get('observation_id') != row.id or binding.get('observation_digest') != row.digest
            or binding.get('ledger_snapshot_id') != run.snapshot_id
            or binding.get('observation') != row.payload
            or value_digest(binding.get('annotation_envelope')) != row.digest
            or run.observation_digest != row.digest
            or value_digest(run.result) != binding.get('result_sha256')):
        raise HTTPException(409, 'Saved policy check binding or output content changed')
    book, snap = scoped_snapshot(s, run.book_id, run.snapshot_id)
    saved_identity = binding.get('policy_rule_identity', {})
    saved_key = f"{saved_identity.get('version')}:{saved_identity.get('source_sha256')}"
    if (saved_key != run.pack_key or run.result.get('policy_rule_identity') != saved_identity
            or binding.get('ledger_snapshot_digest') != snap.digest
            or binding.get('book_revision') != snap.revision
            or binding.get('framework') != snap.policy.get('framework')
            or binding.get('period') != snap.policy.get('period')):
        raise HTTPException(409, 'Saved policy check execution scope or identity changed')
    identity = policy_rule_identity()
    key = f"{identity['version']}:{identity['source_sha256']}"
    return {**serialize(run), 'stale': snap.revision != book.revision or latest_version(s, row).id != row.id or run.pack_key != key,
            'pack_compatible': run.pack_key == key,
            'historical_result': True, 'ledger_writes': 0,
            'notice': 'Saved bounded program observation; human conditions are assertions, not professional approval or ledger adjustment'}
