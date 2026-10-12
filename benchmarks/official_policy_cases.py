"""Hand-authored developer projections of fifteen archived MOF Q&A.

Expected results are literal labels reviewed against the original public answers.
These are not sealed tasks, real ledgers, or 30 independently sourced cases. No
production policy function is called while constructing inputs or expectations.
"""
from copy import deepcopy


VERSION = 'mof-policy-projections-v4'
# These inputs explicitly assume that a reviewer has confirmed the conditions.
# The observations use labelled synthetic evidence, never an actual enterprise.
BASE_CONDITIONS = {
    'resale_inventory': {'ordinary_course': 'yes', 'held_for_sale': 'yes',
                         'economic_benefits_probable': 'yes', 'cost_reliably_measurable': 'yes'},
    'routine_repair': {'routine_repair': 'yes', 'capitalization_criteria_met': 'no'},
    'previous_expensed_prototype': {'previous_expensing_was_correct': 'yes',
                                  'later_sale_contract_or_sale': 'yes'},
    'vat_credit_refund': {'vat_credit_refund_under_2022_policy': 'yes', 'received': 'yes',
                         'repaid_and_credit_continues': 'no'},
    'personal_tax_withholding_fee': {'enterprise_is_withholding_agent': 'yes',
                                    'personal_tax_fee_received': 'yes'},
    'short_term_lease': {'lease_term_not_over_12_months': 'yes', 'contains_purchase_option': 'yes'},
    'lease_cash_flow': {'lease_liability_principal_or_interest': 'yes', 'prepaid_rent_or_lease_deposit': 'no',
                        'simplified_short_term_or_low_value_lease': 'no',
                        'variable_payment_outside_lease_liability': 'no'},
    'transport_fulfillment_cost': {'transport_before_control_transfer': 'yes',
                                   'transport_not_separate_performance_obligation': 'yes'},
    'advance_receipt': {'new_revenue_standard_applied': 'yes', 'received_before_transfer': 'yes',
                        'vat_portion_excluded': 'yes'},
    'contract_asset_impairment': {'contract_asset_impairment': 'yes'},
    'lessee_improvement': {'lessee_improvement_expenditure': 'yes'},
    'foreign_currency_advance': {'foreign_currency_advance': 'yes', 'measured_at_historical_cost': 'yes'},
    'debt_restructuring_inventory': {'debtor_settles_debt_with_inventory': 'yes',
                                     'restructuring_outside_ordinary_activities': 'yes'},
    'cash_discount': {'new_revenue_standard_applied': 'yes', 'cash_discount_granted_on_sale': 'yes'},
    'group_share_payment': {'intragroup_share_payment': 'yes', 'entity_receives_employee_services': 'yes',
                            'no_settlement_obligation': 'yes'},
    'debt_release_timing': {'debt_restructuring_agreement_signed': 'yes', 'present_obligation_not_released': 'yes'},
    'initial_revenue_ecl': {'financial_instruments_2017_applied': 'yes', 'first_application_new_revenue_standard': 'yes',
                            'new_receivable_or_contract_asset_from_transition': 'yes'},
    'rent_subsidy': {'government_grant': 'yes', 'compensates_property_rental_costs': 'yes',
                     'not_for_acquiring_long_term_assets': 'yes'},
    'voluntary_share_cancellation': {'equity_award_cancelled_during_vesting': 'yes', 'employee_voluntary_exit': 'yes',
                                     'not_cancelled_for_failure_of_vesting_conditions': 'yes'},
}

