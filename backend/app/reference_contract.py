from typing import Literal
from pydantic import Field, field_validator
from .schemas import StrictModel


class ReferenceSearchInput(StrictModel):
    book_id: str
    index_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    query: str = Field(min_length=1, max_length=1000)


class ReferenceChoiceInput(StrictModel):
    book_id: str
    mode: Literal['keyword', 'persistent_bge']
    search_id: str | None = None
    expected_result_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    expected_book_revision: int = Field(ge=0)
    expected_parent_id: str | None = None
    selected_parent_ids: list[str] = Field(default_factory=list, max_length=6)
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('必须明确确认参考条文选择')
        return value
