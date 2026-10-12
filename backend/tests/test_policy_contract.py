"""Input contracts for caller-supplied, source-bound policy observations.

These tests do not prove HTTP authorization. The context is trusted component
input; an eventual API must build its membership from server-side snapshots.
"""
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.policy_contract import (
    ConditionEvidence,
    ConfirmedCondition,
    PolicyCheckContext,
    PolicyObservation,
)


KNOWN = '2026-10-01T08:00:00+00:00'
AS_OF = '2026-10-02T08:00:00+00:00'


def evidence_payload(**changes):
    return {'evidence_id': 'ev-condition', 'book_id': 'book-A', 'snapshot_id': 'snapshot-A',
            'known_at': KNOWN, **changes}


def context_payload(**changes):
    return {'book_id': 'book-A', 'snapshot_id': 'snapshot-A', 'framework': 'CAS', 'period': '2026-10',
            'as_of': AS_OF, 'evidence': [evidence_payload()], **changes}


def observation_payload(**changes):
    return {'observation_id': 'observation-A', 'book_id': 'book-A', 'snapshot_id': 'snapshot-A',
            'topic': 'resale_inventory', 'conditions': {}, **changes}


def parse(model, payload):
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


def test_wire_dates_parse_strictly_and_preserve_timezone():
    result = parse(PolicyCheckContext, context_payload())
    assert result.as_of == datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
    assert result.evidence[0].known_at == datetime(2026, 10, 1, 8, tzinfo=timezone.utc)


def test_python_datetime_strings_are_not_silently_coerced():
    with pytest.raises(ValidationError):
        PolicyCheckContext.model_validate(context_payload())


@pytest.mark.parametrize('field', ['known_at', 'as_of'])
def test_naive_knowledge_times_rejected(field):
    payload = evidence_payload(known_at='2026-10-01T08:00:00') if field == 'known_at' else context_payload(
        as_of='2026-10-02T08:00:00')
    with pytest.raises(ValidationError, match='timezone'):
        parse(ConditionEvidence if field == 'known_at' else PolicyCheckContext, payload)


@pytest.mark.parametrize('field', ['evidence_id', 'book_id', 'snapshot_id'])
@pytest.mark.parametrize('bad', ['', 'x' * 201, 123, True])
def test_evidence_identity_bounds_and_types(field, bad):
    with pytest.raises(ValidationError):
        parse(ConditionEvidence, evidence_payload(**{field: bad}))


@pytest.mark.parametrize('period', ['2026-00', '2026-13', '2026-1', '2026-10-01', '1999-12', '', 202610])
def test_context_requires_supported_month_literal(period):
    with pytest.raises(ValidationError):
        parse(PolicyCheckContext, context_payload(period=period))


@pytest.mark.parametrize('change', [
    {'book_id': 'book-B'}, {'snapshot_id': 'snapshot-B'}, {'known_at': '2026-10-03T08:00:00+00:00'},
])
def test_context_rejects_outside_membership_and_future_evidence(change):
    with pytest.raises(ValidationError, match='outside'):
        parse(PolicyCheckContext, context_payload(evidence=[evidence_payload(**change)]))


def test_context_allows_evidence_at_exact_knowledge_cutoff():
    context = parse(PolicyCheckContext, context_payload(evidence=[evidence_payload(known_at=AS_OF)]))
    assert context.evidence[0].known_at == context.as_of


def test_context_compares_instants_with_different_offsets():
    context = parse(PolicyCheckContext, context_payload(
        evidence=[evidence_payload(known_at='2026-10-02T16:00:00+08:00')]))
    assert context.evidence[0].known_at == context.as_of


def test_context_cannot_duplicate_an_evidence_identity():
    with pytest.raises(ValidationError, match='Duplicate'):
        parse(PolicyCheckContext, context_payload(evidence=[evidence_payload(), evidence_payload()]))


def test_context_accepts_empty_evidence_but_does_not_invent_any():
    assert parse(PolicyCheckContext, context_payload(evidence=[])).evidence == []


@pytest.mark.parametrize('value', [True, False, 0, 1, 1.0, None, 'true', 'YES', ''])
def test_condition_only_accepts_explicit_tri_state_strings(value):
    with pytest.raises(ValidationError):
        parse(ConfirmedCondition, {'value': value})


