"""Independent MOF 3762833 timing / 3691861 mixed-payment boundary cases.

Inputs are explicit teaching assumptions; expected values are handwritten from
the two public answers, never produced by the production rule or benchmark.
"""
import json

import pytest

from app.policy_consistency import evaluate_policy
from app.policy_contract import PolicyCheckContext, PolicyObservation


def evaluate(topic, values, reported, confirmations=None):
    conditions = {
        name: {'value': value, 'confirmation': (confirmations or {}).get(name, 'human_confirmed'),
               'evidence_ids': ['teaching-source']}
        for name, value in values.items() if value is not None
    }
    observation = PolicyObservation.model_validate({
        'observation_id': 'boundary', 'book_id': 'book', 'snapshot_id': 'snapshot',
        'topic': topic, 'conditions': conditions, 'reported_treatment': reported,
        'treatment_evidence_ids': ['teaching-source']})
    context = PolicyCheckContext.model_validate_json(json.dumps({
        'book_id': 'book', 'snapshot_id': 'snapshot', 'framework': 'CAS', 'period': '2026-10',
        'as_of': '2026-10-20T00:00:00+00:00', 'evidence': [{
            'evidence_id': 'teaching-source', 'book_id': 'book', 'snapshot_id': 'snapshot',
            'known_at': '2026-10-01T00:00:00+00:00'}]}))
    return evaluate_policy(observation, context)


@pytest.mark.parametrize('reported', ['other', 'operating_cost'])
def test_transport_old_observation_without_revenue_timing_cannot_conclude(reported):
    # No confirmed current-period revenue/cost amortization. Neither keeping an
    # asset nor prematurely expensing it can be judged by this presentation rule.
    result = evaluate('transport_fulfillment_cost', {
        'transport_before_control_transfer': 'yes',
        'transport_not_separate_performance_obligation': 'yes'}, reported)
    assert result['status'] == 'insufficient'
    assert result['expected_treatment'] is None


@pytest.mark.parametrize('reported', ['operating_cash_outflow', 'financing_cash_outflow'])
def test_mixed_long_lease_principal_and_simplified_rent_requires_split(reported):
    # One 1100 payment contains 1000 long-lease principal and 100 short rent.
    # This classifier must not label the whole payment operating or financing.
    result = evaluate('lease_cash_flow', {
        'lease_liability_principal_or_interest': 'yes', 'prepaid_rent_or_lease_deposit': 'no',
        'simplified_short_term_or_low_value_lease': 'yes',
        'variable_payment_outside_lease_liability': 'no'}, reported)
    assert result['status'] == 'unsupported'
    assert result['expected_treatment'] is None


@pytest.mark.parametrize('scope,status', [('no', 'unsupported'), ('unknown', 'insufficient')])
@pytest.mark.parametrize('reported', ['other', 'operating_cost'])
def test_transport_before_revenue_or_unknown_timing_does_not_recommend_expensing(scope, status, reported):
    result = evaluate('transport_fulfillment_cost', {
        'transport_before_control_transfer': 'yes',
        'transport_not_separate_performance_obligation': 'yes',
        'transport_cost_amortization_in_current_revenue_period': scope}, reported)
    assert result['status'] == status
    assert result['expected_treatment'] is None


@pytest.mark.parametrize('confirmation', ['unconfirmed', 'source_reported', 'model_suggested'])
def test_transport_current_amortization_requires_human_confirmation(confirmation):
    field = 'transport_cost_amortization_in_current_revenue_period'
    result = evaluate('transport_fulfillment_cost', {
        'transport_before_control_transfer': 'yes',
        'transport_not_separate_performance_obligation': 'yes', field: 'yes'},
        'operating_cost', {field: confirmation})
    assert result['status'] == 'insufficient'
    assert result['expected_treatment'] is None
    assert field in result['missing_conditions']


@pytest.mark.parametrize('reported,status', [('operating_cost', 'normal'), ('selling_expense', 'supported')])
def test_confirmed_current_transport_amortization_uses_operating_cost(reported, status):
    result = evaluate('transport_fulfillment_cost', {
        'transport_before_control_transfer': 'yes',
        'transport_not_separate_performance_obligation': 'yes',
        'transport_cost_amortization_in_current_revenue_period': 'yes'}, reported)
    assert (result['status'], result['expected_treatment']) == (status, 'operating_cost')


@pytest.mark.parametrize('principal', [None, 'unknown', 'no'])
@pytest.mark.parametrize('confirmation', ['human_confirmed', 'model_suggested'])
def test_simplified_lease_cannot_hide_unconfirmed_liability_component(principal, confirmation):
    result = evaluate('lease_cash_flow', {
        'lease_liability_principal_or_interest': principal, 'prepaid_rent_or_lease_deposit': 'yes',
        'simplified_short_term_or_low_value_lease': 'yes',
        'variable_payment_outside_lease_liability': 'no'}, 'operating_cash_outflow',
        {'lease_liability_principal_or_interest': confirmation})
    if principal == 'no' and confirmation == 'human_confirmed':
        assert (result['status'], result['expected_treatment']) == ('normal', 'operating_cash_outflow')
    else:
        assert result['status'] == 'insufficient' and result['expected_treatment'] is None
        assert 'lease_liability_principal_or_interest' in result['missing_conditions']


@pytest.mark.parametrize('principal,prepaid,simplified,expected', [
    ('yes', 'no', 'no', 'financing_cash_outflow'),
    ('no', 'yes', 'no', 'financing_cash_outflow'),
    ('no', 'yes', 'yes', 'operating_cash_outflow'),
    ('no', 'no', 'yes', 'operating_cash_outflow'),
])
def test_separately_identified_lease_components_retain_official_classification(principal, prepaid, simplified, expected):
    result = evaluate('lease_cash_flow', {
        'lease_liability_principal_or_interest': principal, 'prepaid_rent_or_lease_deposit': prepaid,
        'simplified_short_term_or_low_value_lease': simplified,
        'variable_payment_outside_lease_liability': 'no'}, expected)
    assert (result['status'], result['expected_treatment']) == ('normal', expected)
