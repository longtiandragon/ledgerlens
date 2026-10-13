from typing import Literal
from pydantic import Field, field_validator
from .schemas import LedgerInput, StrictModel


class OcrReviewBase(StrictModel):
    expected_parent_id: str | None = None
    expected_result_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_book_revision: int = Field(ge=0)
    confirmed: Literal[True]
    comment: str = Field(min_length=1, max_length=2000)

    @field_validator('confirmed', mode='before')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('必须明确确认已人工核实，不能以数字或文字代替')
        return value


class OcrRecordInput(StrictModel):
    record: LedgerInput
    field_evidence: dict[str, list[str]] = Field(max_length=20)


class OcrReviewInput(OcrReviewBase, OcrRecordInput):
    """Retained single-record request and historical payload contract."""


class OcrBatchReviewInput(OcrReviewBase):
    schema_version: Literal['ocr-batch-review-v1']
    entries: list[OcrRecordInput] = Field(min_length=1, max_length=100)
