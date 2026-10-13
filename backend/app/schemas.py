from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def money(value):
    if isinstance(value, (float, bool)):
        raise ValueError('金额必须是十进制字符串，不能使用浮点数')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('无效金额')
    if not result.is_finite() or abs(result) > Decimal('999999999999.99'):
        raise ValueError('金额超出支持范围')
    if result != result.quantize(Decimal('.01')):
        raise ValueError('金额精度不得超过两位小数')
    return format(result, '.2f')


class BookInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    framework: Literal['CAS', 'SCAS']
    period: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    currency: Literal['CNY'] = 'CNY'
    accounts: list[str] = Field(default_factory=lambda: ['1002', '1122', '1405', '2202', '6001', '6602'])
    locked_periods: list[str] = Field(default_factory=list)


class LedgerInput(StrictModel):
    source_id: str = Field(min_length=1, max_length=200)
    kind: Literal['journal', 'bank', 'order', 'receipt', 'invoice', 'opening', 'trial_balance']
    business_date: date
    period: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    currency: Literal['CNY'] = 'CNY'
    counterparty: str = Field(default='', max_length=200)
    reference: str = Field(default='', max_length=200)
    order_id: str = Field(default='', max_length=200)
    invoice_id: str = Field(default='', max_length=200)
    voucher_id: str = Field(default='', max_length=200)
    account: str = Field(default='', max_length=100)
    debit: str | None = None
    credit: str | None = None
    amount: str | None = None
    description: str = Field(default='', max_length=4000)
    direction: str = Field(default='', max_length=50)

    @field_validator('debit', 'credit', 'amount', mode='before')
    @classmethod
    def validate_amount(cls, value):
        return None if value is None or value == '' else money(value)

    @model_validator(mode='after')
    def validate_required(self):
        if self.kind == 'journal':
            if not self.voucher_id or not self.account or self.debit is None or self.credit is None:
                raise ValueError('分录必须提供凭证号、科目、借方金额及贷方金额（零也须明确填写）')
            if Decimal(self.debit) < 0 or Decimal(self.credit) < 0:
                raise ValueError('分录借贷金额不能为负；冲销使用反向分录')
            if Decimal(self.debit) and Decimal(self.credit):
                raise ValueError('同一分录不能同时有借方及贷方金额')
        else:
            if self.amount is None:
                raise ValueError('业务记录必须提供 amount，缺失值不能默认为零')
            if self.kind in ['opening', 'trial_balance'] and not self.account:
                raise ValueError('余额记录必须提供科目')
        return self


class SnapshotInput(StrictModel):
    book_id: str


class CheckInput(SnapshotInput):
    snapshot_id: str


class InvestigateInput(StrictModel):
    mode: Literal['rules', 'single', 'multi', 'active'] = 'rules'


class SimulationInput(StrictModel):
    case_id: str
    adjustments: list[dict] = Field(min_length=1, max_length=1000)


class ReviewInput(StrictModel):
    case_id: str
    expected_snapshot_id: str
    decision: Literal['accepted', 'returned', 'reserved']
    comment: str = Field(default='', max_length=4000)


class ApprovalInput(StrictModel):
    review_id: str
    expected_snapshot_id: str
    decision: Literal['approved', 'rejected']
    comment: str = Field(default='', max_length=4000)
