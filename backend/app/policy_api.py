"""Local human annotations and read-only, version-bound policy results."""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import session, Book, PolicyObservationVersion, PolicyCheckRun
from .policy_contract import PolicyModel, Topic, Treatment, ConfirmedCondition, PolicyObservation
from .policy_consistency import policy_rule_catalog, policy_rule_identity
from .policy_responses import PolicyCatalogResponse, PolicyObservationResponse, PolicyCheckResponse
from . import policy_runtime

router = APIRouter(prefix='/api/v1', tags=['bounded policy consistency'])


class PolicyObservationInput(PolicyModel):
    snapshot_id: str = Field(min_length=1, max_length=200)
    logical_observation_key: str = Field(min_length=1, max_length=200)
    parent_version_id: str | None = Field(default=None, min_length=1, max_length=200)
    human_confirmation: bool = Field(strict=True)
    topic: Topic
    conditions: dict[str, ConfirmedCondition]
    beneficiary: Literal['inventory_production', 'administration', 'dedicated_sales', 'other', 'unknown'] = 'unknown'
    beneficiary_confirmation: Literal['human_confirmed', 'source_reported', 'model_suggested', 'unconfirmed'] = 'unconfirmed'
    beneficiary_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    reported_treatment: Treatment = 'unknown'
    treatment_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    comment: str = Field(default='', max_length=4000)

    @field_validator('human_confirmation')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('Explicit local human confirmation is required')
        return value


def scoped(s, cls, identity, book_id):
    row = s.get(cls, identity)
    if row is None or row.book_id != book_id:
        raise HTTPException(404, 'Scoped policy resource not found')
    return row


@router.get('/policy-checks/catalog', response_model=PolicyCatalogResponse)
def catalog():
    return {'policy_rule_identity': policy_rule_identity(), 'rules': policy_rule_catalog(),
            'scope': 'Archived official Q&A consistency; six sources, not full accounting compliance'}


@router.post('/books/{book_id}/policy-observations', status_code=201, response_model=PolicyObservationResponse)
def append_observation(book_id: str, body: PolicyObservationInput, s=Depends(session)):
    # Reuse the pure contract validation without trusting any scope fields from
    # the client. This validation catches unsupported predicates and missing
    # source citations before a stored version can be created.
    values = body.model_dump(mode='python', exclude={
        'snapshot_id', 'logical_observation_key', 'parent_version_id', 'human_confirmation', 'comment'})
    try:
        PolicyObservation(observation_id='validation', book_id=book_id,
                          snapshot_id=body.snapshot_id, **values)
    except ValueError as exc:
        raise HTTPException(422, 'Invalid source-bound policy observation: ' + str(exc)) from exc
    try:
        return policy_runtime.create_observation(s, book_id, body)
    except IntegrityError as exc:
        s.rollback()
        raise HTTPException(409, 'Observation version was concurrently updated') from exc


@router.get('/books/{book_id}/policy-observations', response_model=list[PolicyObservationResponse])
def observations(book_id: str, snapshot_id: str | None = None,
                 offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200),
                 s=Depends(session)):
    if s.get(Book, book_id) is None:
        raise HTTPException(404, 'Book not found')
    query = select(PolicyObservationVersion).where(PolicyObservationVersion.book_id == book_id)
    if snapshot_id is not None:
        policy_runtime.scoped_snapshot(s, book_id, snapshot_id)
        query = query.where(PolicyObservationVersion.snapshot_id == snapshot_id)
    return [policy_runtime.read_observation(s, row) for row in s.scalars(
        query.order_by(PolicyObservationVersion.created_at, PolicyObservationVersion.id).offset(offset).limit(limit))]


@router.get('/books/{book_id}/policy-observations/{identity}', response_model=PolicyObservationResponse)
def observation(book_id: str, identity: str, s=Depends(session)):
    return policy_runtime.read_observation(s, scoped(s, PolicyObservationVersion, identity, book_id))


@router.post('/books/{book_id}/policy-observations/{identity}/check', response_model=PolicyCheckResponse)
def check(book_id: str, identity: str, s=Depends(session)):
    try:
        return policy_runtime.check_observation(s, scoped(s, PolicyObservationVersion, identity, book_id))
    except IntegrityError as exc:
        s.rollback()
        raise HTTPException(409, 'Concurrent policy check; retry reading the saved result') from exc


@router.get('/books/{book_id}/policy-checks', response_model=list[PolicyCheckResponse])
def checks(book_id: str, observation_id: str | None = None, snapshot_id: str | None = None,
           offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200),
           s=Depends(session)):
    if s.get(Book, book_id) is None:
        raise HTTPException(404, 'Book not found')
    query = select(PolicyCheckRun).where(PolicyCheckRun.book_id == book_id)
    if observation_id is not None:
        scoped(s, PolicyObservationVersion, observation_id, book_id)
        query = query.where(PolicyCheckRun.observation_id == observation_id)
    if snapshot_id is not None:
        policy_runtime.scoped_snapshot(s, book_id, snapshot_id)
        query = query.where(PolicyCheckRun.snapshot_id == snapshot_id)
    return [policy_runtime.read_check(s, row) for row in s.scalars(
        query.order_by(PolicyCheckRun.created_at, PolicyCheckRun.id).offset(offset).limit(limit))]


@router.get('/books/{book_id}/policy-checks/{identity}', response_model=PolicyCheckResponse)
def read_check(book_id: str, identity: str, s=Depends(session)):
    return policy_runtime.read_check(s, scoped(s, PolicyCheckRun, identity, book_id))


@router.get('/books/{book_id}/policy-checks/{identity}/export')
def export_check(book_id: str, identity: str, s=Depends(session)):
    result = policy_runtime.read_check(s, scoped(s, PolicyCheckRun, identity, book_id))
    return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="policy-check-{identity}.json"',
                             'Cache-Control': 'no-store'})
