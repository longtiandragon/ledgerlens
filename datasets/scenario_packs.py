"""First-edition scenario packs: 3 packs x 4 conditions, hand-written synthetic books.

Each cell is a small, explicitly synthetic book plus the steps a person takes
through the product and the outcome expected at each step. The expectations
are written by hand from the rule definitions in docs/PRD.md before running
the product; scripts/validate_scenario_packs.py and the matching test only
compare. A cell that fails is reported as failed, never re-labelled.
"""
from __future__ import annotations

PERIOD = '2026-09'
ACCOUNTS = ['1002', '1122', '1405', '2202', '6001', '6602', '6603']


def _row(source: str, kind: str, **fields) -> dict:
    return {'source_id': source, 'kind': kind, 'business_date': f'{PERIOD}-20', 'period': PERIOD,
            'currency': 'CNY', 'description': '合成场景，仅用于验收', **fields}


def bank(source: str, amount: str, **fields) -> dict:
    direction = 'incoming' if not amount.startswith('-') else 'outgoing'
    return _row(source, 'bank', amount=amount, account='1002', direction=direction, **fields)


def voucher(name: str, debit_account: str, credit_account: str, amount: str, **fields) -> list[dict]:
    return [_row(f'{name}-1', 'journal', voucher_id=name, account=debit_account, debit=amount, credit='0.00', **fields),
            _row(f'{name}-2', 'journal', voucher_id=name, account=credit_account, debit='0.00', credit=amount, **fields)]


def line(source: str, voucher_id: str, account: str, debit: str, credit: str, **fields) -> dict:
    return _row(source, 'journal', voucher_id=voucher_id, account=account, debit=debit, credit=credit, **fields)


def document(source: str, kind: str, amount: str, **fields) -> dict:
    return _row(source, kind, amount=amount, **fields)


def balance(source: str, kind: str, account: str, amount: str) -> dict:
    return _row(source, kind, account=account, amount=amount)


SUPPLIER = {'counterparty': '合成供应商甲'}
CUSTOMER = {'counterparty': '合成客户丙'}