# id, source, framework, topic, reported treatment, expected status, expected
# treatment, group, input changes. "anchor" is a structured paraphrase, not an
# end-to-end reading of the question by a model.
SPECS = [
    ('INV-01', '3861813', 'CAS', 'resale_inventory', 'inventory', 'normal', 'inventory', 'anchor', {}),
    ('INV-02', '3861813', 'CAS', 'resale_inventory', 'fixed_asset', 'supported', 'inventory', 'contrast', {}),
    ('INV-03', '3861813', 'CAS', 'resale_inventory', 'inventory', 'insufficient', None, 'contrast',
     {'conditions': {'cost_reliably_measurable': 'unknown'}}),
    ('INV-04', '3861813', 'CAS', 'resale_inventory', 'inventory', 'unsupported', None, 'contrast',
     {'conditions': {'held_for_sale': 'no'}}),
    ('INV-05', '3861813', 'CAS', 'resale_inventory', 'inventory', 'insufficient', None, 'contrast',
     {'confirmations': {'ordinary_course': 'model_suggested'}}),
    ('REP-01', '3762834', 'CAS', 'routine_repair', 'inventory_cost', 'normal', 'inventory_cost', 'anchor',
     {'beneficiary': 'inventory_production'}),
    ('REP-02', '3762834', 'CAS', 'routine_repair', 'management_expense', 'normal', 'management_expense', 'anchor',
     {'beneficiary': 'administration'}),
    ('REP-03', '3762834', 'CAS', 'routine_repair', 'selling_expense', 'normal', 'selling_expense', 'anchor',
     {'beneficiary': 'dedicated_sales'}),
    ('REP-04', '3762834', 'CAS', 'routine_repair', 'management_expense', 'supported', 'inventory_cost', 'contrast',
     {'beneficiary': 'inventory_production'}),
    ('REP-05', '3762834', 'CAS', 'routine_repair', 'management_expense', 'insufficient', None, 'contrast', {}),
    ('REP-06', '3762834', 'CAS', 'routine_repair', 'fixed_asset', 'unsupported', None, 'contrast',
     {'conditions': {'capitalization_criteria_met': 'yes'}, 'beneficiary': 'administration'}),
    ('PRO-01', '3962191', 'CAS', 'previous_expensed_prototype', 'keep_prior_expense', 'normal',
     'keep_prior_expense', 'anchor', {}),
    ('PRO-02', '3962191', 'CAS', 'previous_expensed_prototype', 'reverse_prior_expense', 'supported',
     'keep_prior_expense', 'contrast', {}),
    ('PRO-03', '3962191', 'CAS', 'previous_expensed_prototype', 'keep_prior_expense', 'unsupported', None,
     'contrast', {'conditions': {'previous_expensing_was_correct': 'no'}}),
    ('PRO-04', '3962191', 'CAS', 'previous_expensed_prototype', 'keep_prior_expense', 'insufficient', None,
     'contrast', {'conditions': {'later_sale_contract_or_sale': 'unknown'}}),
    ('VAT-C-01', '3819699', 'CAS', 'vat_credit_refund', 'tax_refunds_received', 'normal',
     'tax_refunds_received', 'anchor', {}),
    ('VAT-C-02', '3819699', 'CAS', 'vat_credit_refund', 'taxes_paid', 'normal', 'taxes_paid', 'anchor',
     {'conditions': {'received': 'no', 'repaid_and_credit_continues': 'yes'}}),
    ('VAT-S-01', '3819702', 'SCAS', 'vat_credit_refund', 'other_operating_cash_received', 'normal',
     'other_operating_cash_received', 'anchor', {}),
    ('VAT-S-02', '3819702', 'SCAS', 'vat_credit_refund', 'taxes_paid', 'normal', 'taxes_paid', 'anchor',
     {'conditions': {'received': 'no', 'repaid_and_credit_continues': 'yes'}}),
    ('VAT-C-03', '3819699', 'CAS', 'vat_credit_refund', 'other_operating_cash_received', 'supported',
     'tax_refunds_received', 'contrast', {}),
    ('VAT-S-03', '3819702', 'SCAS', 'vat_credit_refund', 'tax_refunds_received', 'supported',
     'other_operating_cash_received', 'contrast', {}),
    ('VAT-C-04', '3819699', 'CAS', 'vat_credit_refund', 'tax_refunds_received', 'unsupported', None,
     'contrast', {'conditions': {'repaid_and_credit_continues': 'yes'}}),
    ('VAT-S-04', '3819702', 'SCAS', 'vat_credit_refund', 'other_operating_cash_received', 'insufficient', None,
     'contrast', {'conditions': {'received': 'no'}}),
    ('VAT-C-05', '3819699', 'CAS', 'vat_credit_refund', 'tax_refunds_received', 'insufficient', None,
     'contrast', {'confirmations': {'vat_credit_refund_under_2022_policy': 'source_reported'}}),
    ('FEE-01', '3762844', 'SCAS', 'personal_tax_withholding_fee', 'other_income_and_operating_revenue',
     'normal', 'other_income_and_operating_revenue', 'anchor', {}),
    ('FEE-02', '3762844', 'SCAS', 'personal_tax_withholding_fee', 'nonoperating_income', 'supported',
     'other_income_and_operating_revenue', 'contrast', {}),
    ('FEE-03', '3762844', 'SCAS', 'personal_tax_withholding_fee', 'other_income_and_operating_revenue',
     'insufficient', None, 'contrast', {'conditions': {'enterprise_is_withholding_agent': 'unknown'}}),
    ('FEE-04', '3762844', 'SCAS', 'personal_tax_withholding_fee', 'other_income_and_operating_revenue',
     'unsupported', None, 'contrast', {'conditions': {'personal_tax_fee_received': 'no'}}),
    ('INV-06', '3861813', 'CAS', 'resale_inventory', 'unknown', 'insufficient', 'inventory', 'contrast', {}),
    ('REP-07', '3762834', 'CAS', 'routine_repair', 'management_expense', 'insufficient', None, 'contrast',
     {'beneficiary': 'administration', 'beneficiary_confirmation': 'model_suggested'}),
    # 3691864: a lease with a purchase option is not a short-term lease, even if <= 12 months.
    ('LSE-01', '3691864', 'CAS', 'short_term_lease', 'right_of_use_asset_and_lease_liability', 'normal',
     'right_of_use_asset_and_lease_liability', 'anchor', {}),
    ('LSE-02', '3691864', 'CAS', 'short_term_lease', 'short_term_lease_simplified', 'supported',
     'right_of_use_asset_and_lease_liability', 'contrast', {}),
    ('LSE-03', '3691864', 'CAS', 'short_term_lease', 'short_term_lease_simplified', 'unsupported', None,
     'contrast', {'conditions': {'contains_purchase_option': 'no'}}),
    ('LSE-04', '3691864', 'CAS', 'short_term_lease', 'right_of_use_asset_and_lease_liability', 'unsupported', None,
     'contrast', {'conditions': {'lease_term_not_over_12_months': 'no'}}),
    ('LSE-05', '3691864', 'CAS', 'short_term_lease', 'short_term_lease_simplified', 'insufficient', None,
     'contrast', {'conditions': {'contains_purchase_option': 'unknown'}}),
    ('LSE-06', '3691864', 'SCAS', 'short_term_lease', 'short_term_lease_simplified', 'unsupported', None,
     'contrast', {}),
    # 3691861: principal/interest, prepaid rent and deposits -> financing; simplified short-term or
    # low-value leases (incl. their prepaid rent/deposits) and variable payments outside the
    # lease liability -> operating.
    ('LCF-01', '3691861', 'CAS', 'lease_cash_flow', 'financing_cash_outflow', 'normal',
     'financing_cash_outflow', 'anchor', {}),
    ('LCF-02', '3691861', 'CAS', 'lease_cash_flow', 'operating_cash_outflow', 'supported',
     'financing_cash_outflow', 'contrast', {}),
    ('LCF-03', '3691861', 'CAS', 'lease_cash_flow', 'financing_cash_outflow', 'normal', 'financing_cash_outflow',
     'anchor', {'conditions': {'lease_liability_principal_or_interest': 'no', 'prepaid_rent_or_lease_deposit': 'yes'}}),
    ('LCF-04', '3691861', 'CAS', 'lease_cash_flow', 'operating_cash_outflow', 'normal', 'operating_cash_outflow',
     'anchor', {'conditions': {'lease_liability_principal_or_interest': 'no', 'prepaid_rent_or_lease_deposit': 'yes',
                               'simplified_short_term_or_low_value_lease': 'yes'}}),
    ('LCF-05', '3691861', 'CAS', 'lease_cash_flow', 'financing_cash_outflow', 'supported', 'operating_cash_outflow',
     'contrast', {'conditions': {'lease_liability_principal_or_interest': 'no',
                                 'simplified_short_term_or_low_value_lease': 'yes'}}),
    ('LCF-06', '3691861', 'CAS', 'lease_cash_flow', 'operating_cash_outflow', 'normal', 'operating_cash_outflow',
     'anchor', {'conditions': {'lease_liability_principal_or_interest': 'no',
                               'variable_payment_outside_lease_liability': 'yes'}}),
    ('LCF-07', '3691861', 'CAS', 'lease_cash_flow', 'operating_cash_outflow', 'unsupported', None, 'contrast',
     {'conditions': {'variable_payment_outside_lease_liability': 'yes'}}),
    ('LCF-08', '3691861', 'CAS', 'lease_cash_flow', 'financing_cash_outflow', 'insufficient', None, 'contrast',
     {'conditions': {'simplified_short_term_or_low_value_lease': 'unknown'}}),
    ('LCF-09', '3691861', 'CAS', 'lease_cash_flow', 'operating_cash_outflow', 'unsupported', None, 'contrast',
     {'conditions': {'lease_liability_principal_or_interest': 'no'}}),
    # 3762833: transport before control transfer, not a separate obligation -> operating cost.
    ('TRN-01', '3762833', 'CAS', 'transport_fulfillment_cost', 'operating_cost', 'normal', 'operating_cost', 'anchor', {}),
    ('TRN-02', '3762833', 'CAS', 'transport_fulfillment_cost', 'selling_expense', 'supported', 'operating_cost',
     'contrast', {}),
    ('TRN-03', '3762833', 'CAS', 'transport_fulfillment_cost', 'selling_expense', 'unsupported', None, 'contrast',
     {'conditions': {'transport_not_separate_performance_obligation': 'no'}}),
    ('TRN-04', '3762833', 'CAS', 'transport_fulfillment_cost', 'operating_cost', 'insufficient', None, 'contrast',
     {'confirmations': {'transport_before_control_transfer': 'model_suggested'}}),
    # 3634031: advance receipt before transfer under the new standard -> contract liability.
    ('ADV-01', '3634031', 'CAS', 'advance_receipt', 'contract_liability', 'normal', 'contract_liability', 'anchor', {}),
    ('ADV-02', '3634031', 'CAS', 'advance_receipt', 'advance_receipts', 'supported', 'contract_liability',
     'contrast', {}),
    ('ADV-03', '3634031', 'CAS', 'advance_receipt', 'deferred_income', 'supported', 'contract_liability',
     'contrast', {}),
    ('ADV-04', '3634031', 'CAS', 'advance_receipt', 'contract_liability', 'unsupported', None, 'contrast',
     {'conditions': {'vat_portion_excluded': 'no'}}),
    ('ADV-05', '3634031', 'CAS', 'advance_receipt', 'contract_liability', 'insufficient', None, 'contrast',
     {'conditions': {'received_before_transfer': 'unknown'}}),
    # 3634035: contract asset impairment -> asset impairment loss.
    ('CAI-01', '3634035', 'CAS', 'contract_asset_impairment', 'asset_impairment_loss', 'normal',
     'asset_impairment_loss', 'anchor', {}),
    ('CAI-02', '3634035', 'CAS', 'contract_asset_impairment', 'credit_impairment_loss', 'supported',
     'asset_impairment_loss', 'contrast', {}),
    ('CAI-03', '3634035', 'CAS', 'contract_asset_impairment', 'credit_impairment_loss', 'unsupported', None,
     'contrast', {'conditions': {'contract_asset_impairment': 'no'}}),
    # 3691863: lessee leasehold improvement -> long-term prepaid expense, not right-of-use asset.
    ('LIM-01', '3691863', 'CAS', 'lessee_improvement', 'long_term_prepaid_expense', 'normal',
     'long_term_prepaid_expense', 'anchor', {}),
    ('LIM-02', '3691863', 'CAS', 'lessee_improvement', 'right_of_use_asset_cost', 'supported',
     'long_term_prepaid_expense', 'contrast', {}),
    ('LIM-03', '3691863', 'CAS', 'lessee_improvement', 'long_term_prepaid_expense', 'insufficient', None,
     'contrast', {'confirmations': {'lessee_improvement_expenditure': 'source_reported'}}),
    # 3664191: foreign-currency advances at historical cost -> historical rate, no exchange difference.
    ('FXA-01', '3664191', 'CAS', 'foreign_currency_advance', 'historical_rate_no_exchange_difference', 'normal',
     'historical_rate_no_exchange_difference', 'anchor', {}),
    ('FXA-02', '3664191', 'CAS', 'foreign_currency_advance', 'exchange_difference_recognized', 'supported',
     'historical_rate_no_exchange_difference', 'contrast', {}),
    ('FXA-03', '3664191', 'CAS', 'foreign_currency_advance', 'exchange_difference_recognized', 'unsupported', None,
     'contrast', {'conditions': {'measured_at_historical_cost': 'no'}}),
    # 3664186: debtor settles debt with inventory -> not a sale; difference to other income.
    ('DRI-01', '3664186', 'CAS', 'debt_restructuring_inventory', 'other_income_not_revenue', 'normal',
     'other_income_not_revenue', 'anchor', {}),
    ('DRI-02', '3664186', 'CAS', 'debt_restructuring_inventory', 'revenue_and_cost_of_sales', 'supported',
     'other_income_not_revenue', 'contrast', {}),
    ('DRI-03', '3664186', 'CAS', 'debt_restructuring_inventory', 'revenue_and_cost_of_sales', 'unsupported', None,
     'contrast', {'conditions': {'restructuring_outside_ordinary_activities': 'no'}}),
    ('DRI-04', '3664186', 'CAS', 'debt_restructuring_inventory', 'unknown', 'insufficient',
     'other_income_not_revenue', 'contrast', {}),
    # 3634029: cash discount under the new revenue standard -> variable consideration.
    ('CSD-01', '3634029', 'CAS', 'cash_discount', 'variable_consideration', 'normal', 'variable_consideration',
     'anchor', {}),
    ('CSD-02', '3634029', 'CAS', 'cash_discount', 'finance_expense', 'supported', 'variable_consideration',
     'contrast', {}),
    ('CSD-03', '3634029', 'CAS', 'cash_discount', 'finance_expense', 'unsupported', None, 'contrast',
     {'conditions': {'new_revenue_standard_applied': 'no'}}),
    ('CSD-04', '3634029', 'SCAS', 'cash_discount', 'finance_expense', 'unsupported', None, 'contrast', {}),
]

