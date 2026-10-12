"""Read-only, allowlisted historical component summaries; no model dispatch."""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .model_gateway import strict_json

REPORTS_DIR = Path(__file__).resolve().parents[2] / 'docs'
PILOTS = (
    ('finqa-v1', '首轮程序字符串', '首32题 · 开发试跑', 'finqa-model-pilot-v1-summary.json'),
    ('finqa-v2', '结构化步骤', '同32题 · 开发复测', 'finqa-model-pilot-v2-summary.json'),
    ('finqa-v2-next32', '结构化步骤 · 下一组', '下一组32题 · 公开dev', 'finqa-model-pilot-v2-next32-summary.json'),
)
router = APIRouter(prefix='/api/v1/evaluations', tags=['evaluations'])


class SummaryModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore', allow_inf_nan=False)


class Provenance(SummaryModel):
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    created_at: str = Field(max_length=100)

    @field_validator('created_at')
    @classmethod
    def dated(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError('Historical result requires a timezone')
        return value


class ReplaySummary(Provenance):
    schema_version: str
    metric_scope: str
    calculator_version: str = Field(pattern=r'^[A-Za-z0-9._-]{1,100}$')
    records: int = Field(ge=1, le=883)
    executed: int = Field(ge=0, le=883)
    exact_matches_at_5_decimal_places: int = Field(ge=0, le=883)
    model_requests: int = Field(ge=0)

    @model_validator(mode='after')
    def consistent(self):
        if (self.schema_version != 'finqa-reference-program-replay-v1'
                or self.metric_scope != 'arithmetic_execution_of_supplied_official_dev_programs_not_model_QA'
                or self.exact_matches_at_5_decimal_places > self.executed or self.executed > self.records
                or self.model_requests != 0):
            raise ValueError('Not a consistent reference-program replay')
        return self


class PilotCounts(SummaryModel):
    samples: int = Field(ge=1, le=883)
    scorable: int = Field(ge=0, le=883)
    unscorable: int = Field(ge=0, le=883)
    answered: int = Field(ge=0, le=883)
    abstained: int = Field(ge=0, le=883)
    failed: int = Field(ge=0, le=883)
    exact_matches: int = Field(ge=0, le=883)
    exact_match_rate: float | None
    tokens: int = Field(ge=0)

    @model_validator(mode='after')
    def consistent(self):
        if (self.scorable + self.unscorable != self.samples
                or self.answered + self.abstained > self.samples
                or self.failed > self.samples or self.exact_matches > self.scorable
                or self.exact_matches > self.answered):
            raise ValueError('Invalid historical sample counts')
        expected = self.exact_matches / self.scorable if self.scorable else None
        if ((expected is None) != (self.exact_match_rate is None)
                or expected is not None and not math.isclose(expected, self.exact_match_rate, rel_tol=0, abs_tol=1e-12)):
            raise ValueError('Rate does not match scored denominator')
        return self


class PilotSummary(Provenance):
    schema_version: str
    status: str
    split: str
    prompt_version: str = Field(pattern=r'^[A-Za-z0-9._-]{1,100}$')
    paid_requests: int = Field(ge=0, le=5000)
    summary: PilotCounts

    @model_validator(mode='after')
    def terminal(self):
        if (self.schema_version != 'finqa-numeric-model-pilot-v1'
                or self.status not in {'completed', 'completed_with_failures'} or self.split != 'dev'):
            raise ValueError('Not a terminal dev component pilot')
        return self


def _read(name: str) -> tuple[dict, str]:
    with (REPORTS_DIR / name).open('rb') as stream:
        payload = stream.read(256_001)
    if len(payload) > 256_000:
        raise ValueError('Historical summary exceeds limit')
    value = strict_json(payload.decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Summary must be an object')
    return value, hashlib.sha256(payload).hexdigest()


def _price(raw: dict, counts: PilotCounts, paid_requests: int) -> tuple[str | None, str | None]:
    cost = raw.get('list_price_estimate')
    if (not isinstance(cost, dict) or cost.get('complete_cost_coverage') is not True
            or cost.get('unknown_attempts') != 0 or cost.get('physical_attempts') != paid_requests
            or cost.get('samples') != counts.samples):
        return None, None
    values = [cost.get('known_usage_minimum_usd'), cost.get('known_usage_maximum_usd')]
    if any(not isinstance(v, str) or not re.fullmatch(r'\d{1,10}(?:\.\d{1,18})?', v) for v in values):
        return None, None
    if Decimal(values[0]) > Decimal(values[1]):
        return None, None
    return values[0], values[1]


@router.get('/numeric')
def numeric_evaluations():
    result = {'arithmetic_replay': None, 'model_pilots': [], 'issues': [], 'scope_notes': [
        '给定参考程序的算术重放，不是模型读题或会计准确率。',
        '首轮与结构化步骤复测是同一批公开dev题目，存在开发反馈；下一组也不是封存测试。',
        '错误、保留判断进入已评分分母；没有参考答案的样本另列，不能当作答错或答对。',
        '来源编号可定位和算术通过，不保证年份、单位、公式或业务语义正确。',
        '只读取随项目保存的历史摘要；刷新不运行模型，不代表新验证。费用是已知用量列表价格区间。',
    ]}
    entries = [('arithmetic-replay', 'finqa-program-replay-v3-summary.json'),
               *[(identity, filename) for identity, _, _, filename in PILOTS]]
    for identity, filename in entries:
        try:
            raw, digest = _read(filename)
            if identity == 'arithmetic-replay':
                summary = ReplaySummary.model_validate(raw)
                result['arithmetic_replay'] = {'calculator_version': summary.calculator_version,
                    'records': summary.records, 'executed': summary.executed,
                    'exact_matches': summary.exact_matches_at_5_decimal_places,
                    'model_requests': summary.model_requests, 'source_sha256': summary.source_sha256,
                    'created_at': summary.created_at, 'summary_sha256': digest}
            else:
                summary = PilotSummary.model_validate(raw)
                _, title, group, _ = next(entry for entry in PILOTS if entry[0] == identity)
                minimum, maximum = _price(raw, summary.summary, summary.paid_requests)
                result['model_pilots'].append({'id': identity, 'title': title, 'sample_group': group,
                    'prompt_version': summary.prompt_version, **summary.summary.model_dump(),
                    'paid_requests': summary.paid_requests, 'created_at': summary.created_at,
                    'source_sha256': summary.source_sha256, 'summary_sha256': digest,
                    'cost_min_usd': minimum, 'cost_max_usd': maximum})
        except FileNotFoundError:
            result['issues'].append({'report_id': identity, 'code': 'not_available'})
        except (OSError, ValueError, UnicodeError):
            result['issues'].append({'report_id': identity, 'code': 'invalid_or_unreadable'})
    return result
