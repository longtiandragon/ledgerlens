from decimal import Decimal
from fractions import Fraction

import pytest
from app.financial_math import NumericProgramError, execute_numeric_program
from benchmarks.finqa_program_replay import compare_external_answer, replay_case


@pytest.mark.parametrize('program,expected', [
    ('add(0.1, 0.2)', '0.3'), ('subtract(2, 5)', '-3'), ('multiply(1.25, 8)', '10.00'),
    ('divide(1, 8)', '0.125'), ('divide(50%, 2)', '0.25'), ('exp(9, 0.5)', '3.0000000000000000000000000000000000000000000000000'),
    ('multiply(const_m1, 4)', '-4'), ('greater(2, 1)', 'yes'), ('greater(1, 1)', 'no'),
    ('add(1,234, 5)', '1239'), ('add(2, 3), multiply(#0, 4)', '20'),
    ('add(.3, .45)', '.75'), ('subtract(1., .3)', '.7'),
])
def test_handwritten_arithmetic(program, expected):
    actual = execute_numeric_program(program, [])['value']
    if expected in {'yes', 'no'}:
        assert actual == expected
    else:
        assert Decimal(actual) == Decimal(expected)


def test_independent_rational_reference_for_repeating_division():
    # The oracle uses rational arithmetic, never the production calculator.
    expected = Fraction(1, 3) * Fraction(7, 5)
    value = Decimal(execute_numeric_program('divide(1, 3), multiply(#0, 1.4)', [])['value'])
    assert abs(value - Decimal(expected.numerator) / Decimal(expected.denominator)) < Decimal('1e-27')
    result = execute_numeric_program('divide(1, 3), multiply(#0, 1.4)', [])
    assert not result['steps'][0]['arithmetic_exact']
    assert result['steps'][1]['inputs_from_rounded_results']
    assert not result['steps'][1]['arithmetic_exact']


@pytest.mark.parametrize('operation,expected', [('table_sum', '9'), ('table_average', '3'),
                                              ('table_min', '-1'), ('table_max', '8')])
def test_source_table_cells_and_decimal_metadata(operation, expected):
    result = execute_numeric_program(f'{operation}(profits, none)', [['profits', '$2', '(1)', '8(a)']])
    assert Decimal(result['value']) == Decimal(expected)
    assert result['steps'][0]['source_table_cells'] == {'row': 0, 'columns': [1, 2, 3]}
    assert result['input_grounding'] == 'not_verified' and result['unit_semantics'] == 'caller_defined'


@pytest.mark.parametrize('program', [
    '__import__(os, system)', 'add(1, 2); print(3)', 'add(1, 2),', 'add(1, 2), EOF',
    'eval(1, 2)', 'add(1, 2, 3)', 'add(nan, 1)', 'multiply(inf, 1)', 'add(const_unknown, 1)',
    'divide(1, 0)', 'add(#0, 1)', 'add(#9, 1)', 'greater(2, 1), add(#0, 1)',
    'exp(2, 1001)', 'table_sum(profits, arbitrary)', 'add(1e101, 1)',
    'divide(1, 3), greater(#0, 0.3)',
])
def test_unsafe_undefined_or_unbounded_inputs_are_rejected(program):
    with pytest.raises(NumericProgramError):
        execute_numeric_program(program, [])


@pytest.mark.parametrize('table', [[['profits', '—']], [['profits', '1(unknown)']],
                                  [['profits', '1'], ['profits', '2']], [['profits']]])
def test_missing_ambiguous_and_unknown_cells_never_become_zero(table):
    with pytest.raises(NumericProgramError):
        execute_numeric_program('table_sum(profits, none)', table)


def test_redundant_numeric_cell_representation_is_checked_and_preserves_prefix_sign():
    value = execute_numeric_program('table_sum(profits, none)',
                                    [['profits', '$ -20 ( 20 )', '12 (12)', '-3 ( 3 )']])
    assert value['value'] == '-11'
    value = execute_numeric_program('table_max(margin, none)',
                                    [['margin', '26% ( 26 % )', '20.3% ( 20.3 % )']])
    assert Decimal(value['value']) == Decimal('.26')
    with pytest.raises(NumericProgramError, match='conflicting_cell_representation'):
        execute_numeric_program('table_sum(profits, none)', [['profits', '12 (13)']])


def test_precision_loss_is_explicit_for_exact_operations_and_reported_for_division():
    with pytest.raises(NumericProgramError, match='arithmetic_precision_exceeded'):
        execute_numeric_program('add(1e99, 1)', [])
    with pytest.raises(NumericProgramError, match='result_size_limit'):
        execute_numeric_program('multiply(1e-100, 1e-100)', [])
    assert execute_numeric_program('divide(1, 3)', [])['steps'][0]['rounded_at_precision']
    assert not execute_numeric_program('add(.1, .2)', [])['steps'][0]['rounded_at_precision']


def test_step_limit_and_bad_table_shape():
    with pytest.raises(NumericProgramError, match='step_limit'):
        execute_numeric_program(', '.join(['add(1, 2)'] * 21), [])
    with pytest.raises(NumericProgramError, match='invalid_table_shape'):
        execute_numeric_program('add(1, 2)', [[1, 2]])


def test_external_answers_not_modified_or_rounded_to_make_calculator_pass():
    actual = execute_numeric_program('divide(1, 3)', [])
    assert compare_external_answer(actual, '0.33333')['exact_at_5_decimal_places']
    assert not compare_external_answer(actual, '0.333333')['exact_at_5_decimal_places']
    assert not compare_external_answer(actual, '99')['within_half_last_decimal']
    observed = {'id': 'fixture', 'table': []}
    evaluator = {'id': 'fixture', 'qa': {'program': 'add(1, 2)', 'exe_ans': '9'}}
    assert not replay_case(observed, evaluator)['exact_at_5_decimal_places']
    assert evaluator['qa']['exe_ans'] == '9'
