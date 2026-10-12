"""Bounded financial arithmetic DSL: Decimal operations, never Python execution.

This calculator verifies arithmetic only. Caller-supplied operands, units and
source grounding are not verified here. No source files or evaluator answers
are read, and this module is not yet exposed to investigation Agents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, ROUND_HALF_EVEN, Underflow, localcontext

VERSION = 'financial-math-v3'
OPERATIONS = frozenset({'add', 'subtract', 'multiply', 'divide', 'exp', 'greater',
                        'table_sum', 'table_average', 'table_min', 'table_max'})
CONSTANTS = {f'const_{value}': Decimal(value) for value in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 100, 1000, 1000000)}
CONSTANTS['const_m1'] = Decimal(-1)
NUMBER = re.compile(r'[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?%?\Z')
REFERENCE = re.compile(r'#(0|[1-9]\d{0,2})\Z')
MAX_STEPS = 20
MAX_VALUE = Decimal('1e100')


class NumericProgramError(ValueError):
    def __init__(self, code: str, step: int | None = None):
        super().__init__(code)
        self.code, self.step = code, step


@dataclass(frozen=True)
class NumericStep:
    operation: str
    left: str
    right: str


def decimal_literal(value: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 128 or not NUMBER.fullmatch(value.strip()):
        raise NumericProgramError('invalid_numeric_literal')
    text = value.strip().replace(',', '')
    with localcontext() as context:
        context.prec = max(50, len(text) + 4)
        number = Decimal(text[:-1]).scaleb(-2) if text.endswith('%') else Decimal(text)
    if not number.is_finite() or number.copy_abs() > MAX_VALUE or (number and number.copy_abs() < Decimal('1e-100')):
        raise NumericProgramError('numeric_size_limit')
    return number


def _atom(value: str) -> bool:
    return bool(NUMBER.fullmatch(value) or REFERENCE.fullmatch(value) or value in CONSTANTS)


def parse_numeric_program(program: str) -> tuple[NumericStep, ...]:
    if not isinstance(program, str) or not program.strip() or len(program.encode('utf-8')) > 12_000:
        raise NumericProgramError('invalid_program_size')
    text, position, steps = program.strip(), 0, []
    while position < len(text):
        match = re.match(r'([a-z_]+)\(([^()]*)\)', text[position:])
        if not match:
            raise NumericProgramError('invalid_program_syntax', len(steps))
        operation, arguments = match.groups()
        if operation not in OPERATIONS:
            raise NumericProgramError('unsupported_operation', len(steps))
        if operation.startswith('table_'):
            left, separator, right = arguments.rpartition(',')
            if not separator or not left.strip() or right.strip() != 'none':
                raise NumericProgramError('invalid_table_arguments', len(steps))
            pairs = [(left.strip(), right.strip())]
        else:
            pairs = [(arguments[:index].strip(), arguments[index + 1:].strip())
                     for index, char in enumerate(arguments) if char == ',']
            pairs = [(left, right) for left, right in pairs if _atom(left) and _atom(right)]
            if len(pairs) != 1:
                raise NumericProgramError('invalid_or_ambiguous_operands', len(steps))
        steps.append(NumericStep(operation, *pairs[0]))
        if len(steps) > MAX_STEPS:
            raise NumericProgramError('step_limit')
        position += match.end()
        if position == len(text):
            break
        separator = re.match(r',\s*', text[position:])
        if not separator or position + separator.end() == len(text):
            raise NumericProgramError('invalid_program_syntax', len(steps))
        position += separator.end()
    return tuple(steps)


def _operand(token: str, results: list[Decimal | str], step: int) -> Decimal:
    reference = REFERENCE.fullmatch(token)
    if reference:
        index = int(reference[1])
        if index >= step:
            raise NumericProgramError('forward_or_missing_reference', step)
        value = results[index]
        if not isinstance(value, Decimal):
            raise NumericProgramError('comparison_used_as_number', step)
        return value
    if token in CONSTANTS:
        return CONSTANTS[token]
    return decimal_literal(token)


def _table_number(value: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 200:
        raise NumericProgramError('invalid_table_cell')
    text = value.strip()
    if text.startswith('$'):
        text = text[1:].strip()
    text = re.sub(r'\s+%', '%', text)
    repeated = re.fullmatch(r'([^()]+)\(\s*([^()]+)\s*\)', text)
    if repeated and not re.fullmatch(r'[a-zA-Z]', repeated[2].strip()):
        head, repeated_value = decimal_literal(repeated[1].strip()), decimal_literal(repeated[2].strip())
        if head.copy_abs() != repeated_value.copy_abs():
            raise NumericProgramError('conflicting_cell_representation')
        return head  # preserve the explicit prefix sign, after verifying the repeated magnitude
    # A whole accounting parenthesis is negative; a trailing letter footnote
    # is metadata. Never strip an arbitrary parenthesized amount or annotation.
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1].strip()
    else:
        text = re.sub(r'\s*\([a-zA-Z]\)\s*$', '', text)
    number = decimal_literal(text)
    if negative and number < 0:
        raise NumericProgramError('ambiguous_table_sign')
    return -number if negative else number


def _row(table: list[list[str]], label: str, step: int) -> tuple[int, list[Decimal]]:
    candidates = [(index, row) for index, row in enumerate(table) if row and row[0] == label]
    if not candidates:
        raise NumericProgramError('table_row_not_found', step)
    if len(candidates) != 1:
        raise NumericProgramError('ambiguous_table_row', step)
    index, row = candidates[0]
    if len(row) < 2:
        raise NumericProgramError('empty_table_row', step)
    return index, [_table_number(cell) for cell in row[1:]]


def execute_numeric_program(program: str, table: list[list[str]]) -> dict:
    steps = parse_numeric_program(program)
    if (not isinstance(table, list) or len(table) > 1000 or any(
            not isinstance(row, list) or len(row) > 100 or any(not isinstance(cell, str) for cell in row)
            for row in table)):
        raise NumericProgramError('invalid_table_shape')
    results, trace = [], []
    with localcontext() as context:
        context.prec, context.rounding, context.Emax, context.Emin = 50, ROUND_HALF_EVEN, 200, -200
        context.traps[Underflow] = True
        try:
            for index, step in enumerate(steps):
                context.clear_flags()
                sources = None
                approximate_inputs = any(REFERENCE.fullmatch(token) and
                                         int(token[1:]) < index and not trace[int(token[1:])]['arithmetic_exact']
                                         for token in (step.left, step.right))
                if step.operation.startswith('table_'):
                    row_index, values = _row(table, step.left, index)
                    sources = {'row': row_index, 'columns': list(range(1, len(values) + 1))}
                    if step.operation == 'table_sum':
                        result = sum(values, Decimal(0))
                    elif step.operation == 'table_average':
                        total = sum(values, Decimal(0))
                        if context.flags[Inexact]:
                            raise NumericProgramError('arithmetic_precision_exceeded', index)
                        result = total / Decimal(len(values))
                    elif step.operation == 'table_min':
                        result = min(values)
                    else:
                        result = max(values)
                    operands = [str(value) for value in values]
                else:
                    left, right = _operand(step.left, results, index), _operand(step.right, results, index)
                    operands = [str(left), str(right)]
                    if step.operation == 'add':
                        result = left + right
                    elif step.operation == 'subtract':
                        result = left - right
                    elif step.operation == 'multiply':
                        result = left * right
                    elif step.operation == 'divide':
                        if not right:
                            raise NumericProgramError('division_by_zero', index)
                        result = left / right
                    elif step.operation == 'greater':
                        if approximate_inputs:
                            raise NumericProgramError('comparison_of_rounded_values', index)
                        result = 'yes' if left > right else 'no'
                    else:
                        if abs(right) > 1000:
                            raise NumericProgramError('exponent_limit', index)
                        result = context.power(left, right)
                if (context.flags[Inexact] and not approximate_inputs
                        and step.operation in {'add', 'subtract', 'multiply', 'table_sum'}):
                    raise NumericProgramError('arithmetic_precision_exceeded', index)
                if isinstance(result, Decimal) and (not result.is_finite() or result.copy_abs() > MAX_VALUE
                                                    or result and result.copy_abs() < Decimal('1e-100')):
                    raise NumericProgramError('result_size_limit', index)
                results.append(result)
                trace.append({'step': index, 'operation': step.operation, 'operands': operands,
                              'result': str(result), 'source_table_cells': sources,
                              'rounded_at_precision': context.flags[Inexact],
                              'inputs_from_rounded_results': approximate_inputs,
                              'arithmetic_exact': not context.flags[Inexact] and not approximate_inputs})
        except DecimalException:
            raise NumericProgramError('decimal_operation_undefined', len(results)) from None
    return {'schema_version': VERSION, 'value': str(results[-1]),
            'value_kind': 'decimal' if isinstance(results[-1], Decimal) else 'comparison',
            'steps': trace, 'precision': 50, 'rounding': 'ROUND_HALF_EVEN',
            'input_grounding': 'not_verified', 'unit_semantics': 'caller_defined'}
