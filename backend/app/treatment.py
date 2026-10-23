"""Deterministic treatment drafts for month-end cases (Apache-2.0).

A draft is a starting point for the isolated simulation, never a posting. It is
derived only from the case's own finding and the records of its frozen
snapshot. Investigation results (rules or model) are attached as context and
never change the proposed lines. Where the snapshot cannot determine the
correction, the draft says so and names what is missing instead of guessing
entries; blank fields mark inputs a person must supply.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from .domain import CAS_SOURCE, SCAS_SOURCE, ZERO, formatted, money, month_end_findings, scenario_of

TREATMENT_VERSION = 'treatment-drafts-v1'
_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()
PRIOR_PERIOD_POLICY = {
    'rule_id': 'POLICY_CAS_PRIOR_PERIOD_ERROR_MATERIALITY',
    'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/kjzckjgjbgzzsswd/202504/t20250417_3962193.htm',
}
# Fields a simulation row may carry (LedgerInput minus server-assigned ones).
LINE_FIELDS = ('voucher_id', 'account', 'debit', 'credit', 'business_date', 'period',
               'invoice_id', 'order_id', 'counterparty', 'description')
# Fields compared when deciding whether a person changed a draft.
ACCOUNTING_FIELDS = ('voucher_id', 'account', 'debit', 'credit', 'business_date', 'period',
                     'invoice_id', 'order_id', 'counterparty')
EVIDENCE_KIND = {'RECEIPT_GAP': 'receipt', 'RECEIPT_EXCESS': 'order', 'INVOICE_EVIDENCE': 'invoice',
                 'INVOICE_CONFLICT': 'invoice', 'OPENING_REQUIRED': 'opening', 'TRIAL_BALANCE': 'trial_balance',
                 'PERIOD_VALIDITY': 'journal', 'PERIOD_CUTOFF': 'journal', 'BANK_RECONCILIATION_INPUT': 'bank',
                 'BANK_ACCOUNT_EVIDENCE': 'bank', 'BANK_ACCOUNT_SCOPE': 'bank', 'BANK_UNMATCHED': 'journal',
                 'LEDGER_UNMATCHED': 'bank', 'BANK_MATCH_AMBIGUOUS': 'bank', 'BANK_MATCH_BOUNDED': 'bank'}


def treatment_identity() -> dict[str, str]:
    return {'version': TREATMENT_VERSION, 'source_sha256': _SOURCE_SHA256}


def period_end(period: str) -> str:
    year, month = (int(part) for part in period.split('-'))
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _sort_key(row: dict) -> tuple:
    return str(row.get('business_date', '')), str(row.get('observed_at', '')), str(row.get('source_id', ''))


def _voucher(row: dict) -> str:
    return str(row.get('voucher_id') or row.get('source_id') or row.get('id'))


def _line(row: dict, *, voucher_id: str, debit: Decimal, credit: Decimal, business_date: str, period: str,
          description: str, account: str | None = None) -> dict:
    return {'voucher_id': voucher_id, 'account': str(row.get('account', '')) if account is None else account,
            'debit': formatted(debit), 'credit': formatted(credit), 'business_date': business_date, 'period': period,
            'invoice_id': str(row.get('invoice_id', '')), 'order_id': str(row.get('order_id', '')),
            'counterparty': str(row.get('counterparty', '')), 'description': description}


def _reversal(lines: list[dict], voucher: str, period: str, label: str) -> tuple[list[dict], list[dict]]:
    """Swap debit and credit of every line of one voucher, dated at period end."""
    when = period_end(period)
    result, basis = [], []
    for row in sorted(lines, key=lambda r: str(r.get('source_id', ''))):
        result.append(_line(row, voucher_id=f'SIM-REV-{voucher}', debit=money(row.get('credit', '0.00')),
                            credit=money(row.get('debit', '0.00')), business_date=when, period=period,
                            description=f'{label}（模拟草稿，非正式记账）'))
        basis.append({'source_record_ids': [row['id']], 'input_required': []})
    return result, basis


def _journals(records: list[dict], ids: set[str]) -> list[dict]:
    return [r for r in records if r.get('kind') == 'journal' and r.get('id') in ids]


def _cash_lines(bank_rows: list[dict], cash_account: str, period: str) -> tuple[list[dict], list[dict]]:
    """One balanced pair per unmatched statement line; the counter account is a required input."""
    lines, basis = [], []
    for row in sorted(bank_rows, key=_sort_key):
        amount = money(row.get('amount', '0.00'))
        size = abs(amount)
        when = str(row.get('business_date')) if str(row.get('business_date', ''))[:7] == period else period_end(period)
        voucher = f"SIM-BANK-{row.get('source_id')}"
        text = f"按银行流水 {row.get('source_id')} 补录（对方科目待确认，模拟草稿）"
        cash = _line(row, voucher_id=voucher, debit=size if amount > ZERO else ZERO,
                     credit=size if amount < ZERO else ZERO, business_date=when, period=period,
                     description=text, account=str(row.get('account') or cash_account))
        counter = _line(row, voucher_id=voucher, debit=size if amount < ZERO else ZERO,
                        credit=size if amount > ZERO else ZERO, business_date=when, period=period,
                        description=text, account='')
        lines += [cash, counter]
        basis += [{'source_record_ids': [row['id']], 'input_required': []},
                  {'source_record_ids': [row['id']], 'input_required': ['account']}]
    return lines, basis


def _unmatched_bank_rows(finding: dict, records: list[dict], policy: dict) -> list[dict]:
    by_id = {r['id']: r for r in records}
    if finding['rule_id'] == 'BANK_UNMATCHED':
        return [by_id[i] for i in finding['record_ids'] if by_id.get(i, {}).get('kind') == 'bank']
    account = finding.get('metrics', {}).get('account')
    siblings = [f for f in month_end_findings(records, policy) if f['rule_id'] == 'BANK_UNMATCHED'
                and f.get('metrics', {}).get('account') == account and set(f['record_ids']) <= set(finding['record_ids'])]
    return [by_id[i] for f in siblings for i in f['record_ids'] if by_id.get(i, {}).get('kind') == 'bank']


def _period_path(finding: dict, framework: str) -> tuple[str, str, str, str]:
    """(status, summary, reason, framework note) for a business date outside the posting period."""
    metrics = finding.get('metrics', {})
    business, posting = str(metrics.get('business_month', '')), str(metrics.get('posting_period', ''))
    if business > posting:
        return ('conditional', '确认提前入账后在本期冲回',
                f'业务月份 {business} 晚于记账期间 {posting}：如确认属于提前入账，可在本期冲回，'
                '下期按实际发生入账（下期不在本账套期间，未模拟）。确认前仅供测算。',
                '两种制度均按实际发生期间确认；提前入账在发现期冲回。')
    if business[:4] == posting[:4]:
        return ('not_applicable', '同年度迟到单据：说明原因，不调整分录',
                f'业务月份 {business} 早于记账期间 {posting}，属于同一会计年度内的迟到入账；'
                '跨期本身不构成错账，按月度截止要求说明原因即可，不需要调整分录。',
                '同一年度内的月度截止差异不影响年度报表。')
    if framework == 'SCAS':
        return ('not_applicable', '按未来适用法在发现当期处理，不追溯调整',
                f'业务年度 {business[:4]} 早于记账年度 {posting[:4]}。小企业会计准则下前期差错按未来适用法，'
                '在发现当期处理，不追溯调整。', f'小企业会计准则：未来适用法（{SCAS_SOURCE}）。')
    return ('needs_evidence', '先认定前期差错及重要性',
            f'业务年度 {business[:4]} 早于记账年度 {posting[:4]}。企业会计准则下须先认定是否构成前期差错并判断重要性：'
            '重要的追溯调整前期比较数据，不重要的调整发现当期相关项目。请在“账套与政策”记录人工确认的条件后再编制分录。',
            f"企业会计准则：见政策规则 {PRIOR_PERIOD_POLICY['rule_id']}（{PRIOR_PERIOD_POLICY['url']}）；"
            f'差错处理背景见 {CAS_SOURCE}。')


def propose(finding: dict, records: list[dict], policy: dict) -> dict:
    """Return the deterministic draft body for one finding of one snapshot."""
    rule = finding['rule_id']
    period = str(policy.get('period', ''))
    framework = str(policy.get('framework', 'CAS'))
    ids = set(finding.get('record_ids', []))
    metrics = finding.get('metrics', {})
    lines: list[dict] = []
    line_basis: list[dict] = []
    alternatives: list[dict] = []
    warnings: list[str] = []
    note = ''
    needs = list(finding.get('missing_evidence', []))

    if rule == 'DUPLICATE_INVOICE':
        amount = money(metrics['invoice_amount'])
        vouchers: dict[str, list[dict]] = {}
        for row in _journals(records, ids):
            vouchers.setdefault(_voucher(row), []).append(row)
        full = [v for v in metrics.get('voucher_ids', []) if v in vouchers and
                sum((money(r.get('debit', '0.00')) for r in vouchers[v]), ZERO) == amount]
        if len(full) < 2:
            status, summary = 'needs_input', '无法确定哪一张凭证是重复确认'
            reason = '确认凭证中金额等于发票金额的不足两张；请人工指定需要冲回的凭证。'
        else:
            ordered = sorted(full, key=lambda v: max(_sort_key(r) for r in vouchers[v]))
            chosen = ordered[-1]
            lines, line_basis = _reversal(vouchers[chosen], chosen, period, f'冲回重复确认凭证 {chosen}')
            alternatives = [{'summary': f'改为冲回凭证 {v}', 'voucher_id': v} for v in ordered[:-1]]
            status, summary = 'proposed', f'冲回重复确认凭证 {chosen}'
            reason = (f"发票 {metrics['invoice_id']} 金额 {metrics['invoice_amount']}，已确认 {metrics['recognized_amount']}；"
                      f'{len(full)} 张凭证各自全额确认。草稿冲回最后入账的一张，保留最先入账的一张。')
            warnings.append('应先核实两张凭证确属同一业务；如对方开具的是两张金额相同的发票，草稿不适用。')
    elif rule == 'INVOICE_OVERBOOKED':
        excess = money(metrics['excess'])
        candidates = []
        for voucher in metrics.get('voucher_ids', []):
            rows = [r for r in _journals(records, ids) if _voucher(r) == voucher]
            debits = [r for r in rows if money(r.get('debit', '0.00')) > ZERO]
            credits = [r for r in rows if money(r.get('credit', '0.00')) > ZERO]
            if len(debits) == 1 and len(credits) == 1 and money(debits[0]['debit']) >= excess:
                candidates.append((max(_sort_key(r) for r in rows), voucher, debits[0], credits[0]))
        if not candidates:
            status, summary = 'needs_input', '超额部分所在凭证无法自动确定'
            reason = f'确认凭证超过发票金额 {formatted(excess)}，但凭证结构不是单借单贷，需要人工指定冲减的分录。'
        else:
            candidates.sort()
            _, voucher, debit_row, credit_row = candidates[-1]
            when = period_end(period)
            text = f'冲减凭证 {voucher} 超过发票金额的部分（模拟草稿，非正式记账）'
            lines = [_line(credit_row, voucher_id=f'SIM-ADJ-{voucher}', debit=excess, credit=ZERO, business_date=when,
                           period=period, description=text),
                     _line(debit_row, voucher_id=f'SIM-ADJ-{voucher}', debit=ZERO, credit=excess, business_date=when,
                           period=period, description=text)]
            line_basis = [{'source_record_ids': [credit_row['id']], 'input_required': []},
                          {'source_record_ids': [debit_row['id']], 'input_required': []}]
            alternatives = [{'summary': f'改为冲减凭证 {v}', 'voucher_id': v} for _, v, _, _ in candidates[:-1]]
            status, summary = 'proposed', f'冲减凭证 {voucher} 的超额 {formatted(excess)}'
            reason = (f"发票 {metrics['invoice_id']} 金额 {metrics['invoice_amount']}，分拆确认合计 {metrics['recognized_amount']}，"
                      f'超额 {formatted(excess)}。草稿冲减最后入账的分拆凭证。')
            warnings.append('超额所在凭证须人工确认；分拆确认的其他凭证同样可能是错误来源。')
    elif rule == 'VOUCHER_BALANCE':
        difference = money(metrics['difference'])
        rows = sorted(_journals(records, ids), key=_sort_key)
        anchor = rows[-1] if rows else {}
        voucher = str(metrics.get('voucher_id'))
        text = f'补录凭证 {voucher} 遗漏的分录行（科目待确认，模拟草稿）'
        lines = [_line(anchor, voucher_id=voucher, debit=-difference if difference < ZERO else ZERO,
                       credit=difference if difference > ZERO else ZERO,
                       business_date=str(anchor.get('business_date') or period_end(period)),
                       period=str(anchor.get('period') or period), description=text, account='')]
        line_basis = [{'source_record_ids': sorted(r['id'] for r in rows), 'input_required': ['account']}]
        status, summary = 'needs_input', f'补录凭证 {voucher} 遗漏的{"贷方" if difference > ZERO else "借方"} {formatted(abs(difference))}'
        reason = f'凭证 {voucher} 借减贷差额 {metrics["difference"]}；金额与方向可由差额确定，遗漏行的科目必须依据原始凭证填写。'
        needs = needs or [f'凭证 {voucher} 的原始凭证或附件']
    elif rule == 'PERIOD_CUTOFF':
        status, summary, reason, note = _period_path(finding, framework)
        if status == 'conditional':
            rows = _journals(records, ids)
            vouchers = sorted({_voucher(r) for r in rows})
            for voucher in vouchers:
                voucher_rows = [r for r in records if r.get('kind') == 'journal' and _voucher(r) == voucher]
                more, more_basis = _reversal(voucher_rows, voucher, period, f'冲回提前入账的凭证 {voucher}')
                lines += more
                line_basis += more_basis
    elif rule in {'BANK_LEDGER_DIFFERENCE', 'BANK_UNMATCHED'}:
        rows = _unmatched_bank_rows(finding, records, policy)
        if rows:
            cash = str(metrics.get('account') or rows[0].get('account') or '1002')
            lines, line_basis = _cash_lines(rows, cash, period)
            status = 'needs_input'
            summary = f'按 {len(rows)} 条未匹配银行流水补录分录（对方科目待确认）'
            reason = ('未匹配的银行流水在账面没有对应分录。只有确认属于遗漏记账（如手续费、利息）时才补录；'
                      '未达账项、部分结算或缺失关联不应补录。')
            warnings.append('系统不会自动生成手续费或汇差；对方科目须依据银行回单填写。')
            needs = needs or ['银行回单或费用凭据']
        else:
            status, summary = 'needs_evidence', '先定位造成差异的流水或分录'
            reason = '差异没有对应的未匹配银行流水，可能来自未达账项或账面单边记录；请先补充银行回单或未达账项说明。'
            needs = needs or ['银行回单、未达账项说明或关联凭证']
    elif rule == 'BANK_CANDIDATES_ALIGNED':
        status, summary = 'not_applicable', '复核候选关联，不自动核销'
        reason = '本期间银行与账面发生额一致，候选关联金额一致；仍须复核确认，不自动核销。'
    elif rule == 'PRIOR_FINDING_CLEARED':
        status, summary = 'not_applicable', '核对补证记录与资料变化后复核'
        reason = '上次检查的发现在本快照中未再触发；请核对前一案件的补证记录与资料变化后复核，不另编调整分录。'
    elif rule == 'ACCOUNT_VALIDITY':
        status, summary = 'not_applicable', '确认科目映射，不调整分录'
        reason = (f"科目 {metrics.get('account', '')} 不在账套科目表中。映射缺口通过在“账套与政策”确认科目表处理（形成新的政策版本）；"
                  '只有确认业务科目选用错误时，才另行编制重分类草稿。')
    elif rule in {'JOURNAL_SIGN', 'BANK_DIRECTION_CONFLICT'}:
        status, summary = 'not_applicable', '修订导入资料，不调整分录'
        reason = '这是来源数据格式问题：应在源系统修订后以新的来源编号重新导入，原记录不覆盖。'
    else:
        status, summary = 'needs_evidence', '先补充资料'
        reason = finding.get('explanation', '') + ' 资料取得前无法确定处理分录，不编制猜测性的调整。'
        if rule == 'TRIAL_BALANCE':
            reason = (f"科目 {metrics.get('account')} 按期初加发生额计算为 {metrics.get('calculated')}，导入期末为 {metrics.get('reported')}。"
                      '应先核对试算表来源和期初余额，再定位遗漏或重复的分录；系统不按差额补平。')
            needs = needs or ['试算表来源核对', '期初余额来源']
    if not note:
        note = (f'小企业会计准则：前期差错按未来适用法处理（{SCAS_SOURCE}）。' if framework == 'SCAS' else
                f'企业会计准则：前期差错需判断重要性与追溯重述条件（{CAS_SOURCE}）。')
    return {'rule_id': rule, 'scenario': scenario_of(rule), 'status': status, 'summary': summary, 'reason': reason,
            'framework': framework, 'framework_note': note, 'lines': lines, 'line_basis': line_basis,
            'alternatives': alternatives, 'warnings': warnings, 'needs': needs,
            'evidence_kind': EVIDENCE_KIND.get(rule),
            'basis': {'rule_id': rule, 'record_ids': sorted(ids), 'evidence_ids': list(finding.get('evidence_ids', [])),
                      'metrics': metrics, 'finding_status': finding.get('status')}}


def investigation_context(run: dict | None, draft_status: str) -> dict | None:
    """Summarise the latest finished investigation without letting it alter the lines."""
    if run is None:
        return None
    result = run.get('result') if isinstance(run.get('result'), dict) else {}
    outcome = result.get('status', run.get('status'))
    warning = None
    if outcome in {'failed', 'budget_exhausted'}:
        warning = '最近一次调查未成功完成，草稿仅依据规则发现。'
    elif outcome == 'normal' and draft_status in {'proposed', 'needs_input', 'conditional'}:
        warning = '调查结论为正常差异，草稿可能并不需要；请先复核调查理由。'
    elif outcome in {'insufficient', 'unsupported'} and draft_status == 'proposed':
        warning = '调查认为资料不足或超出覆盖，草稿仅供测算，不能作为处理结论。'
    return {'run_id': run.get('id'), 'mode': run.get('mode'), 'status': outcome, 'warning': warning}


def draft_digest(draft: dict) -> str:
    material = {key: draft[key] for key in ('version', 'source_sha256', 'case_id', 'snapshot_id', 'status', 'lines')}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_draft(case_id: str, snapshot_id: str, finding: dict, records: list[dict], policy: dict,
                latest_run: dict | None = None) -> dict:
    body = propose(finding, records, policy)
    draft = {**treatment_identity(), 'case_id': case_id, 'snapshot_id': snapshot_id, **body,
             'investigation': investigation_context(latest_run, body['status']),
             'notice': '处理草稿只用于隔离模拟，不写入原始账簿；提交模拟前须逐行核对并补齐空白字段。'}
    draft['digest'] = draft_digest(draft)
    return draft


def compare_lines(draft: dict, submitted: list[dict]) -> dict:
    """Classify a person's changes to the draft: completed inputs versus edits."""
    proposed = draft.get('lines', [])
    basis = draft.get('line_basis', [])
    completed, edits = [], []
    for index in range(max(len(proposed), len(submitted))):
        if index >= len(proposed):
            edits.append({'line': index + 1, 'change': 'added'})
            continue
        if index >= len(submitted):
            edits.append({'line': index + 1, 'change': 'removed'})
            continue
        required = set(basis[index]['input_required']) if index < len(basis) else set()
        for field in ACCOUNTING_FIELDS:
            before, after = str(proposed[index].get(field, '') or ''), str(submitted[index].get(field, '') or '')
            if before == after:
                continue
            entry = {'line': index + 1, 'field': field, 'draft': before, 'submitted': after}
            (completed if field in required and before == '' else edits).append(entry)
    return {'edited': bool(edits), 'completed_inputs': completed, 'edits': edits}
