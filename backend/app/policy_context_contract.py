"""Bounded, frozen policy-check evidence for one investigation run.

This verifies content consistency, not a signature against a local actor who
can replace every stored payload and digest. Annotation/capture times remain
separate from the ledger's source-visibility cutoff.
"""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field

from .policy_contract import PolicyModel
from .policy_responses import PolicyCheckBinding, PolicyPackIdentity, PolicyResult

POLICY_CONTEXT_SCHEMA = 'investigation-policy-context-v1'
MAX_CAPTURED_CHECKS = 60
MAX_CONTEXT_BYTES = 2 * 1024 * 1024
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class CapturedPolicyCheck(PolicyModel):
    id: Identifier
    created_at: Identifier
    observation_id: Identifier
    pack_key: Identifier
    observation_digest: Digest
    artifact_digest: Digest
    binding: PolicyCheckBinding
    result: PolicyResult


class CapturedPolicyObservation(PolicyModel):
    id: Identifier
    digest: Digest
    logical_key: Identifier
    version: int = Field(ge=1)


class InvestigationPolicyContextPayload(PolicyModel):
    schema_version: Literal['investigation-policy-context-v1']
    run_id: Identifier
    book_id: Identifier
    snapshot_id: Identifier
    snapshot_digest: Digest
    captured_at: Identifier
    policy_rule_identity: PolicyPackIdentity
    checks: list[CapturedPolicyCheck] = Field(max_length=MAX_CAPTURED_CHECKS)
    observation_manifest: list[CapturedPolicyObservation] = Field(max_length=5000)
    notice: str = Field(min_length=1, max_length=4000)


class InvestigationPolicyContextResponse(PolicyModel):
    status: Literal['captured', 'legacy_uncaptured']
    payload: InvestigationPolicyContextPayload | None
    digest: Digest | None
    stale: bool
    stale_reasons: list[str] = Field(max_length=20)
    notice: str = Field(min_length=1, max_length=4000)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def value_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Policy context timestamp must be a string')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Policy context timestamp is invalid') from exc
    if parsed.tzinfo is None:
        raise ValueError('Policy context timestamp requires a timezone')
    return parsed


def _json_bounds(value):
    """Reject non-JSON values/cycles before typed and digest checks."""
    nodes, active = 0, set()

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 100000 or depth > 20:
            raise ValueError('Policy context JSON structure exceeds bounds')
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            if not -(2 ** 63) <= item < 2 ** 63:
                raise ValueError('Policy context JSON integer exceeds bounds')
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError('Policy context JSON contains a nonfinite number')
            return
        if type(item) is str:
            if len(item) > 16384:
                raise ValueError('Policy context JSON string exceeds bounds')
            return
        if type(item) not in {dict, list} or id(item) in active:
            raise ValueError('Policy context must contain only acyclic JSON values')
        if len(item) > (5000 if type(item) is list else 200):
            raise ValueError('Policy context JSON collection exceeds bounds')
        active.add(id(item))
        if type(item) is dict:
            for key, nested in item.items():
                if type(key) is not str or not key or len(key) > 200:
                    raise ValueError('Policy context JSON key is invalid')
                visit(nested, depth + 1)
        else:
            for nested in item:
                visit(nested, depth + 1)
        active.remove(id(item))

    visit(value, 0)
    if len(canonical(value)) > MAX_CONTEXT_BYTES:
        raise ValueError('Policy context exceeds the serialized size bound')


def _refs(observation):
    references = set(observation['beneficiary_evidence_ids'] + observation['treatment_evidence_ids'])
    for condition in observation['conditions'].values():
        references.update(condition['evidence_ids'])
    return references