@pytest.mark.parametrize('value', ['yes', 'no'])
def test_human_confirmation_needs_original_evidence(value):
    with pytest.raises(ValidationError, match='requires original evidence'):
        parse(ConfirmedCondition, {'value': value, 'confirmation': 'human_confirmed'})


@pytest.mark.parametrize('confirmation', ['source_reported', 'model_suggested', 'unconfirmed'])
def test_unconfirmed_values_remain_distinct_from_human_facts(confirmation):
    condition = parse(ConfirmedCondition, {'value': 'yes', 'confirmation': confirmation})
    assert condition.value == 'yes'
    assert condition.confirmation == confirmation
    assert condition.evidence_ids == []


def test_human_unknown_is_valid_without_fabricated_evidence():
    assert parse(ConfirmedCondition, {'value': 'unknown', 'confirmation': 'human_confirmed'}).value == 'unknown'


@pytest.mark.parametrize('references', [['same', 'same'], [f'e{i}' for i in range(11)]])
def test_condition_evidence_duplicates_and_count_are_rejected(references):
    with pytest.raises(ValidationError):
        parse(ConfirmedCondition, {'value': 'yes', 'confirmation': 'human_confirmed', 'evidence_ids': references})


@pytest.mark.parametrize('field', ['observation_id', 'book_id', 'snapshot_id'])
@pytest.mark.parametrize('bad', ['', 'x' * 201, 123])
def test_observation_identity_constraints(field, bad):
    with pytest.raises(ValidationError):
        parse(PolicyObservation, observation_payload(**{field: bad}))


@pytest.mark.parametrize('predicate', ['capitalization_criteria_met', 'prompt', 'arbitrary_sql', 'unknown'])
def test_observation_predicates_are_topic_specific(predicate):
    with pytest.raises(ValidationError, match='Unsupported condition predicate'):
        parse(PolicyObservation, observation_payload(conditions={predicate: {'value': 'unknown'}}))


def test_missing_conditions_remain_missing_for_engine_to_abstain():
    result = parse(PolicyObservation, observation_payload())
    assert result.conditions == {}
    assert result.reported_treatment == 'unknown'


def test_reported_treatment_cannot_be_asserted_without_source():
    with pytest.raises(ValidationError, match='Reported treatment requires original evidence'):
        parse(PolicyObservation, observation_payload(reported_treatment='inventory'))


def test_human_confirmed_beneficiary_requires_source():
    with pytest.raises(ValidationError, match='Confirmed beneficiary requires original evidence'):
        parse(PolicyObservation, observation_payload(topic='routine_repair', beneficiary='administration',
                                                     beneficiary_confirmation='human_confirmed'))


@pytest.mark.parametrize('field', ['treatment_evidence_ids', 'beneficiary_evidence_ids'])
def test_observation_duplicate_references_rejected(field):
    with pytest.raises(ValidationError, match='Duplicate'):
        parse(PolicyObservation, observation_payload(**{field: ['ev-condition', 'ev-condition']}))


@pytest.mark.parametrize('model,payload', [
    (ConditionEvidence, evidence_payload()),
    (ConfirmedCondition, {'value': 'unknown'}),
    (PolicyObservation, observation_payload()),
    (PolicyCheckContext, context_payload()),
])
def test_contracts_reject_extra_fields(model, payload):
    payload = deepcopy(payload)
    payload['execute'] = 'not an allowed tool'
    with pytest.raises(ValidationError, match='Extra inputs'):
        parse(model, payload)


def test_nested_model_suggestions_cannot_pass_arbitrary_instructions():
    with pytest.raises(ValidationError, match='Extra inputs'):
        parse(PolicyObservation, observation_payload(conditions={
            'ordinary_course': {'value': 'yes', 'confirmation': 'model_suggested', 'sql': 'arbitrary'},
        }))


def test_contract_fields_cannot_be_reassigned():
    observation = parse(PolicyObservation, observation_payload())
    with pytest.raises(ValidationError, match='frozen'):
        observation.reported_treatment = 'inventory'