# Five new normative anchors transcribed from the archived official answers.
# Each has six independently specified expected paths; no production catalogue
# or evaluator is consulted to construct these labels.
V4_ANCHORS = [
    ('GSP', '3664183', 'group_share_payment', 'equity_settled_share_payment',
     'cash_settled_share_payment', 'no_settlement_obligation'),
    ('DRT', '3664190', 'debt_release_timing', 'defer_debt_restructuring_gain',
     'recognize_debt_restructuring_gain', 'present_obligation_not_released'),
    ('IRE', '3634036', 'initial_revenue_ecl', 'opening_retained_earnings_no_comparative_restatement',
     'restate_comparative_ecl', 'first_application_new_revenue_standard'),
    ('RSG', '3819697', 'rent_subsidy', 'income_related_grant',
     'asset_related_grant', 'not_for_acquiring_long_term_assets'),
    ('VSC', '3896761', 'voluntary_share_cancellation', 'accelerate_vesting_keep_prior_expense',
     'reverse_share_payment_expense', 'not_cancelled_for_failure_of_vesting_conditions'),
]
for prefix, source, topic, expected_treatment, wrong_treatment, predicate in V4_ANCHORS:
    SPECS.extend([
        (prefix + '-01', source, 'CAS', topic, expected_treatment, 'normal', expected_treatment, 'anchor', {}),
        (prefix + '-02', source, 'CAS', topic, wrong_treatment, 'supported', expected_treatment, 'contrast', {}),
        (prefix + '-03', source, 'CAS', topic, wrong_treatment, 'insufficient', None, 'contrast',
         {'conditions': {predicate: 'unknown'}}),
        (prefix + '-04', source, 'CAS', topic, wrong_treatment, 'unsupported', None, 'contrast',
         {'conditions': {predicate: 'no'}}),
        (prefix + '-05', source, 'CAS', topic, expected_treatment, 'insufficient', None, 'contrast',
         {'confirmations': {predicate: 'model_suggested'}}),
        (prefix + '-06', source, 'SCAS', topic, wrong_treatment, 'unsupported', None, 'contrast', {}),
    ])


