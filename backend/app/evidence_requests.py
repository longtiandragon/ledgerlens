"""Evidence requests and human-confirmed fulfilment from later snapshots (Apache-2.0).

A request names a record kind and, optionally, a reference visible in the case's
snapshot. Records that appear in a LATER snapshot of the same book with that kind
and the same order/invoice chain are offered as candidates. Nothing is accepted
automatically: a person confirms the exact records, recorded as an appended event.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .db import Audit, Case, EvidenceRequest, EvidenceRequestEvent, Run, Snapshot, serialize, session
from .imports import snapshot_records

router = APIRouter(prefix='/api/v1', tags=['evidence requests'])
Kind = Literal['order', 'receipt', 'invoice', 'bank', 'journal', 'opening', 'trial_balance']
# Rule -> kind of evidence that can resolve it; suggestions only, never auto-created.
SUGGESTED_KIND = {'RECEIPT_GAP': 'receipt', 'RECEIPT_EXCESS': 'order', 'INVOICE_EVIDENCE': 'invoice',
                  'INVOICE_CONFLICT': 'invoice', 'OPENING_REQUIRED': 'opening', 'TRIAL_BALANCE': 'trial_balance',
                  'BANK_UNMATCHED': 'journal', 'LEDGER_UNMATCHED': 'bank'}


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RequestInput(_Strict):
    kind: Kind
    reference: str | None = Field(default=None, min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)


class FulfilInput(_Strict):
    snapshot_id: str = Field(min_length=1, max_length=64)
    record_ids: list[str] = Field(min_length=1, max_length=200)
    comment: str = Field(default='', max_length=2000)


class WithdrawInput(_Strict):
    comment: str = Field(min_length=1, max_length=2000)


def _ids(record: dict) -> set:
    return {record.get(key) for key in ('order_id', 'invoice_id', 'reference', 'source_id')} - {None, ''}


def chains(reference: str | None, visible: list[dict]) -> set | None:
    """Order/invoice chains named by a visible reference; None means unrestricted."""
    if reference is None:
        return None
    named = [r for r in visible if reference in _ids(r)]
    return {value for r in named for value in (r.get('order_id'), r.get('invoice_id')) if value} | {reference}


def candidates(kind: str, reference: str | None, before: list[dict], after: list[dict]) -> list[dict]:
    """Records new in `after` (by id) of the requested kind and chain."""
    known = {r['id'] for r in before}
    scope = chains(reference, before)
    return [r for r in after if r['id'] not in known and r.get('kind') == kind
            and (scope is None or bool({r.get('order_id'), r.get('invoice_id'), *_ids(r)} & scope))]


def _latest_snapshot(s, book_id: str):
    return s.scalars(select(Snapshot).where(Snapshot.book_id == book_id).order_by(Snapshot.created_at.desc())).first()


def _state(s, request: EvidenceRequest) -> dict:
    events = s.scalars(select(EvidenceRequestEvent).where(EvidenceRequestEvent.request_id == request.id)
                       .order_by(EvidenceRequestEvent.created_at)).all()
    status = events[-1].action if events else 'open'
    result = {**serialize(request), 'status': status, 'events': [serialize(e) for e in events], 'candidates': [],
              'candidate_snapshot_id': None}
    if status == 'open':
        origin = s.get(Snapshot, request.snapshot_id)
        latest = _latest_snapshot(s, request.book_id)
        if origin and latest and latest.id != origin.id and latest.created_at >= origin.created_at:
            found = candidates(request.kind, request.reference, snapshot_records(s, origin), snapshot_records(s, latest))
            result['candidates'] = [{'record_id': r['id'], 'evidence_id': r.get('evidence_id'), 'kind': r.get('kind'),
                                     'source_id': r.get('source_id'), 'business_date': r.get('business_date'),
                                     'amount': r.get('amount')} for r in found]
            result['candidate_snapshot_id'] = latest.id
    return result


def requests_for_case(s, case_id: str) -> list[dict]:
    """Current state of every request of one case, oldest first."""
    rows = s.scalars(select(EvidenceRequest).where(EvidenceRequest.case_id == case_id)
                     .order_by(EvidenceRequest.created_at)).all()
    return [_state(s, r) for r in rows]


def _case(s, case_id: str) -> Case:
    item = s.get(Case, case_id)
    if item is None:
        raise HTTPException(404, '案件不存在')
    return item


@router.get('/cases/{case_id}/evidence-requests')
def list_requests(case_id: str, s=Depends(session)):
    item = _case(s, case_id)
    rows = s.scalars(select(EvidenceRequest).where(EvidenceRequest.case_id == case_id)
                     .order_by(EvidenceRequest.created_at)).all()
    rule = (item.finding or {}).get('rule_id')
    # Needs raised during investigations (rules or model) are only suggestions:
    # a person chooses the kind and registers a request; nothing is auto-created.
    raised = []
    for run in s.scalars(select(Run).where(Run.case_id == case_id).order_by(Run.created_at)):
        needs = run.result.get('missing_evidence') if isinstance(run.result, dict) else None
        for need in needs if isinstance(needs, list) else []:
            if isinstance(need, str) and need.strip() and need not in raised:
                raised.append(need[:1000])
    return {'requests': [_state(s, r) for r in rows], 'raised_needs': raised[:20],
            'suggestion': {'kind': SUGGESTED_KIND[rule], 'rule_id': rule} if rule in SUGGESTED_KIND else None,
            'notice': '候选资料来自同一账套较新的快照；必须人工确认具体记录后才记为已取得。'}


@router.post('/cases/{case_id}/evidence-requests', status_code=201)
def create_request(case_id: str, body: RequestInput, s=Depends(session)):
    item = _case(s, case_id)
    snap = s.get(Snapshot, item.snapshot_id)
    if body.reference is not None and not any(body.reference in _ids(r) for r in snapshot_records(s, snap)):
        raise HTTPException(422, '编号必须是案件快照中可见的订单、发票或来源编号')
    row = EvidenceRequest(book_id=item.book_id, case_id=item.id, snapshot_id=item.snapshot_id, kind=body.kind,
                          reference=body.reference, description=body.description, origin='human')
    s.add(row)
    s.add(Audit(book_id=item.book_id, action='evidence_request.created', details={'case_id': item.id, 'kind': body.kind}))
    s.commit()
    return _state(s, row)


def _open_request(s, request_id: str) -> EvidenceRequest:
    request = s.get(EvidenceRequest, request_id)
    if request is None:
        raise HTTPException(404, '补证事项不存在')
    if _state(s, request)['status'] != 'open':
        raise HTTPException(409, '补证事项已关闭；结果不可改写')
    return request


@router.post('/evidence-requests/{request_id}/fulfil')
def fulfil(request_id: str, body: FulfilInput, s=Depends(session)):
    request = _open_request(s, request_id)
    state = _state(s, request)
    offered = {c['record_id'] for c in state['candidates']}
    if body.snapshot_id != state['candidate_snapshot_id'] or not set(body.record_ids) <= offered:
        raise HTTPException(409, '只能确认当前最新快照中由服务端给出的候选记录')
    s.add(EvidenceRequestEvent(request_id=request.id, action='fulfilled', snapshot_id=body.snapshot_id,
                               record_ids=sorted(set(body.record_ids)), comment=body.comment))
    s.add(Audit(book_id=request.book_id, action='evidence_request.fulfilled',
                details={'request_id': request.id, 'snapshot_id': body.snapshot_id}))
    s.commit()
    return _state(s, request)


@router.post('/evidence-requests/{request_id}/withdraw')
def withdraw(request_id: str, body: WithdrawInput, s=Depends(session)):
    request = _open_request(s, request_id)
    s.add(EvidenceRequestEvent(request_id=request.id, action='withdrawn', snapshot_id=None, record_ids=[],
                               comment=body.comment))
    s.commit()
    return _state(s, request)
