"""Read-only public retrieval summaries; never load evaluators or query truth (Apache-2.0)."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DOCS = Path(__file__).resolve().parents[2] / 'docs'
router = APIRouter(prefix='/api/v1/evaluations', tags=['evaluations'])


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore', allow_inf_nan=False)


class Metrics(Strict):
    answerable: int = Field(ge=1)
    unanswerable: int = Field(ge=1)
    accepted_answerable: int = Field(ge=0)
    rejected_unanswerable: int = Field(ge=0)
    accepted_unanswerable: int = Field(ge=0)
    correct_hit_at_6_kept: int = Field(ge=0)
    correct_hit_at_6_lost: int = Field(ge=0)
    failures: int = Field(ge=0)
    balanced_answerability_accuracy: float = Field(ge=0, le=1)


class Decision(Strict):
    query_id: str = Field(min_length=1, max_length=100)
    no_answer: bool
    accepted: bool
    failed: bool
    correct_hit_at_6: bool


class AbstentionMethod(Strict):
    threshold: float
    heldout: Metrics
    decisions: list[Decision] = Field(min_length=24, max_length=24)
    candidate_meets_gate: bool


class Abstention(Strict):
    schema_version: Literal['reference-abstention-summary-v1']
    status: Literal['completed']
    engine_scope: Literal['Local BGE exact NumPy cosine; no PostgreSQL claim']
    report_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    methods: dict[str, AbstentionMethod]
    model_requests: int = Field(ge=0, le=0)
    ledger_writes: int = Field(ge=0, le=0)
    default_changed: bool


def require(condition: bool):
    if not condition:
        raise ValueError('Inconsistent public summary')


def abstention_rows(raw: dict) -> list[dict]:
    summary = Abstention.model_validate(raw)
    require(set(summary.methods) == {'keyword', 'bge_numpy'} and not summary.default_changed)
    rows = []
    identities = None
    for name, method in summary.methods.items():
        decisions = method.decisions
        labels = {d.query_id: d.no_answer for d in decisions}
        require(len(labels) == 24 and (identities is None or identities == labels))
        identities = labels
        require(all(not (d.no_answer and d.correct_hit_at_6) and
                    not (d.failed and (d.accepted or d.correct_hit_at_6)) for d in decisions))
        answerable = sum(not d.no_answer for d in decisions)
        unanswerable = sum(d.no_answer for d in decisions)
        require(answerable == unanswerable == 12)
        counts = {
            'answerable': answerable, 'unanswerable': unanswerable,
            'accepted_answerable': sum(not d.failed and not d.no_answer and d.accepted for d in decisions),
            'rejected_unanswerable': sum(not d.failed and d.no_answer and not d.accepted for d in decisions),
            'accepted_unanswerable': sum(not d.failed and d.no_answer and d.accepted for d in decisions),
            'correct_hit_at_6_kept': sum(d.correct_hit_at_6 and d.accepted for d in decisions),
            'correct_hit_at_6_lost': sum(d.correct_hit_at_6 and not d.accepted for d in decisions),
            'failures': sum(d.failed for d in decisions),
        }
        metrics = method.heldout.model_dump()
        require(all(metrics[k] == value for k, value in counts.items()))
        accuracy = (counts['accepted_answerable'] / answerable + counts['rejected_unanswerable'] / unanswerable) / 2
        require(math.isclose(metrics['balanced_answerability_accuracy'], accuracy, abs_tol=1e-12))
        # Frozen v1 gate. Failed queries remain in the denominators and can never pass.
        gate = (counts['failures'] == 0 and counts['accepted_unanswerable'] <= 1 and
                counts['correct_hit_at_6_kept'] >= 9 and counts['correct_hit_at_6_lost'] <= 1)
        require(method.candidate_meets_gate == gate)
        rows.append({'method': name, 'threshold': method.threshold, **counts, 'candidate_meets_gate': gate})
    return rows


class Scores(Strict):
    no_answer: bool
    first_relevant_rank: int | None = Field(default=None, ge=1, le=6)
    hit_at_6: bool | None = None
    returned_count: int | None = Field(default=None, ge=0, le=6)
    empty_result: bool | None = None


class Case(Strict):
    query_id: str = Field(min_length=1, max_length=100)
    no_answer: bool
    keyword_scores: Scores
    bge_pgvector_scores: Scores


class Totals(Strict):
    answerable_count: int = Field(ge=1)
    unanswerable_count: int = Field(ge=1)
    failed_queries: int = Field(ge=0, le=0)
    hit_at_6: float = Field(ge=0, le=1)
    no_answer_returned_candidates: int = Field(ge=0)
    no_answer_successful_empty_results: int = Field(ge=0)


class Baseline(Strict):
    schema_version: Literal['reference-retrieval-summary-v1']
    status: Literal['completed']
    report_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    totals: dict[str, Totals]
    cases: list[Case] = Field(min_length=20, max_length=20)
    pg_numpy_verified_queries: int = Field(ge=20, le=20)
    external_model_requests: int = Field(ge=0, le=0)
    ledger_writes: int = Field(ge=0, le=0)


def baseline_rows(raw: dict) -> list[dict]:
    summary = Baseline.model_validate(raw)
    require(set(summary.totals) == {'keyword', 'bge_pgvector'})
    require(len({case.query_id for case in summary.cases}) == 20)
    require(sum(case.no_answer for case in summary.cases) == 2)
    rows = []
    for name, total in summary.totals.items():
        hits = returned = 0
        for case in summary.cases:
            scores = getattr(case, name + '_scores')
            require(scores.no_answer == case.no_answer)
            if case.no_answer:
                require(scores.returned_count is not None and scores.empty_result == (scores.returned_count == 0))
                returned += scores.returned_count > 0
            else:
                require(scores.hit_at_6 is not None and scores.hit_at_6 == (scores.first_relevant_rank is not None))
                hits += scores.hit_at_6
        require(total.answerable_count == 18 and total.unanswerable_count == 2)
        require(math.isclose(total.hit_at_6, hits / 18, abs_tol=1e-12))
        require(total.no_answer_returned_candidates == returned and total.no_answer_successful_empty_results == 2 - returned)
        rows.append({'method': name, 'answerable': 18, 'unanswerable': 2, 'correct_hit_at_6': hits,
                     'returned_unanswerable': returned, 'failures': total.failed_queries})
    return rows


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def read_summary(filename: str, parse) -> dict:
    result = {'source': filename}
    try:
        with (DOCS / filename).open('rb') as stream:
            content = stream.read(1_000_001)
    except OSError:
        return {**result, 'status': 'unavailable', 'notice': '摘要缺失或无法读取；未补零或推断结果。'}
    try:
        require(len(content) <= 1_000_000)
        raw = json.loads(content, object_pairs_hook=unique_keys)
        rows = parse(raw)
    except (ValueError, ValidationError, TypeError, RecursionError):
        return {**result, 'status': 'invalid', 'notice': '摘要结构或计数校验失败；未展示推断结果。'}
    return {**result, 'status': 'available', 'rows': rows,
            'summary_sha256': hashlib.sha256(content).hexdigest(), 'report_sha256': raw['report_sha256']}


@router.get('/retrieval')
def retrieval_results():
    return {'baseline': read_summary('retrieval-validation.json', baseline_rows),
            'abstention': read_summary('retrieval-abstention-validation.json', abstention_rows),
            'notice': '只读公开历史摘要；结构与计数校验不等于独立复现或专业复核。刷新不运行检索、不调用模型。'}