def cases() -> list[dict]:
    result = []
    for identity, source, framework, topic, reported, status, expected, group, changes in SPECS:
        evidence_id = 'synthetic-policy-evidence-' + identity
        conditions = deepcopy(BASE_CONDITIONS[topic])
        conditions.update(changes.get('conditions', {}))
        observation = {
            'observation_id': identity, 'book_id': 'synthetic-policy-book-' + framework,
            'snapshot_id': 'synthetic-policy-snapshot-' + identity, 'topic': topic,
            'conditions': {name: {'value': value, 'confirmation': changes.get('confirmations', {}).get(
                name, 'human_confirmed'), 'evidence_ids': [evidence_id]} for name, value in conditions.items()},
            'reported_treatment': reported,
            'treatment_evidence_ids': [] if reported == 'unknown' else [evidence_id],
        }
        if 'beneficiary' in changes:
            observation.update(beneficiary=changes['beneficiary'], beneficiary_confirmation=changes.get(
                'beneficiary_confirmation', 'human_confirmed'), beneficiary_evidence_ids=[evidence_id])
        context = {
            'book_id': observation['book_id'], 'snapshot_id': observation['snapshot_id'], 'framework': framework,
            'period': '2026-09', 'as_of': '2026-09-30T16:00:00+00:00',
            'evidence': [{'evidence_id': evidence_id, 'book_id': observation['book_id'],
                          'snapshot_id': observation['snapshot_id'], 'known_at': '2026-09-01T00:00:00+00:00'}],
        }
        result.append({'id': identity, 'source_id': 'mof-qa-' + source, 'group': group,
                       'observation': observation, 'context': context,
                       'expected': {'status': status, 'expected_treatment': expected},
                       'synthetic': True, 'answer_basis': 'hand_authored_archived_QA_projection'})
    return result