def validate_policy_context(payload, *, book_id, snapshot_id=None, snapshot_digest=None, evidence_ids=None):
    """Validate a frozen in-memory payload; never fetch current checks or rules."""
    _json_bounds(payload)
    parsed = InvestigationPolicyContextPayload.model_validate(payload)
    if parsed.book_id != book_id or snapshot_id is not None and parsed.snapshot_id != snapshot_id:
        raise ValueError('Policy context is outside the investigation book or snapshot')
    if snapshot_digest is not None and parsed.snapshot_digest != snapshot_digest:
        raise ValueError('Policy context ledger snapshot digest differs')
    # Reject omitted nested defaults rather than silently introducing new facts.
    normalized = parsed.model_dump(mode='json')
    if canonical(normalized) != canonical(payload):
        raise ValueError('Policy context must contain the complete saved contract')
    captured_at = timestamp(parsed.captured_at)
    manifest = {}
    logical_keys = set()
    for item in payload['observation_manifest']:
        if item['id'] in manifest or item['logical_key'] in logical_keys:
            raise ValueError('Policy observation manifest contains duplicate identity or logical key')
        manifest[item['id']] = item
        logical_keys.add(item['logical_key'])
    if payload['observation_manifest'] != sorted(payload['observation_manifest'], key=lambda item: item['logical_key']):
        raise ValueError('Policy observation manifest ordering is not canonical')
    checks, selected_observations, common_scope = set(), set(), None
    for item in payload['checks']:
        if item['id'] in checks or item['observation_id'] in selected_observations:
            raise ValueError('Policy context contains a repeated check or observation')
        checks.add(item['id'])
        selected_observations.add(item['observation_id'])
        binding, result = item['binding'], item['result']
        observation, annotation = binding['observation'], binding['annotation_envelope']
        expected_manifest = {'id': annotation['id'], 'digest': item['observation_digest'],
                             'logical_key': annotation['logical_key'], 'version': annotation['version']}
        if manifest.get(item['observation_id']) != expected_manifest:
            raise ValueError('Captured check is not bound to its latest observation manifest entry')
        if (binding['ledger_snapshot_id'] != parsed.snapshot_id
                or binding['ledger_snapshot_digest'] != parsed.snapshot_digest
                or binding['observation_id'] != item['observation_id']
                or binding['observation_digest'] != item['observation_digest']
                or annotation['id'] != item['observation_id']
                or observation['observation_id'] != item['observation_id']
                or annotation['book_id'] != parsed.book_id or observation['book_id'] != parsed.book_id
                or annotation['snapshot_id'] != parsed.snapshot_id or observation['snapshot_id'] != parsed.snapshot_id
                or canonical(annotation['payload']) != canonical(observation)
                or binding['observation_version'] != annotation['version']
                or binding['annotation_recorded_at'] != annotation['created_at']
                or value_digest(annotation) != item['observation_digest']
                or value_digest(result) != binding['result_sha256']):
            raise ValueError('Captured policy check annotation/result binding is inconsistent')
        pack = payload['policy_rule_identity']
        if (canonical(binding['policy_rule_identity']) != canonical(pack)
                or canonical(result['policy_rule_identity']) != canonical(pack)
                or item['pack_key'] != f"{pack['version']}:{pack['source_sha256']}"):
            raise ValueError('Captured policy check pack identities disagree')
        if (type(binding['book_revision']) is not int or binding['book_revision'] < 0
                or type(binding['observation_version']) is not int or binding['observation_version'] < 1
                or not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', binding['period'])):
            raise ValueError('Captured policy check revision or accounting period is invalid')
        scope = (binding['framework'], binding['period'], binding['book_revision'], binding['source_cutoff'])
        if common_scope is not None and scope != common_scope:
            raise ValueError('Captured policy checks disagree on their ledger policy or source cutoff')
        common_scope = scope
        cutoff = timestamp(binding['source_cutoff'])
        if (timestamp(annotation['created_at']) > timestamp(item['created_at'])
                or timestamp(item['created_at']) > captured_at or cutoff > captured_at):
            raise ValueError('Captured policy context contains information saved after capture')
        references = _refs(observation)
        if evidence_ids is not None and not references <= set(evidence_ids):
            raise ValueError('Captured policy check references evidence outside the fixed snapshot')
        locators = binding['evidence_locators']
        if (len(locators) > 60 or len({locator['evidence_id'] for locator in locators}) != len(locators)
                or {locator['evidence_id'] for locator in locators} != references
                or result['evidence_ids'] != sorted(references)
                or result['reported_treatment'] != observation['reported_treatment']):
            raise ValueError('Captured policy result and source references disagree')
        for locator in locators:
            if (timestamp(locator['source_known_at']) > cutoff
                    or not locator['confirmed_import_ids']
                    or locator['confirmed_import_ids'] != sorted(set(locator['confirmed_import_ids']))
                    or not re.fullmatch(r'[a-f0-9]{64}', locator['document_sha256'])
                    or not re.fullmatch(r'[a-f0-9]{64}', locator['record_fingerprint'])):
                raise ValueError('Captured policy evidence provenance is invalid')
        expected = result['expected_treatment']
        if (result['status'] == 'normal' and (expected is None or expected != result['reported_treatment'])
                or result['status'] == 'supported' and (expected is None or expected == result['reported_treatment'])
                or result['status'] == 'unsupported' and expected is not None):
            raise ValueError('Captured policy result status and reported/expected treatment disagree')
        if len(result['missing_conditions']) != len(set(result['missing_conditions'])):
            raise ValueError('Captured policy result repeats missing conditions')
        try:
            source_url = urlsplit(result['source']['url'])
            if (source_url.scheme != 'https' or source_url.hostname not in {'kjs.mof.gov.cn', 'www.mof.gov.cn'}
                    or source_url.username or source_url.password or source_url.port not in {None, 443}
                    or source_url.fragment):
                raise ValueError('Captured policy source URL is outside approved origins')
            published = date.fromisoformat(result['source']['publication_date'])
        except (ValueError, TypeError) as exc:
            raise ValueError('Captured policy source identity is invalid') from exc
        # MOF publication dates are Chinese civil days, as in evaluate_policy.
        available_at = datetime.combine(published, time.min, tzinfo=timezone(timedelta(hours=8)))
        if available_at > captured_at or (result['status'] in {'normal', 'supported'} and available_at > cutoff):
            raise ValueError('Captured policy conclusion relies on a source published after its cutoff')
        artifact = {key: item[key] for key in ('id', 'created_at', 'observation_id', 'pack_key',
                                             'observation_digest', 'binding', 'result')}
        artifact.update(book_id=parsed.book_id, snapshot_id=parsed.snapshot_id)
        if value_digest(artifact) != item['artifact_digest']:
            raise ValueError('Captured policy check artifact digest differs')
    if payload['checks'] != sorted(payload['checks'], key=lambda item: item['id']):
        raise ValueError('Captured policy check ordering is not canonical')
    return deepcopy(payload)