CELLS = [
    # ---------------------------------------------------------------- funds
    {'id': 'funds-normal', 'pack': 'funds', 'condition': 'normal',
     'title': '两笔付款合并入账，另有一笔退款：发生额与候选关联一致',
     'books': {'main': {'rows': [bank('B-F1-1', '-600.00', order_id='PO-F1', **SUPPLIER),
                                 bank('B-F1-2', '-400.00', order_id='PO-F1', **SUPPLIER),
                                 *voucher('JV-F1', '2202', '1002', '1000.00', order_id='PO-F1', **SUPPLIER),
                                 bank('B-F1-R', '50.00', order_id='PO-F1', **SUPPLIER),
                                 *voucher('JV-F1R', '1002', '2202', '50.00', order_id='PO-F1', **SUPPLIER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('BANK_CANDIDATES_ALIGNED', 'normal')]}),
               ('investigate', {'rule': 'BANK_CANDIDATES_ALIGNED', 'expect_fact': 'normal'}),
               ('draft', {'rule': 'BANK_CANDIDATES_ALIGNED', 'expect_status': 'not_applicable', 'expect_lines': 0}),
               ('review', {'rule': 'BANK_CANDIDATES_ALIGNED', 'decision': 'accepted', 'expect_open': []}),
               ('export', {'rule': 'BANK_CANDIDATES_ALIGNED'})]},
    {'id': 'funds-anomaly', 'pack': 'funds', 'condition': 'definite_anomaly',
     'title': '银行手续费未入账：差异定位到未匹配流水，补齐对方科目后一致',
     'books': {'main': {'rows': [bank('B-F2-PAY', '-1000.00', order_id='PO-F2', **SUPPLIER),
                                 bank('B-F2-FEE', '-15.00', order_id='PO-F2', reference='手续费', **SUPPLIER),
                                 *voucher('JV-F2', '2202', '1002', '1000.00', order_id='PO-F2', **SUPPLIER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('BANK_LEDGER_DIFFERENCE', 'supported'), ('BANK_UNMATCHED', 'insufficient')]}),
               ('investigate', {'rule': 'BANK_LEDGER_DIFFERENCE', 'expect_fact': 'supported'}),
               ('draft', {'rule': 'BANK_LEDGER_DIFFERENCE', 'expect_status': 'needs_input', 'expect_lines': 2,
                          'expect_required': [[], ['account']]}),
               ('simulate', {'rule': 'BANK_LEDGER_DIFFERENCE', 'fill': {1: {'account': '6603'}}, 'expect_target': 'resolved',
                             'expect_resolved': ['BANK_LEDGER_DIFFERENCE', 'BANK_UNMATCHED'], 'expect_normal': ['BANK_CANDIDATES_ALIGNED']}),
               ('review', {'rule': 'BANK_LEDGER_DIFFERENCE', 'decision': 'accepted', 'simulation': 'last', 'expect_open': []}),
               ('export', {'rule': 'BANK_LEDGER_DIFFERENCE', 'expect_completed_inputs': 1})]},
    {'id': 'funds-insufficient', 'pack': 'funds', 'condition': 'insufficient',
     'title': '只有银行流水、缺少本期银行存款分录：补证后重新检查并接续复核',
     'books': {'main': {'rows': [bank('B-F3-1', '2000.00', order_id='SO-F3', **CUSTOMER),
                                 bank('B-F3-2', '500.00', order_id='SO-F3', **CUSTOMER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('BANK_RECONCILIATION_INPUT', 'insufficient')]}),
               ('draft', {'rule': 'BANK_RECONCILIATION_INPUT', 'expect_status': 'needs_evidence', 'expect_lines': 0}),
               ('request', {'rule': 'BANK_RECONCILIATION_INPUT', 'name': 'cash', 'kind': 'journal', 'description': '本期银行存款分录'}),
               ('review', {'rule': 'BANK_RECONCILIATION_INPUT', 'decision': 'reserved',
                           'expect_open': ['evidence_request', 'treatment_pending']}),
               ('supplement', {'book': 'main', 'rows': [*voucher('JV-F3-1', '1002', '1122', '2000.00', order_id='SO-F3', **CUSTOMER),
                                                        *voucher('JV-F3-2', '1002', '1122', '500.00', order_id='SO-F3', **CUSTOMER)]}),
               ('lineage', {'rule': 'BANK_RECONCILIATION_INPUT', 'expect_state': 'not_rechecked'}),
               ('check', {'book': 'main', 'expect': [('BANK_CANDIDATES_ALIGNED', 'normal'), ('PRIOR_FINDING_CLEARED', 'normal')]}),
               ('lineage', {'rule': 'BANK_RECONCILIATION_INPUT', 'snapshot': 'first', 'expect_state': 'not_reproduced'}),
               ('fulfil', {'name': 'cash', 'expect_candidates': 4}),
               ('review', {'rule': 'PRIOR_FINDING_CLEARED', 'decision': 'accepted', 'expect_open': []}),
               ('export', {'rule': 'PRIOR_FINDING_CLEARED'})]},
    {'id': 'funds-conflict', 'pack': 'funds', 'condition': 'conflicting',
     'title': '两笔等额付款对应两笔等额分录：多种配对均成立，不自动核销',
     'books': {'main': {'rows': [bank('B-F4-A', '-300.00', order_id='PO-F4', **SUPPLIER),
                                 bank('B-F4-B', '-300.00', order_id='PO-F4', **SUPPLIER),
                                 *voucher('JV-F4-A', '2202', '1002', '300.00', order_id='PO-F4', **SUPPLIER),
                                 *voucher('JV-F4-B', '2202', '1002', '300.00', order_id='PO-F4', **SUPPLIER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('BANK_MATCH_AMBIGUOUS', 'insufficient')]}),
               ('investigate', {'rule': 'BANK_MATCH_AMBIGUOUS', 'expect_fact': 'insufficient'}),
               ('draft', {'rule': 'BANK_MATCH_AMBIGUOUS', 'expect_status': 'needs_evidence', 'expect_lines': 0}),
               ('review', {'rule': 'BANK_MATCH_AMBIGUOUS', 'decision': 'reserved', 'expect_open': ['treatment_pending']}),
               ('export', {'rule': 'BANK_MATCH_AMBIGUOUS'})]},
    # ------------------------------------------------------------- purchase
    {'id': 'purchase-normal', 'pack': 'purchase', 'condition': 'normal',
     'title': '分批收货、分拆发票、分次付款：不判为重复确认',
     'books': {'main': {'rows': [document('PO-P1', 'order', '1000.00', order_id='PO-P1', **SUPPLIER),
                                 document('R-P1-1', 'receipt', '600.00', order_id='PO-P1', **SUPPLIER),
                                 document('R-P1-2', 'receipt', '400.00', order_id='PO-P1', **SUPPLIER),
                                 document('INV-P1-A', 'invoice', '600.00', invoice_id='INV-P1-A', order_id='PO-P1', **SUPPLIER),
                                 document('INV-P1-B', 'invoice', '400.00', invoice_id='INV-P1-B', order_id='PO-P1', **SUPPLIER),
                                 *voucher('JV-P1-A', '1405', '2202', '600.00', invoice_id='INV-P1-A', **SUPPLIER),
                                 *voucher('JV-P1-B', '1405', '2202', '400.00', invoice_id='INV-P1-B', **SUPPLIER),
                                 bank('B-P1-1', '-600.00', order_id='PO-P1', **SUPPLIER),
                                 bank('B-P1-2', '-400.00', order_id='PO-P1', **SUPPLIER),
                                 *voucher('PJ-P1-1', '2202', '1002', '600.00', order_id='PO-P1', **SUPPLIER),
                                 *voucher('PJ-P1-2', '2202', '1002', '400.00', order_id='PO-P1', **SUPPLIER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('BANK_CANDIDATES_ALIGNED', 'normal')]}),
               ('absent', {'rules': ['DUPLICATE_INVOICE', 'INVOICE_OVERBOOKED', 'INVOICE_EVIDENCE', 'RECEIPT_GAP',
                                     'RECEIPT_EXCESS']})]},
    {'id': 'purchase-anomaly', 'pack': 'purchase', 'condition': 'definite_anomaly',
     'title': '同一发票两张凭证全额确认：冲回后入账的一张，原账不变',
     'books': {'main': {'rows': [document('PO-P2', 'order', '500.00', order_id='PO-P2', **SUPPLIER),
                                 document('R-P2', 'receipt', '500.00', order_id='PO-P2', **SUPPLIER),
                                 document('INV-P2', 'invoice', '500.00', invoice_id='INV-P2', **SUPPLIER),
                                 *voucher('JV-P2-A', '1405', '2202', '500.00', invoice_id='INV-P2', **SUPPLIER),
                                 *voucher('JV-P2-B', '1405', '2202', '500.00', invoice_id='INV-P2', business_date=f'{PERIOD}-22',
                                          **SUPPLIER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('DUPLICATE_INVOICE', 'supported')]}),
               ('investigate', {'rule': 'DUPLICATE_INVOICE', 'expect_fact': 'supported'}),
               ('draft', {'rule': 'DUPLICATE_INVOICE', 'expect_status': 'proposed', 'expect_lines': 2,
                          'expect_text': '冲回重复确认凭证 JV-P2-B'}),
               ('simulate', {'rule': 'DUPLICATE_INVOICE', 'expect_target': 'resolved', 'expect_resolved': ['DUPLICATE_INVOICE']}),
               ('review', {'rule': 'DUPLICATE_INVOICE', 'decision': 'accepted', 'simulation': 'last', 'expect_open': []}),
               ('export', {'rule': 'DUPLICATE_INVOICE', 'expect_basis': 'treatment_draft'})]},
    {'id': 'purchase-insufficient', 'pack': 'purchase', 'condition': 'insufficient',
     'title': '收货单未到：登记补证，迟到收货单导入后缺口不再出现并接续复核',
     'books': {'main': {'rows': [document('PO-P3', 'order', '800.00', order_id='PO-P3', **SUPPLIER),
                                 document('R-P3-1', 'receipt', '500.00', order_id='PO-P3', **SUPPLIER),
                                 document('INV-P3', 'invoice', '800.00', invoice_id='INV-P3', order_id='PO-P3', **SUPPLIER),
                                 *voucher('JV-P3', '1405', '2202', '800.00', invoice_id='INV-P3', **SUPPLIER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('RECEIPT_GAP', 'insufficient')]}),
               ('investigate', {'rule': 'RECEIPT_GAP', 'expect_fact': 'insufficient'}),
               ('draft', {'rule': 'RECEIPT_GAP', 'expect_status': 'needs_evidence', 'expect_lines': 0}),
               ('request', {'rule': 'RECEIPT_GAP', 'name': 'receipt', 'kind': 'receipt', 'reference': 'PO-P3',
                            'description': '订单剩余收货单'}),
               ('review', {'rule': 'RECEIPT_GAP', 'decision': 'reserved', 'expect_open': ['evidence_request', 'treatment_pending']}),
               ('supplement', {'book': 'main', 'rows': [document('R-P3-2', 'receipt', '300.00', order_id='PO-P3',
                                                                 business_date=f'{PERIOD}-25', **SUPPLIER)]}),
               ('check', {'book': 'main', 'expect': [('PRIOR_FINDING_CLEARED', 'normal')]}),
               ('lineage', {'rule': 'RECEIPT_GAP', 'snapshot': 'first', 'expect_state': 'not_reproduced'}),
               ('fulfil', {'name': 'receipt', 'expect_candidates': 1}),
               ('review', {'rule': 'PRIOR_FINDING_CLEARED', 'decision': 'accepted', 'expect_open': []}),
               ('export', {'rule': 'PRIOR_FINDING_CLEARED'})]},
    {'id': 'purchase-conflict', 'pack': 'purchase', 'condition': 'conflicting',
     'title': '同一发票号两份来源金额不同：不判重复，登记补证并保留意见',
     'books': {'main': {'rows': [document('INV-P4-SYS', 'invoice', '900.00', invoice_id='INV-P4', **SUPPLIER),
                                 document('INV-P4-SCAN', 'invoice', '950.00', invoice_id='INV-P4', **SUPPLIER),
                                 *voucher('JV-P4-A', '1405', '2202', '900.00', invoice_id='INV-P4', **SUPPLIER),
                                 *voucher('JV-P4-B', '6602', '2202', '50.00', invoice_id='INV-P4', **SUPPLIER)]}},
     'steps': [('check', {'book': 'main', 'expect': [('INVOICE_CONFLICT', 'insufficient')]}),
               ('absent', {'rules': ['DUPLICATE_INVOICE', 'INVOICE_OVERBOOKED']}),
               ('investigate', {'rule': 'INVOICE_CONFLICT', 'expect_fact': 'insufficient'}),
               ('draft', {'rule': 'INVOICE_CONFLICT', 'expect_status': 'needs_evidence', 'expect_lines': 0}),
               ('request', {'rule': 'INVOICE_CONFLICT', 'name': 'invoice', 'kind': 'invoice', 'reference': 'INV-P4',
                            'description': '原始发票核验'}),
               ('review', {'rule': 'INVOICE_CONFLICT', 'decision': 'reserved', 'expect_open': ['evidence_request', 'treatment_pending']}),
               ('export', {'rule': 'INVOICE_CONFLICT'})],
     'boundary': '冲突的两份来源都保留在只读原账中；当前版本没有作废来源记录的机制，冲突只能以补证记录和保留意见处理。'},
    # --------------------------------------------------------------- period
    {'id': 'period-normal', 'pack': 'period', 'condition': 'normal',
     'title': '期初加本期发生等于试算期末：不产生期间或余额发现',
     'books': {'main': {'rows': [balance('O-1405', 'opening', '1405', '1000.00'),
                                 balance('O-2202', 'opening', '2202', '-1000.00'),
                                 *voucher('JV-D1', '1405', '2202', '300.00', business_date=f'{PERIOD}-15'),
                                 balance('T-1405', 'trial_balance', '1405', '1300.00'),
                                 balance('T-2202', 'trial_balance', '2202', '-1300.00')]}},
     'steps': [('check', {'book': 'main', 'expect': []})]},
    {'id': 'period-anomaly', 'pack': 'period', 'condition': 'definite_anomaly',
     'title': '凭证漏记一行：凭证不平且试算不衔接，补录遗漏行后两项都消除',
     'books': {'main': {'rows': [balance('O-1405', 'opening', '1405', '0.00'), balance('O-2202', 'opening', '2202', '0.00'),
                                 line('JV-D2-1', 'JV-D2', '1405', '100.00', '0.00'),
                                 line('JV-D2-2', 'JV-D2', '2202', '0.00', '60.00'),
                                 balance('T-1405', 'trial_balance', '1405', '100.00'),
                                 balance('T-2202', 'trial_balance', '2202', '-100.00')]}},
     'steps': [('check', {'book': 'main', 'expect': [('TRIAL_BALANCE', 'supported'), ('VOUCHER_BALANCE', 'supported')]}),
               ('investigate', {'rule': 'VOUCHER_BALANCE', 'expect_fact': 'supported'}),
               ('draft', {'rule': 'TRIAL_BALANCE', 'expect_status': 'needs_evidence', 'expect_lines': 0}),
               ('draft', {'rule': 'VOUCHER_BALANCE', 'expect_status': 'needs_input', 'expect_lines': 1,
                          'expect_required': [['account']]}),
               ('simulate', {'rule': 'VOUCHER_BALANCE', 'fill': {0: {'account': '2202'}}, 'expect_target': 'resolved',
                             'expect_resolved': ['TRIAL_BALANCE', 'VOUCHER_BALANCE']}),
               ('review', {'rule': 'VOUCHER_BALANCE', 'decision': 'accepted', 'simulation': 'last', 'expect_open': []}),
               ('export', {'rule': 'VOUCHER_BALANCE', 'expect_completed_inputs': 1})]},
    {'id': 'period-insufficient', 'pack': 'period', 'condition': 'insufficient',
     'title': '试算表没有期初余额：不按零处理，补充期初后接续复核',
     'books': {'main': {'rows': [*voucher('JV-D3', '1405', '2202', '200.00'),
                                 balance('T-1405', 'trial_balance', '1405', '1200.00'),
                                 balance('T-2202', 'trial_balance', '2202', '-1200.00')]}},
     'steps': [('check', {'book': 'main', 'expect': [('OPENING_REQUIRED', 'insufficient'), ('OPENING_REQUIRED', 'insufficient')]}),
               ('draft', {'rule': 'OPENING_REQUIRED', 'expect_status': 'needs_evidence', 'expect_lines': 0}),
               ('request', {'rule': 'OPENING_REQUIRED', 'name': 'opening', 'kind': 'opening', 'description': '各科目期初余额'}),
               ('supplement', {'book': 'main', 'rows': [balance('O-1405', 'opening', '1405', '1000.00'),
                                                        balance('O-2202', 'opening', '2202', '-1000.00')]}),
               ('check', {'book': 'main', 'expect': [('PRIOR_FINDING_CLEARED', 'normal'), ('PRIOR_FINDING_CLEARED', 'normal')]}),
               ('absent', {'rules': ['TRIAL_BALANCE', 'OPENING_REQUIRED']}),
               ('fulfil', {'name': 'opening', 'expect_candidates': 2}),
               ('review', {'rule': 'PRIOR_FINDING_CLEARED', 'decision': 'accepted', 'expect_open': []})]},
    {'id': 'period-conflict', 'pack': 'period', 'condition': 'conflicting',
     'title': '业务日期与记账期间矛盾：同年迟到单据不调账，跨年差错按制度给出不同路径',
     'books': {'cas': {'framework': 'CAS', 'rows': [*voucher('JV-LATE', '6602', '2202', '120.00', business_date='2026-08-28'),
                                                    *voucher('JV-PRIOR', '6602', '2202', '80.00', business_date='2025-12-30')]},
               'scas': {'framework': 'SCAS', 'rows': voucher('JV-PRIOR', '6602', '2202', '80.00', business_date='2025-12-30')}},
     'steps': [('check', {'book': 'cas', 'expect': [('PERIOD_CUTOFF', 'insufficient')] * 4}),
               ('draft', {'rule': 'PERIOD_CUTOFF', 'where': {'business_month': '2026-08'}, 'expect_status': 'not_applicable',
                          'expect_text': '同一会计年度'}),
               ('draft', {'rule': 'PERIOD_CUTOFF', 'where': {'business_month': '2025-12'}, 'expect_status': 'needs_evidence',
                          'expect_text': 'POLICY_CAS_PRIOR_PERIOD_ERROR_MATERIALITY'}),
               ('review', {'rule': 'PERIOD_CUTOFF', 'where': {'business_month': '2025-12'}, 'decision': 'reserved',
                           'expect_open': ['treatment_pending']}),
               ('check', {'book': 'scas', 'expect': [('PERIOD_CUTOFF', 'insufficient')] * 2}),
               ('draft', {'rule': 'PERIOD_CUTOFF', 'where': {'business_month': '2025-12'}, 'expect_status': 'not_applicable',
                          'expect_text': '未来适用法'})]},
]
