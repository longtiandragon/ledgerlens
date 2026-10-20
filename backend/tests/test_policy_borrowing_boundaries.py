"""Handwritten CAS 17 art. 11 scope cases; no runtime-generated expectations."""
import json

import pytest

from app.policy_consistency import evaluate_policy
from app.policy_contract import PolicyCheckContext, PolicyObservation


def borrowing(*, excluded='yes', uniform='yes', ready='no', confirmation='human_confirmed', omit=()):
    values = {'qualifying_asset_long_construction': 'yes', 'asset_expenditure_incurred': 'yes',
              'borrowing_cost_incurred': 'yes', 'manufacturer_production_started': 'yes',
              'asset_ready_for_intended_use': ready, 'capitalization_suspension_excluded': excluded,
              'uniform_capitalization_phase_for_observation': uniform,
              'specific_borrowing_interest_only': 'yes'}
    observation = {'observation_id': 'borrowing-review', 'book_id': 'review', 'snapshot_id': 'snapshot',
                   'topic': 'custom_equipment_borrowing_cost', 'conditions': {
                       name: {'value': value, 'confirmation': confirmation, 'evidence_ids': ['activity-log']}
                       for name, value in values.items() if name not in omit},
                   'reported_treatment': 'capitalize_borrowing_costs', 'treatment_evidence_ids': ['activity-log']}
    context = {'book_id': 'review', 'snapshot_id': 'snapshot', 'framework': 'CAS', 'period': '2026-10',
               'as_of': '2026-10-14T00:00:00+00:00', 'evidence': [{'evidence_id': 'activity-log',
               'book_id': 'review', 'snapshot_id': 'snapshot', 'known_at': '2026-10-01T00:00:00+00:00'}]}
    return evaluate_policy(PolicyObservation.model_validate_json(json.dumps(observation)),
                           PolicyCheckContext.model_validate_json(json.dumps(context)))


def test_abnormal_four_month_interruption_cannot_pass_as_continuous_capitalization():
    result = borrowing(excluded='no')
    assert result['status'] == 'unsupported' and result['expected_treatment'] is None


@pytest.mark.parametrize('scenario', ['necessary_curing_four_months', 'abnormal_two_months', 'separate_resumed_phase'])
def test_confirmed_no_suspension_single_phase_can_capitalize(scenario):
    # The scenario name explains independently reviewed fixture semantics; the
    # engine does not infer eligibility from text or calculate elapsed months.
    result = borrowing(excluded='yes', uniform='yes')
    assert (result['status'], result['expected_treatment']) == ('normal', 'capitalize_borrowing_costs')


@pytest.mark.parametrize('ready', ['yes', 'no'])
def test_period_spanning_pause_restart_or_completion_requires_segmentation(ready):
    result = borrowing(uniform='no', ready=ready)
    assert result['status'] == 'unsupported' and result['expected_treatment'] is None


@pytest.mark.parametrize('missing', ['capitalization_suspension_excluded', 'uniform_capitalization_phase_for_observation'])
@pytest.mark.parametrize('ready', ['yes', 'no'])
def test_old_observations_cannot_default_missing_boundaries(missing, ready):
    result = borrowing(omit=[missing], ready=ready)
    assert result['status'] == 'insufficient' and missing in result['missing_conditions']


@pytest.mark.parametrize('confirmation', ['model_suggested', 'source_reported', 'unconfirmed'])
def test_model_and_source_cannot_confirm_the_manual_scope(confirmation):
    result = borrowing(confirmation=confirmation)
    assert result['status'] == 'insufficient' and result['expected_treatment'] is None
