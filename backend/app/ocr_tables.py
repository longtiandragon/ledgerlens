"""Conservative geometry-based table candidates from verified OCR observations.

No evaluator, image synthesis, model calls or ledger writes. Unknown layouts and
ambiguous cells remain explicit observations, never repaired financial facts.
"""
from datetime import date
from decimal import Decimal
import re
from statistics import median

from .ocr_observations import canonical, sha

VERSION = 'ocr-table-candidates-v2'
HEADERS = {
    '日期': 'business_date', '业务日期': 'business_date',
    '金额': 'amount', '业务金额': 'amount',
    '编号': 'source_id', '来源编号': 'source_id', '流水号': 'source_id',
    '摘要': 'description', '说明': 'description',
    '交易对手': 'counterparty', '往来单位': 'counterparty',
    '订单号': 'order_id', '发票号': 'invoice_id', '参考编号': 'reference',
}
# These labels identify a table boundary, not a supported field mapping. In
# particular, income/expense or debit/credit must never inherit a prior amount.
UNSUPPORTED_HEADERS = {'收入', '支出', '借方', '贷方', '借方金额', '贷方金额', '余额', '单价', '数量'}
FOOTERS = ('合计', '总计', '备注', '制表', '审核')
MAX_ROWS = 100


def box(line):
    points = line['locator']['polygon_px']
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def group_lines(lines):
    """Bound vertical spread instead of transitive overlap that merges nearby rows."""
    ordered = sorted(lines, key=lambda r: ((box(r)[1] + box(r)[3]) / 2, box(r)[0], r['observation_id']))
    groups = []
    for line in ordered:
        left, top, right, bottom = box(line)
        center, height = (top + bottom) / 2, bottom - top
        if groups:
            previous = groups[-1]
            centers = [(box(r)[1] + box(r)[3]) / 2 for r in previous]
            heights = [box(r)[3] - box(r)[1] for r in previous]
            if (abs(center - median(centers)) <= min(height, median(heights)) * .45
                    and max([*centers, center]) - min([*centers, center]) <= min([*heights, height]) * .65):
                previous.append(line)
                continue
        groups.append([line])
    return [sorted(group, key=lambda r: (box(r)[0], r['observation_id'])) for group in groups]


def field_value(field, raw):
    text = raw.strip()
    if field == 'business_date':
        if not re.fullmatch(r'[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}', text):
            return None, 'invalid_date'
        try:
            return date.fromisoformat(text.replace('/', '-')).isoformat(), None
        except ValueError:
            return None, 'invalid_date'
    if field == 'amount':
        if len(text) > 64 or not re.fullmatch(r'-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?', text):
            return None, 'invalid_amount'
        return format(Decimal(text.replace(',', '')), '.2f'), None
    if not text or len(text) > (4000 if field == 'description' else 200):
        return None, 'invalid_text_length'
    return text, None


