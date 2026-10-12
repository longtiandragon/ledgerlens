"""Locate arithmetic operands in one supplied document; no filesystem access.

Lexical value presence and source-cell locations do not verify the question's
meaning, units, choice of formula or accounting policy.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .financial_math import CONSTANTS, REFERENCE, NumericProgramError, decimal_literal, execute_numeric_program, parse_numeric_program

SOURCE_TOKEN = re.compile(r'(?<![\w.])[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)'
                          r'(?:[eE][+-]?\d{1,3})?(?:\s*%)?(?![\w%]|\.\d)')
DOCUMENT_FIELDS = {'id', 'question', 'pre_text', 'post_text', 'table', 'locators'}


def document_sources(document: dict) -> dict[str, str]:
    if (not isinstance(document, dict) or set(document) != DOCUMENT_FIELDS
            or not isinstance(document['id'], str) or not document['id']
            or not isinstance(document['question'], str)):
        raise NumericProgramError('invalid_observed_document')
    sources = {'question': document['question']}
    for field in ('pre_text', 'post_text'):
        if not isinstance(document[field], list) or any(not isinstance(value, str) for value in document[field]):
            raise NumericProgramError('invalid_observed_document')
        sources.update({f'{field}:{index}': value for index, value in enumerate(document[field])})
    if not isinstance(document['table'], list):
        raise NumericProgramError('invalid_observed_document')
    for row_index, row in enumerate(document['table']):
        if not isinstance(row, list) or any(not isinstance(cell, str) for cell in row):
            raise NumericProgramError('invalid_observed_document')
        sources.update({f'table:{row_index}:{column}': cell for column, cell in enumerate(row)})
    if len(sources) > 10_000 or sum(len(value.encode('utf-8')) for value in sources.values()) > 1_000_000:
        raise NumericProgramError('document_size_limit')
    return sources


def locate_program_operands(program: str, document: dict, cited_source_ids: list[str]) -> dict:
    sources = document_sources(document)
    if (not isinstance(cited_source_ids, list) or not cited_source_ids or len(cited_source_ids) > 30
            or any(not isinstance(identity, str) or identity not in sources for identity in cited_source_ids)):
        raise NumericProgramError('citation_outside_document')
    values: dict[Decimal, list[dict]] = {}
    for identity in dict.fromkeys(cited_source_ids):
        for match in SOURCE_TOKEN.finditer(sources[identity]):
            token = re.sub(r'\s+%', '%', match[0])
            try:
                value = decimal_literal(token)
            except NumericProgramError:
                continue
            variants = [(value, 'source_numeric_lexeme')]
            if token.endswith('%'):
                # The source explicitly contains both a percentage-point
                # lexeme and a ratio representation. Record that distinction.
                variants.append((decimal_literal(token[:-1]), 'percentage_points'))
            for number, representation in variants:
                values.setdefault(number, []).append({'source_evidence_id': identity,
                    'character_span': [match.start(), match.end()], 'representation': representation})
    bindings, constants = [], set()
    for index, step in enumerate(parse_numeric_program(program)):
        if step.operation.startswith('table_'):
            continue  # actual row/cell locations come from the calculator below
        for argument, token in [('left', step.left), ('right', step.right)]:
            if REFERENCE.fullmatch(token):
                continue
            if token in CONSTANTS:
                constants.add(token)
                continue
            candidates = values.get(decimal_literal(token), [])
            if not candidates:
                raise NumericProgramError('operand_not_in_cited_sources', index)
            bindings.append({'step': index, 'argument': argument, 'literal': token,
                             'source_candidates': candidates[:10], 'candidate_count': len(candidates)})
    actual = execute_numeric_program(program, document['table'])
    table_sources = []
    for step in actual['steps']:
        cells = step['source_table_cells']
        if cells:
            table_sources.extend(f'table:{cells["row"]}:{column}' for column in [0, *cells['columns']])
    if not set(table_sources) <= sources.keys():
        raise NumericProgramError('calculation_source_outside_document')
    return {'document_id': document['id'], 'cited_source_ids': list(dict.fromkeys(cited_source_ids)),
            'operand_bindings': bindings, 'calculation_table_source_ids': list(dict.fromkeys(table_sources)),
            'used_constants': sorted(constants), 'actual': actual,
            'grounding_status': 'lexical_values_and_table_cells_located', 'semantic_status': 'unverified'}
