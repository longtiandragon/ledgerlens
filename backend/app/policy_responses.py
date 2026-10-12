"""Typed read contracts for independently versioned policy observations/checks."""
from typing import Literal

from pydantic import Field

from .policy_contract import PolicyModel, Topic, Treatment, PolicyObservation


class PolicyPackIdentity(PolicyModel):
    version: str
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class PolicySource(PolicyModel):
    source_id: str
    url: str
    publication_date: str
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    locator: str


class PolicyResult(PolicyModel):
    rule_id: str
    title: str
    status: Literal['normal', 'supported', 'insufficient', 'unsupported']
    expected_treatment: Treatment | None
    reported_treatment: Treatment
    evidence_ids: list[str]
    missing_conditions: list[str]
    reason: str
    policy_rule_identity: PolicyPackIdentity
    source: PolicySource
    scope_note: str


class PolicyRule(PolicyModel):
    rule_id: str
    framework: Literal['CAS', 'SCAS']
    topic: Topic
    title: str
    source_id: str
    publication_date: str
    url: str
    source_sha256: str
    applicable_conditions: str
    unsupported_conditions: list[str]
    known_limits: list[str]
    treatment_captions: dict[str, str]
    source: PolicySource
    required_conditions: list[str]
    version: str
    policy_rule_identity: PolicyPackIdentity
    validation_status: str
    status: str
    reference_available_from: str
    effective_from: str | None
    effective_to: str | None
    scope_note: str


class PolicyCatalogResponse(PolicyModel):
    policy_rule_identity: PolicyPackIdentity
    rules: list[PolicyRule]
    scope: str


class PolicyObservationResponse(PolicyModel):
    id: str
    created_at: str
    book_id: str
    snapshot_id: str
    logical_key: str
    version: int
    parent_version_id: str | None
    payload: PolicyObservation
    digest: str
    comment: str
    latest: bool
    stale: bool
    confirmation_notice: str
    source_cutoff: str
    annotation_recorded_at: str


class PolicyEvidenceLocator(PolicyModel):
    evidence_id: str
    record_id: str
    source_id: str
    document_id: str
    document_name: str
    document_sha256: str
    synthetic: bool
    locator: dict
    record_fingerprint: str
    source_known_at: str
    confirmed_import_ids: list[str]


class PolicyAnnotationEnvelope(PolicyModel):
    id: str
    created_at: str
    book_id: str
    snapshot_id: str
    logical_key: str
    version: int
    parent_version_id: str | None
    payload: PolicyObservation
    comment: str


class PolicyCheckBinding(PolicyModel):
    ledger_snapshot_id: str
    ledger_snapshot_digest: str
    book_revision: int
    framework: Literal['CAS', 'SCAS']
    period: str
    source_cutoff: str
    observation_version: int
    observation_id: str
    observation_digest: str
    annotation_recorded_at: str
    annotation_envelope: PolicyAnnotationEnvelope
    policy_rule_identity: PolicyPackIdentity
    observation: PolicyObservation
    evidence_locators: list[PolicyEvidenceLocator]
    result_sha256: str
    confirmation_scope: str


class PolicyCheckResponse(PolicyModel):
    id: str
    created_at: str
    book_id: str
    snapshot_id: str
    observation_id: str
    pack_key: str
    observation_digest: str
    artifact_digest: str
    binding: PolicyCheckBinding
    result: PolicyResult
    stale: bool
    pack_compatible: bool
    historical_result: bool
    ledger_writes: Literal[0]
    notice: str
