"""Read-only, validated root-cause track summary; no model dispatch (Apache-2.0)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SUMMARY = Path(__file__).resolve().parents[2] / 'docs' / 'root-cause-results.json'
router = APIRouter(prefix='/api/v1/evaluations', tags=['evaluations'])


class _Model(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore', allow_inf_nan=False)


class MethodMetrics(_Model):
    samples: int = Field(ge=0)
    failures: int = Field(ge=0)
    category_macro_f1: float | None = Field(default=None, ge=0, le=1)
    conclusion_accuracy: float | None = Field(default=None, ge=0, le=1)
    decisive_evidence_obtained: str = Field(pattern=r'^\d+/\d+$')

    @model_validator(mode='after')
    def consistent(self):
        obtained, needed = (int(x) for x in self.decisive_evidence_obtained.split('/'))
        if self.failures > self.samples or obtained > needed:
            raise ValueError('Inconsistent counts')
        return self


class Run(_Model):
    id: str = Field(pattern=r'^[a-z0-9-]{1,80}$')
    label: str = Field(max_length=120)
    split: str = Field(pattern=r'^(development|validation|sealed_test)$')
    role: str = Field(pattern=r'^(baseline|smoke|final)$')
    validity: str = Field(max_length=300)
    report: str = Field(max_length=200)
    report_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    repeat: int = Field(ge=1)
    paid_requests: int = Field(ge=0)
    offpeak_cost_cny_estimate: str | None = None
    status: str = Field(max_length=60)
    methods: dict[str, MethodMetrics]


@router.get('/root-cause')
def root_cause_results():
    try:
        raw = json.loads(SUMMARY.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'status': 'unavailable', 'runs': [], 'notice': '原因调查摘要不存在或无法解析；未补零或推断结果。'}
    if not isinstance(raw, dict) or raw.get('schema_version') != 'root-cause-results-v1' or not isinstance(raw.get('runs'), list):
        return {'status': 'invalid', 'runs': [], 'notice': '原因调查摘要结构不符合约定。'}
    runs = []
    for item in raw['runs']:
        try:
            runs.append({**Run.model_validate(item).model_dump(), 'summary_status': 'verified'})
        except ValidationError:
            # A damaged entry is isolated and shown, never silently dropped or repaired.
            identity = item.get('id') if isinstance(item, dict) and isinstance(item.get('id'), str) else 'unknown'
            runs.append({'id': re.sub(r'[^a-z0-9-]', '', identity)[:80] or 'unknown', 'summary_status': 'invalid'})
    return {'status': 'available', 'track_version': raw.get('track_version'), 'dataset_version': raw.get('dataset_version'),
            'conclusion': raw.get('conclusion'), 'limitations': raw.get('limitations', []), 'runs': runs,
            'notice': '合成数据上的研究结果；失败计入分母，费用为列表价估算。'}
