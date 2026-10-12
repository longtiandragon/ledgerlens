from copy import deepcopy
from decimal import Decimal

import pytest
from app.financial_evidence import document_sources, locate_program_operands
from app.financial_math import NumericProgramError


def document():
    return {'id': 'independent-fixture', 'question': 'What is the increase?',
            'pre_text': ['Revenue grew from 20 to 30.'], 'post_text': ['Margin was 5%.'],
            'table': [['year', 'revenue'], ['2021', '$30'], ['2020', '$20']], 'locators': {}}


def test_literals_must_exist_in_cited_current_document_sources():
    doc = document()
    before = deepcopy(doc)
    result = locate_program_operands('subtract(30, 20)', doc, ['pre_text:0'])
    assert result['actual']['value'] == '10'
    assert result['document_id'] == doc['id'] and result['semantic_status'] == 'unverified'
    assert result['operand_bindings'][0]['source_candidates'][0]['character_span'] == [24, 26]
    assert doc == before
    with pytest.raises(NumericProgramError, match='operand_not_in_cited_sources'):
        locate_program_operands('subtract(30, 99)', doc, ['pre_text:0'])
    with pytest.raises(NumericProgramError, match='operand_not_in_cited_sources'):
        locate_program_operands('subtract(30, 20)', doc, ['post_text:0'])


def test_constants_and_percentage_representations_are_explicit():
    result = locate_program_operands('multiply(5%, const_100)', document(), ['post_text:0'])
    assert Decimal(result['actual']['value']) == 5
    assert result['used_constants'] == ['const_100']
    assert result['operand_bindings'][0]['source_candidates'][0]['representation'] == 'source_numeric_lexeme'
    result = locate_program_operands('divide(5, const_100)', document(), ['post_text:0'])
    assert result['operand_bindings'][0]['source_candidates'][0]['representation'] == 'percentage_points'


def test_table_operation_retains_all_used_source_cell_positions():
    result = locate_program_operands('table_sum(2021, none)', document(), ['table:1:0'])
    assert result['actual']['value'] == '30'
    assert result['calculation_table_source_ids'] == ['table:1:0', 'table:1:1']


@pytest.mark.parametrize('citations', [[], ['other-record:pre_text:0'], ['table:99:99'], ['../../truth/answers']])
def test_wrong_scope_and_path_like_citations_are_rejected(citations):
    with pytest.raises(NumericProgramError, match='citation_outside_document'):
        locate_program_operands('add(20, 30)', document(), citations)


def test_answer_bearing_fields_are_not_accepted_as_runtime_document():
    doc = document()
    doc['qa'] = {'exe_ans': 10, 'program': 'subtract(30, 20)'}
    with pytest.raises(NumericProgramError, match='invalid_observed_document'):
        document_sources(doc)
