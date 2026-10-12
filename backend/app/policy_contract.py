"""Source-bound policy observations; model suggestions never confirm facts."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Topic = Literal['resale_inventory', 'routine_repair', 'previous_expensed_prototype',
                'vat_credit_refund', 'personal_tax_withholding_fee', 'short_term_lease', 'lease_cash_flow',
                'transport_fulfillment_cost', 'advance_receipt', 'contract_asset_impairment', 'lessee_improvement', 'foreign_currency_advance', 'debt_restructuring_inventory', 'cash_discount',
                'group_share_payment', 'debt_release_timing', 'initial_revenue_ecl', 'rent_subsidy', 'voluntary_share_cancellation']
Treatment = Literal['inventory', 'fixed_asset', 'period_expense', 'inventory_cost', 'management_expense',
                    'selling_expense', 'reverse_prior_expense', 'keep_prior_expense', 'tax_refunds_received',
                    'other_operating_cash_received', 'taxes_paid', 'other_income_and_operating_revenue',
                    'nonoperating_income', 'right_of_use_asset_and_lease_liability', 'short_term_lease_simplified',
                    'financing_cash_outflow', 'operating_cash_outflow',
                    'advance_receipts', 'asset_impairment_loss', 'contract_liability', 'credit_impairment_loss', 'deferred_income', 'exchange_difference_recognized', 'finance_expense', 'historical_rate_no_exchange_difference', 'long_term_prepaid_expense', 'operating_cost', 'other_income_not_revenue', 'revenue_and_cost_of_sales', 'right_of_use_asset_cost', 'variable_consideration',
                    'equity_settled_share_payment', 'cash_settled_share_payment', 'defer_debt_restructuring_gain',
                    'recognize_debt_restructuring_gain', 'opening_retained_earnings_no_comparative_restatement',
                    'restate_comparative_ecl', 'income_related_grant', 'asset_related_grant',
                    'accelerate_vesting_keep_prior_expense', 'reverse_share_payment_expense', 'unknown', 'other']

PREDICATES = {
    'resale_inventory': {'ordinary_course', 'held_for_sale', 'economic_benefits_probable', 'cost_reliably_measurable'},
    'routine_repair': {'routine_repair', 'capitalization_criteria_met'},
    'previous_expensed_prototype': {'previous_expensing_was_correct', 'later_sale_contract_or_sale'},
    'vat_credit_refund': {'vat_credit_refund_under_2022_policy', 'received', 'repaid_and_credit_continues'},
    'personal_tax_withholding_fee': {'enterprise_is_withholding_agent', 'personal_tax_fee_received'},
    'short_term_lease': {'lease_term_not_over_12_months', 'contains_purchase_option'},
    'lease_cash_flow': {'lease_liability_principal_or_interest', 'prepaid_rent_or_lease_deposit',
                        'simplified_short_term_or_low_value_lease', 'variable_payment_outside_lease_liability'},
    'transport_fulfillment_cost': {'transport_before_control_transfer', 'transport_not_separate_performance_obligation'},
    'advance_receipt': {'new_revenue_standard_applied', 'received_before_transfer', 'vat_portion_excluded'},
    'contract_asset_impairment': {'contract_asset_impairment'},
    'lessee_improvement': {'lessee_improvement_expenditure'},
    'foreign_currency_advance': {'foreign_currency_advance', 'measured_at_historical_cost'},
    'debt_restructuring_inventory': {'debtor_settles_debt_with_inventory', 'restructuring_outside_ordinary_activities'},
    'cash_discount': {'cash_discount_granted_on_sale', 'new_revenue_standard_applied'},
    'group_share_payment': {'intragroup_share_payment', 'entity_receives_employee_services', 'no_settlement_obligation'},
    'debt_release_timing': {'debt_restructuring_agreement_signed', 'present_obligation_not_released'},
    'initial_revenue_ecl': {'financial_instruments_2017_applied', 'first_application_new_revenue_standard',
                            'new_receivable_or_contract_asset_from_transition'},
    'rent_subsidy': {'government_grant', 'compensates_property_rental_costs', 'not_for_acquiring_long_term_assets'},
    'voluntary_share_cancellation': {'equity_award_cancelled_during_vesting', 'employee_voluntary_exit',
                                     'not_cancelled_for_failure_of_vesting_conditions'},
}


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class ConditionEvidence(PolicyModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    book_id: str = Field(min_length=1, max_length=200)
    snapshot_id: str = Field(min_length=1, max_length=200)
    known_at: datetime

    @field_validator('known_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('Knowledge time must include timezone')
        return value


class ConfirmedCondition(PolicyModel):
    value: Literal['yes', 'no', 'unknown'] = 'unknown'
    confirmation: Literal['human_confirmed', 'source_reported', 'model_suggested', 'unconfirmed'] = 'unconfirmed'
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError('Duplicate condition evidence')
        if self.confirmation == 'human_confirmed' and self.value != 'unknown' and not self.evidence_ids:
            raise ValueError('A confirmed condition requires original evidence')
        return self


class PolicyObservation(PolicyModel):
    observation_id: str = Field(min_length=1, max_length=200)
    book_id: str = Field(min_length=1, max_length=200)
    snapshot_id: str = Field(min_length=1, max_length=200)
    topic: Topic
    conditions: dict[str, ConfirmedCondition]
    beneficiary: Literal['inventory_production', 'administration', 'dedicated_sales', 'other', 'unknown'] = 'unknown'
    beneficiary_confirmation: Literal['human_confirmed', 'source_reported', 'model_suggested', 'unconfirmed'] = 'unconfirmed'
    beneficiary_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    reported_treatment: Treatment = 'unknown'
    treatment_evidence_ids: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def scope(self):
        if set(self.conditions) - PREDICATES[self.topic]:
            raise ValueError('Unsupported condition predicate for this policy topic')
        for references in (self.beneficiary_evidence_ids, self.treatment_evidence_ids):
            if len(set(references)) != len(references):
                raise ValueError('Duplicate observation evidence')
        if self.reported_treatment != 'unknown' and not self.treatment_evidence_ids:
            raise ValueError('Reported treatment requires original evidence')
        if self.beneficiary_confirmation == 'human_confirmed' and self.beneficiary != 'unknown' and not self.beneficiary_evidence_ids:
            raise ValueError('Confirmed beneficiary requires original evidence')
        return self


class PolicyCheckContext(PolicyModel):
    book_id: str
    snapshot_id: str
    framework: Literal['CAS', 'SCAS']
    period: str = Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    as_of: datetime
    evidence: list[ConditionEvidence] = Field(max_length=50000)

    @field_validator('as_of')
    @classmethod
    def aware(cls, value):
        return ConditionEvidence.aware(value)

    @model_validator(mode='after')
    def valid_membership(self):
        if len({e.evidence_id for e in self.evidence}) != len(self.evidence):
            raise ValueError('Duplicate context evidence identity')
        if any(e.book_id != self.book_id or e.snapshot_id != self.snapshot_id or e.known_at > self.as_of for e in self.evidence):
            raise ValueError('Evidence is outside the book, snapshot or knowledge time')
        date.fromisoformat(self.period + '-01')
        return self