def candidates(result, page_number):
    pages = result.get('pages') or [{**result, 'page': 1}]
    page = next((p for p in pages if p['page'] == page_number), None)
    if page is None:
        raise ValueError('OCR page does not exist')
    digest = sha(canonical(result))
    output = {'schema_version': VERSION, 'result_digest': digest, 'page': page_number,
              'confirmation': 'unconfirmed', 'ledger_writes': 0, 'tables': [], 'unassigned': [], 'issues': []}
    if page.get('status') == 'failed':
        return {**output, 'status': 'page_failed', 'issues': ['failed_page_has_no_candidates']}
    lines = page.get('observations', [])
    if len(lines) > 2000:
        return {**output, 'status': 'limit_exceeded', 'issues': ['too_many_observations']}
    if not lines:
        return {**output, 'status': 'no_table', 'issues': ['no_text']}
    groups = group_lines(lines)
    width = page['image']['width']
    table, count = None, 0
    for group in groups:
        labels = [re.sub(r'\s+', '', row['text']) for row in group]
        keys = [HEADERS.get(label) for label in labels]
        recognized = [key for key in keys if key]
        ids = [row['observation_id'] for row in group]
        if 'business_date' in recognized and 'amount' in recognized and len(recognized) >= 3:
            if None in keys or len(set(keys)) != len(keys):
                output['unassigned'].append({'observation_ids': ids, 'reason': 'ambiguous_header'})
                output['issues'].append('ambiguous_header')
                table = None
                continue
            centers = [(box(row)[0] + box(row)[2]) / 2 for row in group]
            boundaries = [max(0, centers[0] - (centers[1] - centers[0]) / 2),
                          *[(a + b) / 2 for a, b in zip(centers, centers[1:])],
                          min(width, centers[-1] + (centers[-1] - centers[-2]) / 2)]
            if (any(a >= b for a, b in zip(boundaries, boundaries[1:]))
                    or any(box(row)[0] < boundaries[i] or box(row)[2] > boundaries[i + 1] for i, row in enumerate(group))):
                output['unassigned'].append({'observation_ids': ids, 'reason': 'overlapping_header'})
                output['issues'].append('overlapping_header')
                table = None
                continue
            table = {'table_id': sha(canonical({'version': VERSION, 'result': digest, 'headers': ids})),
                     'headers': [{'field': key, 'text': row['text'], 'observation_id': row['observation_id'],
                                  'left': boundaries[i], 'right': boundaries[i + 1]}
                                 for i, (key, row) in enumerate(zip(keys, group))], 'rows': []}
            output['tables'].append(table)
            continue
        if sum(label in HEADERS or label in UNSUPPORTED_HEADERS for label in labels) >= 2:
            # A partial, changed or unsupported header ends the preceding table.
            # Preserve its observations and wait for a complete supported header.
            output['unassigned'].append({'observation_ids': ids, 'reason': 'unsupported_header_boundary'})
            if 'unsupported_header_boundary' not in output['issues']:
                output['issues'].append('unsupported_header_boundary')
            table = None
            continue
        if table is None:
            output['unassigned'].append({'observation_ids': ids, 'reason': 'outside_recognized_table'})
            continue
        if group[0]['text'].strip().startswith(FOOTERS):
            output['unassigned'].append({'observation_ids': ids, 'reason': 'footer_requires_manual_review'})
            table = None
            continue
        if count >= MAX_ROWS:
            output['unassigned'].append({'observation_ids': ids, 'reason': 'row_budget_exhausted'})
            continue
        count += 1
        cells = {header['field']: [] for header in table['headers']}
        issues, unmatched = [], []
        for line in group:
            left, _, right, _ = box(line)
            matches = [h for h in table['headers'] if h['left'] <= left and right <= h['right']]
            if len(matches) != 1:
                unmatched.append(line['observation_id'])
                issues.append('crossing_or_outside_column')
            else:
                cells[matches[0]['field']].append(line)
        proposed, evidence, displayed = {}, {}, {}
        for field, entries in cells.items():
            displayed[field] = {'text': '\n'.join(e['text'] for e in entries),
                                'observation_ids': [e['observation_id'] for e in entries]}
            if len(entries) > 1:
                issues.append('multiple_observations_in_cell:' + field)
            elif len(entries) == 1:
                value, problem = field_value(field, entries[0]['text'])
                if problem:
                    issues.append(problem + ':' + field)
                else:
                    proposed[field] = value
                    if field != 'source_id':
                        evidence[field] = [entries[0]['observation_id']]
        for field in ('business_date', 'amount'):
            if field not in proposed:
                issues.append('missing_valid_field:' + field)
        ambiguous = bool(unmatched) or any(len(entries) > 1 for entries in cells.values())
        identity = sha(canonical({'version': VERSION, 'result': digest, 'table': table['table_id'], 'observations': ids}))
        table['rows'].append({'candidate_id': identity, 'observation_ids': ids, 'cells': displayed,
                              'record': proposed, 'field_evidence': evidence, 'issues': sorted(set(issues)),
                              'copyable': not ambiguous, 'unassigned_observation_ids': unmatched,
                              'confirmation': 'unconfirmed'})
    exhausted = any(g['reason'] == 'row_budget_exhausted' for g in output['unassigned'])
    output['status'] = 'limit_exceeded' if exhausted else 'requires_manual_review' if output['tables'] else 'no_table'
    if exhausted:
        output['issues'].append('row_budget_exhausted')
    return output
