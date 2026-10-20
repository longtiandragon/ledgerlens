"""Independent scope cases: CAS 17 auxiliary fees must not enter the interest rule."""
import json

import pytest

from app.policy_consistency import evaluate_policy
from app.policy_contract import PolicyCheckContext, PolicyObservation


def evaluate(*, scope='yes', confirmation='human_confirmed', ready='no', started='no', overrides=None):
    values = {'qualifying_asset_long_construction': 'yes', 'asset_expenditure_incurred': 'yes',
              'borrowing_cost_incurred': 'yes', 'manufacturer_production_started': started,
              'asset_ready_for_intended_use': ready, 'capitalization_suspension_excluded': 'yes',
              'uniform_capitalization_phase_for_observation': 'yes'}
    conditions = {key: {'value': value, 'confirmation': 'human_confirmed', 'evidence_ids': ['source']}
                  for key, value in values.items()}
    if scope is not None:
        conditions['specific_borrowing_interest_only'] = {
            'value': scope, 'confirmation': confirmation, 'evidence_ids': ['source']}
    for key, value in (overrides or {}).items():
        conditions[key].update(value)
    observation = PolicyObservation.model_validate({
        'observation_id': 'scope-case', 'book_id': 'book', 'snapshot_id': 'snapshot',
        'topic': 'custom_equipment_borrowing_cost', 'conditions': conditions,
        'reported_treatment': 'capitalize_borrowing_costs', 'treatment_evidence_ids': ['source']})
    context = PolicyCheckContext.model_validate_json(json.dumps({
        'book_id': 'book', 'snapshot_id': 'snapshot', 'framework': 'CAS', 'period': '2026-10',
        'as_of': '2026-10-15T00:00:00+00:00', 'evidence': [{
            'evidence_id': 'source', 'book_id': 'book', 'snapshot_id': 'snapshot',
            'known_at': '2026-10-01T00:00:00+00:00'}]}))
    return evaluate_policy(observation, context)


@pytest.mark.parametrize('ready', ['yes', 'no'])
def test_auxiliary_fees_are_outside_interest_rule_even_before_production(ready):
    # CAS 17 art. 10 governs auxiliary fees separately; this narrow rule must abstain.
    result = evaluate(scope='no', ready=ready, started='no')
    assert result['status'] == 'unsupported'
    assert result['expected_treatment'] is None


@pytest.mark.parametrize('scope', [None, 'unknown'])
@pytest.mark.parametrize('ready', ['yes', 'no'])
def test_missing_interest_scope_never_defaults_to_interest(scope, ready):
    result = evaluate(scope=scope, ready=ready)
    assert result['status'] == 'insufficient'
    assert result['expected_treatment'] is None
    assert 'specific_borrowing_interest_only' in result['missing_conditions']


@pytest.mark.parametrize('confirmation', ['model_suggested', 'source_reported', 'unconfirmed'])
def test_only_scope_predicate_unconfirmed_blocks_otherwise_human_confirmed_path(confirmation):
    result = evaluate(confirmation=confirmation)
    assert result['status'] == 'insufficient' and result['expected_treatment'] is None


@pytest.mark.parametrize('ready,started,expected,status', [
    ('no', 'yes', 'capitalize_borrowing_costs', 'normal'),
    ('no', 'no', 'expense_borrowing_costs', 'supported'),
    ('yes', 'yes', 'expense_borrowing_costs', 'supported'),
])
def test_explicit_specific_interest_retains_limited_timing_rule(ready, started, expected, status):
    result = evaluate(ready=ready, started=started)
    assert (result['status'], result['expected_treatment']) == (status, expected)


@pytest.mark.parametrize('boundary', ['capitalization_suspension_excluded',
                                     'uniform_capitalization_phase_for_observation'])
@pytest.mark.parametrize('value,confirmation,status', [
    ('unknown', 'human_confirmed', 'insufficient'),
    ('yes', 'model_suggested', 'insufficient'),
    ('no', 'human_confirmed', 'unsupported'),
])
def test_ready_shortcut_still_requires_each_manual_boundary(boundary, value, confirmation, status):
    result = evaluate(ready='yes', overrides={boundary: {'value': value, 'confirmation': confirmation}})
    assert result['status'] == status and result['expected_treatment'] is None


@pytest.mark.parametrize('field', ['qualifying_asset_long_construction',
                                  'asset_ready_for_intended_use', 'manufacturer_production_started'])
def test_unknown_asset_or_timing_does_not_justify_capitalization(field):
    result = evaluate(started='yes', overrides={field: {'value': 'unknown'}})
    assert result['status'] == 'insufficient' and result['expected_treatment'] is None
