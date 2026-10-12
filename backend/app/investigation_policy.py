"""Transaction-scoped capture and immutable loading of saved policy checks."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from .db import (Audit, Book, Case, InvestigationPolicyContext, PolicyCheckRun, now, uid)
from .policy_consistency import policy_rule_identity
from .policy_context_contract import (MAX_CAPTURED_CHECKS, POLICY_CONTEXT_SCHEMA, canonical,
                                      timestamp, validate_policy_context, value_digest)
from . import policy_runtime

NOTICE = ('保存的有限政策一致性检查是独立的程序观察；不是专业复核、完整准则覆盖或正式记账授权。'
          '条件标注及检查捕获时间独立于原账簿的资料可见截止时间。')


def context_digest(row):
    return value_digest({key: getattr(row, key) for key in (
        'id', 'created_at', 'run_id', 'book_id', 'snapshot_id', 'payload')})


def _scope(s, run, snap):
    if (run is None or snap is None or not isinstance(run.id, str) or not run.id
            or not isinstance(snap.policy, dict)):
        raise HTTPException(409, 'Investigation policy context has no valid run/snapshot scope')
    case = s.get(Case, run.case_id)
    book = s.get(Book, snap.book_id)
    if case is None or book is None or case.book_id != snap.book_id or case.snapshot_id != snap.id:
        raise HTTPException(409, 'Investigation policy context crosses its case, book or snapshot scope')
    return book


def _manifest(rows):
    groups = {}
    for row in rows:
        if row.logical_key not in groups or groups[row.logical_key].version < row.version:
            groups[row.logical_key] = row
    return [groups[key] for key in sorted(groups)]


def _manifest_payload(rows):
    return [{'id': row.id, 'digest': row.digest, 'logical_key': row.logical_key, 'version': row.version}
            for row in rows]


def _validate_saved(s, row, run, snap):
    _scope(s, run, snap)
    try:
        if (row.run_id != run.id or row.book_id != snap.book_id or row.snapshot_id != snap.id
                or not isinstance(row.payload, dict) or context_digest(row) != row.digest
                or row.payload.get('run_id') != run.id or row.payload.get('captured_at') != row.created_at):
            raise ValueError('Saved investigation policy context envelope changed')
        # Membership comes from the frozen ledger snapshot, never latest rows.
        _, sources = policy_runtime.source_context(s, snap)
        payload = validate_policy_context(row.payload, book_id=snap.book_id, snapshot_id=snap.id,
                                          snapshot_digest=snap.digest, evidence_ids=set(sources))
        for check in payload['checks']:
            binding = check['binding']
            if (binding['book_revision'] != snap.revision or binding['framework'] != snap.policy.get('framework')
                    or binding['period'] != snap.policy.get('period') or binding['source_cutoff'] != snap.created_at):
                raise ValueError('Captured check ledger policy differs from its fixed snapshot')
        return payload
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(409, 'Saved investigation policy context is invalid') from exc


def capture_policy_context(s, run, snap):
    """Capture under the caller's book lock/Run transaction; no internal commit.

    Existing rows are idempotently returned after immutable validation. A new
    capture verifies all historical checks/annotations in this exact snapshot,
    then selects only latest annotations with a saved current-pack check.
    """
    book = _scope(s, run, snap)
    existing = s.scalar(select(InvestigationPolicyContext).where(InvestigationPolicyContext.run_id == run.id))
    if existing is not None:
        _validate_saved(s, existing, run, snap)
        return existing
    captured_at = now()
    identity = policy_rule_identity()
    pack_key = f"{identity['version']}:{identity['source_sha256']}"
    try:
        _, sources = policy_runtime.source_context(s, snap)
        versions = policy_runtime.verified_versions(s, snap.book_id, snap.id)
        if len(versions) > 5000:
            raise HTTPException(409, 'Too many saved policy annotation versions; context was not truncated')
        latest = _manifest(versions)
        if any(timestamp(row.created_at) > timestamp(captured_at) for row in versions):
            raise ValueError('Saved policy annotation timestamp is after context capture')
        # Even unchecked/stale annotations must not carry foreign or changed refs.
        locators = {row.id: policy_runtime.source_locators(s, policy_runtime.checked_observation(row), sources)
                    for row in versions}
        saved = s.scalars(select(PolicyCheckRun).where(
            PolicyCheckRun.book_id == snap.book_id, PolicyCheckRun.snapshot_id == snap.id).order_by(PolicyCheckRun.id)).all()
        if len(saved) > 5000:
            raise HTTPException(409, 'Too many historical policy checks; context was not truncated')
        selected, latest_ids = [], {row.id for row in latest}
        for check in saved:
            policy_runtime.read_check(s, check)
            if (timestamp(check.created_at) > timestamp(captured_at)
                    or canonical(check.binding.get('evidence_locators')) != canonical(locators.get(check.observation_id))
                    or check.binding.get('source_cutoff') != snap.created_at):
                raise ValueError('Saved policy check sources or knowledge cutoff changed')
            item = {key: deepcopy(getattr(check, key)) for key in (
                'id', 'created_at', 'observation_id', 'pack_key', 'observation_digest',
                'artifact_digest', 'binding', 'result')}
            annotation = check.binding['annotation_envelope']
            # Validate stale/old-pack artifacts too; they must not bypass typed
            # output, timing, citation or digest checks merely by being excluded.
            validate_policy_context({
                'schema_version': POLICY_CONTEXT_SCHEMA, 'run_id': run.id, 'book_id': snap.book_id,
                'snapshot_id': snap.id, 'snapshot_digest': snap.digest, 'captured_at': captured_at,
                'policy_rule_identity': check.binding['policy_rule_identity'], 'checks': [item],
                'observation_manifest': [{'id': annotation['id'], 'digest': check.observation_digest,
                    'logical_key': annotation['logical_key'], 'version': annotation['version']}], 'notice': NOTICE},
                book_id=snap.book_id, snapshot_id=snap.id, snapshot_digest=snap.digest, evidence_ids=set(sources))
            if check.observation_id in latest_ids and check.pack_key == pack_key:
                selected.append(item)
        if len(selected) > MAX_CAPTURED_CHECKS:
            raise HTTPException(409, 'More than 60 current saved policy checks; context was not truncated')
        missing = len(latest) - len(selected)
        notice = NOTICE + f' 当前快照有 {missing} 个最新版条件尚未提供已保存的当前规则包检查，未沿用其旧版结论。'
        if book.revision != snap.revision:
            notice += ' 此调查使用历史账簿快照，未混入当前账簿的新资料。'
        payload = {'schema_version': POLICY_CONTEXT_SCHEMA, 'run_id': run.id, 'book_id': snap.book_id,
                   'snapshot_id': snap.id, 'snapshot_digest': snap.digest, 'captured_at': captured_at,
                   'policy_rule_identity': identity, 'checks': sorted(selected, key=lambda item: item['id']),
                   'observation_manifest': _manifest_payload(latest), 'notice': notice}
        payload = validate_policy_context(payload, book_id=snap.book_id, snapshot_id=snap.id,
                                          snapshot_digest=snap.digest, evidence_ids=set(sources))
    except HTTPException as exc:
        if exc.status_code == 409:
            raise
        raise HTTPException(409, 'Stored policy scope or evidence could not be verified for capture') from exc
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(409, 'Policy checks could not be captured with verified source bindings') from exc
    row = InvestigationPolicyContext(id=uid(), created_at=captured_at, run_id=run.id, book_id=snap.book_id,
                                     snapshot_id=snap.id, payload=payload, digest='')
    row.digest = context_digest(row)
    s.add(row)
    s.flush()
    return row


def load_policy_context(s, run, snap):
    """Return only the original frozen payload; legacy absence stays uncaptured."""
    _scope(s, run, snap)
    row = s.scalar(select(InvestigationPolicyContext).where(InvestigationPolicyContext.run_id == run.id))
    if row is None:
        orphan = s.scalar(select(InvestigationPolicyContext.id).where(
            InvestigationPolicyContext.payload['run_id'].as_string() == run.id))
        captured_marker = s.scalar(select(Audit.id).where(
            Audit.action == 'investigation.policy_context.captured', Audit.details['run_id'].as_string() == run.id))
        if orphan is not None or captured_marker is not None:
            raise HTTPException(409, 'Previously captured investigation policy context is missing or reassigned')
        return None
    return _validate_saved(s, row, run, snap)


def read_policy_context(s, run, snap):
    """Show immutable history plus separate current-version staleness reasons."""
    payload = load_policy_context(s, run, snap)
    if payload is None:
        return {'status': 'legacy_uncaptured', 'payload': None, 'digest': None, 'stale': False,
                'stale_reasons': [], 'notice': '旧调查没有冻结政策检查上下文；不会以当前检查回填历史。'}
    row = s.scalar(select(InvestigationPolicyContext).where(InvestigationPolicyContext.run_id == run.id))
    book = _scope(s, run, snap)
    reasons = []
    if book.revision != snap.revision:
        reasons.append('book_revision_changed')
    identity = policy_rule_identity()
    if canonical(identity) != canonical(payload['policy_rule_identity']):
        reasons.append('policy_rule_identity_changed')
    try:
        latest = _manifest(policy_runtime.verified_versions(s, snap.book_id, snap.id))
        if canonical(_manifest_payload(latest)) != canonical(payload['observation_manifest']):
            reasons.append('policy_observations_changed')
        key = f"{identity['version']}:{identity['source_sha256']}"
        latest_ids = {item.id for item in latest}
        saved = s.scalars(select(PolicyCheckRun).where(
            PolicyCheckRun.book_id == snap.book_id, PolicyCheckRun.snapshot_id == snap.id)).all()
        current_artifacts = set()
        for check in saved:
            policy_runtime.read_check(s, check)
            if check.observation_id in latest_ids and check.pack_key == key:
                current_artifacts.add((check.id, check.artifact_digest))
        if current_artifacts != {(item['id'], item['artifact_digest']) for item in payload['checks']}:
            reasons.append('policy_checks_changed')
    except (HTTPException, ValueError, TypeError, AttributeError):
        # Current corruption cannot replace or suppress intact frozen history.
        reasons.append('current_policy_state_invalid')
    return {'status': 'captured', 'payload': payload, 'digest': row.digest, 'stale': bool(reasons),
            'stale_reasons': reasons, 'notice': payload['notice']}
