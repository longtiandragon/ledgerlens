"""Snapshot reference choices; historical verification never opens today's index."""
from copy import deepcopy
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from .db import ReferenceChoice
from .knowledge import verify_frozen_references
from .reference_sources import canonical, digest, _read
from .retrieval_index import validate_manifest
from .retrieval_vectors import eligible

ROOT = Path(__file__).resolve().parents[2]
REGISTRIES = {'mof-retrieval-v1': ROOT / 'docs/retrieval-sources-v1.json'}


def registry_for(key, expected_hash):
    path = REGISTRIES.get(key)
    if path is None or digest(_read(path)) != expected_hash:
        raise ValueError('Unknown or changed trusted reference registry')
    return path


def verify_snapshot_references(policy, book_id=None):
    try:
        return _verify_snapshot_references(policy, book_id)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Malformed snapshot reference selection') from exc


def _verify_snapshot_references(policy, book_id):
    choice = policy.get('reference_choice')
    if choice is None:
        return verify_frozen_references(policy.get('reference_passages', []))
    if set(choice) != {'id', 'version', 'digest', 'payload'} or choice['digest'] != digest(canonical(choice['payload'])):
        raise ValueError('Snapshot reference choice changed')
    payload = choice['payload']
    if set(payload) != {'mode', 'registry_key', 'manifest', 'query', 'search_id', 'search_result_digest',
                       'selected_parent_ids', 'references', 'book_id', 'book_version', 'framework', 'period'}:
        raise ValueError('Invalid snapshot reference choice envelope')
    expected_book = book_id or policy.get('book_id')
    if expected_book is not None and payload['book_id'] != expected_book:
        raise ValueError('Snapshot references belong to another book')
    if (payload['mode'] != 'persistent_bge' or payload['framework'] != policy['framework']
            or payload['period'] != policy['period'] or payload['book_version'] != policy['version']):
        raise ValueError('Snapshot reference choice scope differs from policy')
    validate_manifest(payload['manifest'])
    registry = registry_for(payload['registry_key'], payload['manifest']['registry_sha256'])
    references = payload['references']
    if (not references or len(references) > 6 or canonical(references) != canonical(policy.get('reference_passages'))
            or [p['source_id'] for p in references] != payload['selected_parent_ids']):
        raise ValueError('Snapshot reference selection differs from frozen passages')
    if any(not eligible(p, policy['framework'], policy['period']) for p in references):
        raise ValueError('Frozen references fall outside the selected scope')
    return verify_frozen_references(references, registry=registry)


def latest_choice(s, book_id):
    return s.scalar(select(ReferenceChoice).where(ReferenceChoice.book_id == book_id).order_by(ReferenceChoice.version.desc()))


def apply_choice(s, book, policy):
    """Return true when an explicit current selection replaces default retrieval."""
    choice = latest_choice(s, book.id)
    if choice is None:
        return False
    if choice.digest != digest(canonical(choice.payload)):
        raise HTTPException(409, '参考资料选择记录校验失败，未建立快照')
    if choice.book_version != book.version:
        policy['reference_choice_status'] = 'expired_by_policy_change'
        return False
    if choice.payload.get('mode') == 'keyword':
        return False
    if choice.payload.get('book_id') != book.id:
        raise HTTPException(409, '参考资料选择不属于当前账套')
    policy['reference_passages'] = deepcopy(choice.payload.get('references'))
    policy['reference_choice'] = {'id': choice.id, 'version': choice.version, 'digest': choice.digest,
                                  'payload': deepcopy(choice.payload)}
    try:
        verify_snapshot_references(policy, book.id)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(409, '参考资料选择或来源绑定校验失败，未建立快照') from exc
    policy['reference_integrity'] = 'verified'
    return True
