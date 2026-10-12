"""Hand-authored policy outcome/bindings; no production financial oracle.

Only the current pack identity is imported as protocol metadata. Expected
inventory treatment and the source paths below are independent literal values.
"""
from copy import deepcopy
import hashlib
import json

from app.policy_consistency import policy_rule_identity


def checksum(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def frozen_payload(*, run_id='context-run', book_id='context-book', snapshot_id='context-snapshot',
                   snapshot_digest='e' * 64, evidence_id='context-evidence', locator=None, reported='fixed_asset'):
    pack = policy_rule_identity()
    observation = {'observation_id': 'context-annotation', 'book_id': book_id, 'snapshot_id': snapshot_id,
        'topic': 'resale_inventory', 'conditions': {name: {
            'value': 'yes', 'confirmation': 'human_confirmed', 'evidence_ids': [evidence_id]}
            for name in ('ordinary_course', 'held_for_sale', 'economic_benefits_probable', 'cost_reliably_measurable')},
        'beneficiary': 'unknown', 'beneficiary_confirmation': 'unconfirmed', 'beneficiary_evidence_ids': [],
        'reported_treatment': reported, 'treatment_evidence_ids': [evidence_id]}
    annotation = {'id': observation['observation_id'], 'created_at': '2026-10-03T00:00:00+00:00',
        'book_id': book_id, 'snapshot_id': snapshot_id, 'logical_key': 'handwritten-resale', 'version': 1,
        'parent_version_id': None, 'payload': observation, 'comment': 'Hand-authored local assertion'}
    result = {'rule_id': 'POLICY_CAS_RESALE_INVENTORY', 'title': '日常购进待售商品的存货确认',
        'status': 'normal' if reported == 'inventory' else 'supported', 'expected_treatment': 'inventory',
        'reported_treatment': reported, 'evidence_ids': [evidence_id], 'missing_conditions': [],
        'reason': '独立手写预期：四项条件确认时应为存货。', 'policy_rule_identity': pack,
        'source': {'source_id': 'mof-qa-3861813',
            'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/chzzsswd/202301/t20230103_3861813.htm',
            'publication_date': '2023-01-03', 'locator': 'div.TRS_Editor',
            'source_sha256': 'c2a124f48ff46df0807a46da04de6ceaba341a9401e0d12318ed7a76535d4837'},
        'scope_note': '仅为独立手写有限政策检查，非专业批准。'}
    locator = deepcopy(locator) if locator is not None else {
        'evidence_id': evidence_id, 'record_id': 'context-record', 'source_id': 'context-source',
        'document_id': 'context-document', 'document_name': 'order.json', 'document_sha256': '2' * 64,
        'synthetic': True, 'locator': {'row': 1}, 'record_fingerprint': '3' * 64,
        'source_known_at': '2026-10-01T00:00:00+00:00', 'confirmed_import_ids': ['context-import']}
    binding = {'ledger_snapshot_id': snapshot_id, 'ledger_snapshot_digest': snapshot_digest,
        'book_revision': 1, 'framework': 'CAS', 'period': '2026-09',
        'source_cutoff': '2026-10-02T00:00:00+00:00', 'observation_version': 1,
        'observation_id': observation['observation_id'], 'observation_digest': checksum(annotation),
        'annotation_recorded_at': annotation['created_at'], 'annotation_envelope': annotation,
        'policy_rule_identity': pack, 'observation': observation, 'evidence_locators': [locator],
        'result_sha256': checksum(result), 'confirmation_scope': 'Local assertion; not professional approval'}
    check = {'id': 'context-check', 'created_at': '2026-10-04T00:00:00+00:00',
        'observation_id': observation['observation_id'], 'pack_key': f"{pack['version']}:{pack['source_sha256']}",
        'observation_digest': checksum(annotation), 'binding': binding, 'result': result}
    check['artifact_digest'] = checksum({**check, 'book_id': book_id, 'snapshot_id': snapshot_id})
    return {'schema_version': 'investigation-policy-context-v1', 'run_id': run_id, 'book_id': book_id,
        'snapshot_id': snapshot_id, 'snapshot_digest': snapshot_digest,
        'captured_at': '2026-10-05T00:00:00+00:00', 'policy_rule_identity': pack, 'checks': [check],
        'observation_manifest': [{'id': observation['observation_id'], 'digest': checksum(annotation),
            'logical_key': annotation['logical_key'], 'version': 1}],
        'notice': '有限政策检查；标注、捕获与原资料可见时间分开。'}
