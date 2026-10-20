"""Independent lease boundary cases from CAS 21 arts. 14 and 30-33.

Written during AI review, not an independent accountant's sign-off. These tests
do not import benchmark answers or generate expected outcomes from the engine.
"""
import json

import pytest

from app.policy_consistency import evaluate_policy
from app.policy_contract import PolicyCheckContext, PolicyObservation


def lease(topic='one_year_lease_term', *, purchase='no', low_value='no', renewal='no',
          total_not_over_12='yes', treatment='short_term_lease_simplified', omit=(), confirmation='human_confirmed'):
    values = {'contains_purchase_option': purchase, 'low_value_asset_lease_eligible': low_value}
    if topic == 'one_year_lease_term':
        values.update(contract_term_not_over_12_months='yes',
                      lease_term_not_over_12_months=total_not_over_12,
                      renewal_or_non_termination_reasonably_certain=renewal,
                      improvements_amortized_beyond_lease_term='no')
    else:
        values['lease_term_not_over_12_months'] = 'yes'
    observation = {'observation_id': 'ai-review-lease', 'book_id': 'review-book', 'snapshot_id': 'review-snapshot',
                   'topic': topic, 'conditions': {
                       name: {'value': value, 'confirmation': confirmation, 'evidence_ids': ['contract-1']}
                       for name, value in values.items() if name not in omit},
                   'reported_treatment': treatment, 'treatment_evidence_ids': ['contract-1']}
    context = {'book_id': 'review-book', 'snapshot_id': 'review-snapshot', 'framework': 'CAS', 'period': '2026-10',
               'as_of': '2026-10-14T00:00:00+00:00', 'evidence': [
                   {'evidence_id': 'contract-1', 'book_id': 'review-book', 'snapshot_id': 'review-snapshot',
                    'known_at': '2026-10-01T00:00:00+00:00'}]}
    return (PolicyObservation.model_validate_json(json.dumps(observation)),
            PolicyCheckContext.model_validate_json(json.dumps(context)))


def test_purchase_option_disallows_short_term_even_without_likely_renewal():
    result = evaluate_policy(*lease(purchase='yes'))
    assert (result['status'], result['expected_treatment']) == (
        'supported', 'right_of_use_asset_and_lease_liability')


@pytest.mark.parametrize('field', ['contains_purchase_option', 'low_value_asset_lease_eligible',
                                  'lease_term_not_over_12_months'])
def test_old_one_year_observation_without_eligibility_cannot_be_normal(field):
    result = evaluate_policy(*lease(omit=[field]))
    assert result['status'] == 'insufficient'
    assert field in result['missing_conditions']
    assert result['expected_treatment'] is None


@pytest.mark.parametrize('topic', ['one_year_lease_term', 'short_term_lease'])
@pytest.mark.parametrize('renewal', ['yes', 'no'])
def test_low_value_election_is_a_separate_path_not_forced_recognition(topic, renewal):
    # Eligibility does not mean election; narrow archived-Q&A rules must abstain.
    result = evaluate_policy(*lease(topic, purchase='yes', low_value='yes', renewal=renewal))
    assert result['status'] == 'unsupported'
    assert result['expected_treatment'] is None


def test_old_purchase_option_observation_needs_low_value_exclusion():
    result = evaluate_policy(*lease('short_term_lease', purchase='yes', omit=['low_value_asset_lease_eligible']))
    assert result['status'] == 'insufficient'
    assert result['missing_conditions'] == ['low_value_asset_lease_eligible']


@pytest.mark.parametrize('confirmation', ['model_suggested', 'source_reported', 'unconfirmed'])
def test_model_or_source_statement_does_not_confirm_lease_eligibility(confirmation):
    result = evaluate_policy(*lease(confirmation=confirmation))
    assert result['status'] == 'insufficient'


@pytest.mark.parametrize('treatment', ['short_term_lease_simplified', 'right_of_use_asset_and_lease_liability'])
def test_no_purchase_no_renewal_non_low_value_can_elect_short_term_or_recognize(treatment):
    result = evaluate_policy(*lease(treatment=treatment))
    assert (result['status'], result['expected_treatment']) == ('normal', treatment)


def test_non_low_value_purchase_option_can_be_recognized():
    result = evaluate_policy(*lease('short_term_lease', purchase='yes',
                                    treatment='right_of_use_asset_and_lease_liability'))
    assert (result['status'], result['expected_treatment']) == (
        'normal', 'right_of_use_asset_and_lease_liability')


def test_six_month_contract_plus_six_month_renewal_can_still_be_short_term():
    result = evaluate_policy(*lease(renewal='yes', total_not_over_12='yes'))
    assert (result['status'], result['expected_treatment']) == ('normal', 'short_term_lease_simplified')


def test_six_month_contract_plus_twelve_month_renewal_cannot_be_short_term():
    result = evaluate_policy(*lease(renewal='yes', total_not_over_12='no'))
    assert (result['status'], result['expected_treatment']) == (
        'supported', 'right_of_use_asset_and_lease_liability')
