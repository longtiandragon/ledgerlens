"""Bounded consistency checks against six archived MOF implementation Q&A.

This component consumes explicit, source-bound observations. It neither reads
the evaluator answer store nor infers business conditions from model output.
Its caller must build the context from authoritative snapshot membership; a
caller-supplied context is not an authentication or authorization boundary.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from pathlib import Path

from .policy_contract import PREDICATES, PolicyCheckContext, PolicyObservation

POLICY_VERSION = 'policy-consistency-v4'
_SOURCE_SHA256 = hashlib.sha256(b'\0'.join(
    name.encode() + b'\0' + Path(__file__).with_name(name).read_text(encoding='utf-8').encode()
    for name in ('policy_contract.py', 'policy_consistency.py')
)).hexdigest()
_SCOPE_NOTE = (
    '仅检查已归档财政部实施问答的有限处理路径；条件须由人确认且引用当前账套快照内的证据。'
    '引用存在不等于证据真实或专业判断正确。发布日期仅表示该参考资料可见时间，'
    '不代表法规生效日、完整准则覆盖或现行有效性认证。'

)

# Reviewed source identities are compiled metadata, not a runtime answer index.
_DEFINITIONS = (
    {
        'rule_id': 'POLICY_CAS_RESALE_INVENTORY', 'framework': 'CAS',
        'topic': 'resale_inventory', 'title': '日常购进待售商品的存货确认',
        'source_id': 'mof-qa-3861813', 'publication_date': '2023-01-03',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/chzzsswd/202301/t20230103_3861813.htm',
        'source_sha256': 'c2a124f48ff46df0807a46da04de6ceaba341a9401e0d12318ed7a76535d4837',
        'applicable_conditions': '日常活动中持有以备出售；经济利益很可能流入；成本可可靠计量。',
        'unsupported_conditions': ['非日常活动或非待售用途不由本规则分类'],
        'known_limits': ['不判定其他资产类型、初始成本、税额或存货跌价'],
        'treatment_captions': {'inventory': '存货（库存商品等科目）'},
    },
    {
        'rule_id': 'POLICY_CAS_ROUTINE_REPAIR', 'framework': 'CAS',
        'topic': 'routine_repair', 'title': '非资本化日常修理的受益对象归集',
        'source_id': 'mof-qa-3762834', 'publication_date': '2021-11-02',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/gdzczzsswd/202111/t20211102_3762834.htm',
        'source_sha256': 'f2e966d221ebfea80e01fb2063e2c9089d617a470d24b65ce321bb10656063fb',
        'applicable_conditions': '固定资产日常修理不满足资本化条件；受益对象已确认。',
        'unsupported_conditions': ['资本化后续支出或非日常修理另行判断'],
        'known_limits': ['不判定资本化条件本身', '其他受益对象需补证；不计算成本分配'],
        'treatment_captions': {
            'inventory_cost': '存货成本', 'management_expense': '管理费用',
            'selling_expense': '销售费用',
        },
    },
    {
        'rule_id': 'POLICY_CAS_PREVIOUS_EXPENSED_PROTOTYPE', 'framework': 'CAS',
        'topic': 'previous_expensed_prototype', 'title': '正确费用化研发样机的后续销售',
        'source_id': 'mof-qa-3962191', 'publication_date': '2025-04-17',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/wxzczzsswd/202504/t20250417_3962191.htm',
        'source_sha256': '58d1e5355011ec094d7d38a91eee3a06b850ee0b9caf7c6756d159c3492e25c1',
        'applicable_conditions': '以前费用化处理已确认正确，后续签订销售合同或者销售样机。',
        'unsupported_conditions': ['原费用化存在差错时须采用独立差错处理路径', '尚无后续销售事件'],
        'known_limits': ['不判定原研发支出的资本化条件', '不处理后续新增成本、收入或差错更正分录'],
        'treatment_captions': {'keep_prior_expense': '以前正确费用化的支出不因后续销售而冲回'},
    },
    {
        'rule_id': 'POLICY_CAS_VAT_CREDIT_REFUND', 'framework': 'CAS',
        'topic': 'vat_credit_refund', 'title': '企业准则留抵退税现金流列示',
        'source_id': 'mof-qa-3819699', 'publication_date': '2022-06-21',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/xjllbzzsswd/202206/t20220621_3819699.htm',
        'source_sha256': 'e0d6aa47ec3d50473a934fca90f9c93247ffd26ba7c0370b62c35b0e4d34fb6b',
        'applicable_conditions': '适用所述2022留抵退税政策；单笔收到或缴回并继续按规定抵扣的事件。',
        'unsupported_conditions': ['其他退款类型不适用', '收到与缴回混合时须拆分事件'],
        'known_limits': ['只检查现金流列示项目；不计算退税资格、税额或完整现金流量表'],
        'treatment_captions': {'tax_refunds_received': '收到的税费返还', 'taxes_paid': '支付的各项税费'},
    },
    {
        'rule_id': 'POLICY_SCAS_VAT_CREDIT_REFUND', 'framework': 'SCAS',
        'topic': 'vat_credit_refund', 'title': '小企业准则留抵退税现金流列示',
        'source_id': 'mof-qa-3819702', 'publication_date': '2022-06-21',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/xqykjzzsswd/202206/t20220621_3819702.htm',
        'source_sha256': 'b0c2f5f27ddf30cc86385962a727d0733adc23e9d668cf9f8224e533c46f02a2',
        'applicable_conditions': '适用所述2022留抵退税政策；单笔收到或缴回并继续按规定抵扣的事件。',
        'unsupported_conditions': ['其他退款类型不适用', '收到与缴回混合时须拆分事件'],
        'known_limits': ['不得沿用企业准则的收款列示项目', '只检查现金流列示项目；不计算退税资格或税额'],
        'treatment_captions': {
            'other_operating_cash_received': '收到其他与经营活动有关的现金', 'taxes_paid': '支付的税费',
        },
    },
    {
        'rule_id': 'POLICY_SCAS_PERSONAL_TAX_WITHHOLDING_FEE', 'framework': 'SCAS',
        'topic': 'personal_tax_withholding_fee', 'title': '小企业个税扣缴手续费处理',
        'source_id': 'mof-qa-3762844', 'publication_date': '2021-11-02',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/xqykjzzsswd/202111/t20211102_3762844.htm',
        'source_sha256': '45a248ca3f44cdb7cb9a3d3692b0a2e65162ea8a14171245bfa6cf3fa753709e',
        'applicable_conditions': '小企业作为个人所得税扣缴义务人，收到按个税法规定的扣缴手续费。',
        'unsupported_conditions': ['非个税扣缴手续费或非扣缴义务人不适用'],
        'known_limits': ['不计算税费或扩展其他税种的手续费', '只检查科目与利润表列示的组合处理'],
        'treatment_captions': {'other_income_and_operating_revenue': '其他收益科目；营业收入项目'},
    },
    {
        'rule_id': 'POLICY_CAS_SHORT_TERM_LEASE', 'framework': 'CAS',
        'topic': 'short_term_lease', 'title': '含购买选择权的租赁不属于短期租赁',
        'source_id': 'mof-qa-3691864', 'publication_date': '2021-04-25',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/zlzzsswd/202104/t20210425_3691864.htm',
        'source_sha256': '18b511890690de18d1a6913f9e4d084d233b857fef03d9f7c2ac140581a14cf5',
        'applicable_conditions': '承租人的租赁期不超过十二个月且租赁包含购买选择权。',
        'unsupported_conditions': ['不含购买选择权的租赁是否为短期租赁须先按全部事实合理确定租赁期，本规则不判断',
                                   '租赁期超过十二个月的租赁不在本问答情形内'],
        'known_limits': ['不确定租赁期本身（续租、终止选择权须人工评估）', '不计量使用权资产或租赁负债'],
        'treatment_captions': {'right_of_use_asset_and_lease_liability': '确认使用权资产和租赁负债（不适用短期租赁简化处理）',
                               'short_term_lease_simplified': '按短期租赁简化处理'},
    },
    {
        'rule_id': 'POLICY_CAS_LEASE_CASH_FLOW', 'framework': 'CAS',
        'topic': 'lease_cash_flow', 'title': '承租人租赁相关付款的现金流量列报',
        'source_id': 'mof-qa-3691861', 'publication_date': '2021-04-25',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/zlzzsswd/202104/t20210425_3691861.htm',
        'source_sha256': '7f8e969c11ec5d7d1dc5ac1062fc3d6960501f6c8fd7a4db154213856aef4b82',
        'applicable_conditions': '承租人单笔支付租赁负债本金和利息、预付租金、租赁保证金，或简化处理租赁、未纳入租赁负债的可变付款。',
        'unsupported_conditions': ['一笔付款混合多种性质时须拆分后分别检查', '非上述租赁相关付款不适用'],
        'known_limits': ['只检查现金流量表活动分类；不计算金额或编制完整现金流量表'],
        'treatment_captions': {'financing_cash_outflow': '筹资活动现金流出', 'operating_cash_outflow': '经营活动现金流出'},
    },
    {
        'rule_id': 'POLICY_CAS_TRANSPORT_FULFILLMENT_COST', 'framework': 'CAS',
        'topic': 'transport_fulfillment_cost', 'title': '履约运输成本的利润表列示',
        'source_id': 'mof-qa-3762833', 'publication_date': '2021-11-02',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/srzzsswd/202111/t20211102_3762833.htm',
        'source_sha256': 'f58df319bc3e06d38ecdbb923291291b8187941f40a45cf936485e9aa17b50df',
        'applicable_conditions': '控制权转移前为履行收入合同发生的运输活动，且不构成单项履约义务。',
        'unsupported_conditions': ['运输活动构成单项履约义务或发生在控制权转移之后，超出本问答情形。'],
        'known_limits': ['不计算摊销金额或判断履约义务本身'],
        'treatment_captions': {'operating_cost': '主营业务成本或其他业务成本，利润表营业成本项目', 'selling_expense': '销售费用'},
    },
    {
        'rule_id': 'POLICY_CAS_ADVANCE_RECEIPT_CONTRACT_LIABILITY', 'framework': 'CAS',
        'topic': 'advance_receipt', 'title': '转让商品前预收款的科目',
        'source_id': 'mof-qa-3634031', 'publication_date': '2020-12-11',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/srzzsswd/202012/t20201211_3634031.htm',
        'source_sha256': '9073c8a850fa5b87838900bce20eaad767f9a80b32b36c1b0a35de0ece195450',
        'applicable_conditions': '执行新收入准则；转让商品前已收客户对价；所检查金额不含增值税部分。',
        'unsupported_conditions': ['未执行新收入准则、并非转让前预收，或金额含增值税部分（增值税部分不确认为合同负债，须拆分）。'],
        'known_limits': ['不确定增值税部分的科目', '不判断收入确认时点'],
        'treatment_captions': {'contract_liability': '合同负债', 'advance_receipts': '预收账款', 'deferred_income': '递延收益'},
    },
    {
        'rule_id': 'POLICY_CAS_CONTRACT_ASSET_IMPAIRMENT', 'framework': 'CAS',
        'topic': 'contract_asset_impairment', 'title': '合同资产减值的科目',
        'source_id': 'mof-qa-3634035', 'publication_date': '2020-12-11',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/srzzsswd/202012/t20201211_3634035.htm',
        'source_sha256': 'd90ac598dc58a3874b4144d029a3ef112197e8eff3aae20bdbeb77232c5b16e5',
        'applicable_conditions': '合同资产发生减值或转回已计提的减值准备。',
        'unsupported_conditions': ['并非合同资产减值事项。'],
        'known_limits': ['不计算减值金额或预期信用损失'],
        'treatment_captions': {'asset_impairment_loss': '资产减值损失（贷记合同资产减值准备）', 'credit_impairment_loss': '信用减值损失'},
    },
    {
        'rule_id': 'POLICY_CAS_LESSEE_IMPROVEMENT', 'framework': 'CAS',
        'topic': 'lessee_improvement', 'title': '承租人租赁资产改良支出',
        'source_id': 'mof-qa-3691863', 'publication_date': '2021-04-25',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/zlzzsswd/202104/t20210425_3691863.htm',
        'source_sha256': '9572638352fb99c07d4508bf76d7b412f46c5503d19dd6c5568cd11e8b390bc7',
        'applicable_conditions': '承租人对租入资产发生的改良支出。',
        'unsupported_conditions': ['并非承租人租赁资产改良支出。'],
        'known_limits': ['改良导致的预计复原支出按租赁准则第十六条处理，本规则不判断'],
        'treatment_captions': {'long_term_prepaid_expense': '长期待摊费用', 'right_of_use_asset_cost': '计入使用权资产成本'},
    },
    {
        'rule_id': 'POLICY_CAS_FOREIGN_CURRENCY_ADVANCE', 'framework': 'CAS',
        'topic': 'foreign_currency_advance', 'title': '外币预收预付款的资产负债表日折算',
        'source_id': 'mof-qa-3664191', 'publication_date': '2021-03-02',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/wbzs/202103/t20210302_3664191.htm',
        'source_sha256': '923c6f69d9c2db834e515dad4f32694f7bc0db232e797785cdae55d6c99e53c5',
        'applicable_conditions': '外币预收账款或预付账款，以历史成本计量。',
        'unsupported_conditions': ['并非以历史成本计量的外币预收或预付款。'],
        'known_limits': ['不计算折算金额'],
        'treatment_captions': {'historical_rate_no_exchange_difference': '按交易日即期汇率折算，不产生汇兑损益', 'exchange_difference_recognized': '按资产负债表日汇率折算并确认汇兑损益'},
    },
    {
        'rule_id': 'POLICY_CAS_DEBT_RESTRUCTURING_INVENTORY', 'framework': 'CAS',
        'topic': 'debt_restructuring_inventory', 'title': '债务人以存货清偿债务',
        'source_id': 'mof-qa-3664186', 'publication_date': '2021-03-02',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/zwcz/202103/t20210302_3664186.htm',
        'source_sha256': 'df84f1457570800e25da3f2926277f3f03efd27cccb3f10be2d321d581f8a0a6',
        'applicable_conditions': '债务人以存货清偿债务进行债务重组，且该债务重组不属于日常活动。',
        'unsupported_conditions': ['并非以存货清偿债务，或该交易属于日常活动，超出本问答“通常情况下”的范围。'],
        'known_limits': ['不计算债务或存货账面价值'],
        'treatment_captions': {'other_income_not_revenue': '不作存货销售处理，差额记入其他收益', 'revenue_and_cost_of_sales': '确认收入并结转销售成本'},
    },
    {
        'rule_id': 'POLICY_CAS_CASH_DISCOUNT', 'framework': 'CAS',
        'topic': 'cash_discount', 'title': '销售商品给予客户的现金折扣',
        'source_id': 'mof-qa-3634029', 'publication_date': '2020-12-11',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/srzzsswd/202012/t20201211_3634029.htm',
        'source_sha256': 'e16496b3abbfb237fd32b0a052594124191472b4b7d9e4d9d41f1523a0083d92',
        'applicable_conditions': '执行新收入准则；销售商品时给予客户现金折扣。',
        'unsupported_conditions': ['未执行新收入准则或并非销售商品给予的现金折扣。'],
        'known_limits': ['不估计可变对价金额或限制条件'],
        'treatment_captions': {'variable_consideration': '按可变对价规定处理（影响交易价格）', 'finance_expense': '计入财务费用'},
    },
    {
        'rule_id': 'POLICY_CAS_GROUP_SHARE_PAYMENT', 'framework': 'CAS',
        'topic': 'group_share_payment', 'title': '无结算义务的集团内股份支付',
        'source_id': 'mof-qa-3664183', 'publication_date': '2021-03-02',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/gfzzsswd/202103/t20210302_3664183.htm',
        'source_sha256': 'ca3ac90a0901a83448e19d69c60171c5dd149711492993d4d6a298f641a85507',
        'applicable_conditions': '适用股份支付准则的集团内交易；本企业接受员工服务且无结算义务。',
        'unsupported_conditions': ['有结算义务、不是接受服务方或不适用集团内股份支付情形。'],
        'known_limits': ['不判断集团关系或结算义务，不计算授予日公允价值和费用'],
        'treatment_captions': {'equity_settled_share_payment': '作为权益结算股份支付处理',
                               'cash_settled_share_payment': '作为现金结算股份支付处理'},
    },
    {
        'rule_id': 'POLICY_CAS_DEBT_RELEASE_TIMING', 'framework': 'CAS',
        'topic': 'debt_release_timing', 'title': '现时义务未解除时不得提前确认重组损益',
        'source_id': 'mof-qa-3664190', 'publication_date': '2021-03-02',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/zwcz/202103/t20210302_3664190.htm',
        'source_sha256': '4e624f6e70c40f3adc6abfaea54ed2edd8f462fbfc96c23914629b8c8b9d312c',
        'applicable_conditions': '已签重组合同，但债务现时义务尚未解除。',
        'unsupported_conditions': ['义务已经解除或不属于该债务重组情形；其他终止确认路径须另行审查。'],
        'known_limits': ['义务是否解除需人工确认；不确定损益金额'],
        'treatment_captions': {'defer_debt_restructuring_gain': '不终止确认债务，不确认相关重组损益',
                               'recognize_debt_restructuring_gain': '签约时确认债务重组损益'},
    },
    {
        'rule_id': 'POLICY_CAS_INITIAL_REVENUE_ECL', 'framework': 'CAS',
        'topic': 'initial_revenue_ecl', 'title': '首次执行新收入准则的预期信用损失衔接',
        'source_id': 'mof-qa-3634036', 'publication_date': '2020-12-11',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/srzzsswd/202012/t20201211_3634036.htm',
        'source_sha256': 'dcfd17d651bc5da2e85f5e25f5317f86e6b91da55d0ef29a3500eec5c3cb0850',
        'applicable_conditions': '已执行2017年金融工具准则；首次执行新收入准则新产生应收账款或合同资产。',
        'unsupported_conditions': ['不是首次执行衔接产生的应收款或合同资产，或尚未执行2017年金融工具准则。'],
        'known_limits': ['不估计信用损失金额，不处理其他准则首次执行衔接'],
        'treatment_captions': {'opening_retained_earnings_no_comparative_restatement': '相应信用损失调整年初留存收益，不追溯调整可比信息',
                               'restate_comparative_ecl': '追溯调整可比期间信用损失'},
    },
    {
        'rule_id': 'POLICY_CAS_RENT_SUBSIDY', 'framework': 'CAS',
        'topic': 'rent_subsidy', 'title': '弥补物业租赁成本的政府补助分类',
        'source_id': 'mof-qa-3819697', 'publication_date': '2022-06-21',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/zfbzzzsswd/202206/t20220621_3819697.htm',
        'source_sha256': 'c51b0c59343f825a5ade462a1545de8428f2ee68dda5e8e802434359e97cbb7a',
        'applicable_conditions': '政府补助用于弥补物业租赁成本，不用于取得长期资产。',
        'unsupported_conditions': ['形成长期资产或不是所述租赁成本补偿的补助。'],
        'known_limits': ['不凭名称推断用途，不判定确认期间、金额或总额/净额法'],
        'treatment_captions': {'income_related_grant': '与收益相关的政府补助', 'asset_related_grant': '与资产相关的政府补助'},
    },
    {
        'rule_id': 'POLICY_CAS_VOLUNTARY_SHARE_CANCELLATION', 'framework': 'CAS',
        'topic': 'voluntary_share_cancellation', 'title': '等待期内自愿退出权益激励计划',
        'source_id': 'mof-qa-3896761', 'publication_date': '2023-07-17',
        'url': 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/gfzzsswd/202307/t20230717_3896761.htm',
        'source_sha256': 'b81fd1edccce57133eab29e8c9e47c4fb5b51237a4d5af7f5b5ac0dfbc512823',
        'applicable_conditions': '等待期内取消授予权益工具，职工自愿退出，不因未满足可行权条件而取消。',
        'unsupported_conditions': ['因未满足可行权条件取消、不是等待期内取消或不是所述自愿退出。'],
        'known_limits': ['不推断可行权条件性质，不计算剩余应确认金额'],
        'treatment_captions': {'accelerate_vesting_keep_prior_expense': '加速行权，剩余金额计入损益及资本公积，不冲回以前费用',
                               'reverse_share_payment_expense': '冲回以前确认的股份支付费用'},
    },
)


# topic -> (expected treatment, reason when a condition is confirmed "no")
SIMPLE_RULES = {
    'transport_fulfillment_cost': ('operating_cost', '运输活动构成单项履约义务或发生在控制权转移之后，超出本问答情形。'),
    'advance_receipt': ('contract_liability', '未执行新收入准则、并非转让前预收，或金额含增值税部分（增值税部分不确认为合同负债，须拆分）。'),
    'contract_asset_impairment': ('asset_impairment_loss', '并非合同资产减值事项。'),
    'lessee_improvement': ('long_term_prepaid_expense', '并非承租人租赁资产改良支出。'),
    'foreign_currency_advance': ('historical_rate_no_exchange_difference', '并非以历史成本计量的外币预收或预付款。'),
    'debt_restructuring_inventory': ('other_income_not_revenue', '并非以存货清偿债务，或该交易属于日常活动，超出本问答“通常情况下”的范围。'),
    'cash_discount': ('variable_consideration', '未执行新收入准则或并非销售商品给予的现金折扣。'),
    'group_share_payment': ('equity_settled_share_payment', '本企业有结算义务、不是接受服务方或非集团内股份支付。'),
    'debt_release_timing': ('defer_debt_restructuring_gain', '不属于已签合同但现时义务未解除的限定情形。'),
    'initial_revenue_ecl': ('opening_retained_earnings_no_comparative_restatement', '不属于已执行金融工具准则的首次新收入准则衔接情形。'),
    'rent_subsidy': ('income_related_grant', '不是弥补租赁成本且不形成长期资产的政府补助。'),
    'voluntary_share_cancellation': ('accelerate_vesting_keep_prior_expense', '不属于等待期内自愿取消且非未满足可行权条件的情形。'),
}


def policy_rule_identity() -> dict[str, str]:
    """Identity of the loaded contract and implementation, captured at import."""
    return {'version': POLICY_VERSION, 'source_sha256': _SOURCE_SHA256}


def _source(definition: dict) -> dict:
    return {key: definition[key] for key in ('source_id', 'url', 'publication_date', 'source_sha256')} | {
        'locator': 'div.TRS_Editor',
    }


def policy_rule_catalog() -> list[dict]:
    result = []
    for definition in _DEFINITIONS:
        required = set(PREDICATES[definition['topic']])
        if definition['topic'] == 'routine_repair':
            required.add('beneficiary')
        result.append({
            **deepcopy(definition), 'source': _source(definition),
            'required_conditions': sorted(required), 'version': POLICY_VERSION,
            'policy_rule_identity': policy_rule_identity(), 'validation_status': 'experimental',
            'status': 'experimental', 'reference_available_from': definition['publication_date'],
            'effective_from': None, 'effective_to': None, 'scope_note': _SCOPE_NOTE,
        })
    return result


def evaluate_policy(observation: PolicyObservation, context: PolicyCheckContext) -> dict:
    """Return a bounded policy conclusion; reject invalid scope instead of guessing."""
    # Pydantic's frozen models still contain mutable lists/dicts. Revalidation
    # also prevents model_construct or later container edits bypassing invariants.
    observation = PolicyObservation.model_validate(observation.model_dump())
    context = PolicyCheckContext.model_validate(context.model_dump())
    if (observation.book_id, observation.snapshot_id) != (context.book_id, context.snapshot_id):
        raise ValueError('Policy observation is outside the book or snapshot')
    references = set(observation.treatment_evidence_ids) | set(observation.beneficiary_evidence_ids)
    for condition in observation.conditions.values():
        references.update(condition.evidence_ids)
    if not references <= {item.evidence_id for item in context.evidence}:
        raise ValueError('Policy observation contains evidence outside the fixed snapshot')
    candidates = [item for item in _DEFINITIONS if item['topic'] == observation.topic]
    definition = next((item for item in candidates if item['framework'] == context.framework), candidates[0])
    result = {
        'rule_id': definition['rule_id'], 'title': definition['title'], 'status': 'insufficient',
        'expected_treatment': None, 'reported_treatment': observation.reported_treatment,
        'evidence_ids': sorted(references), 'missing_conditions': [], 'reason': '',
        'policy_rule_identity': policy_rule_identity(), 'source': _source(definition), 'scope_note': _SCOPE_NOTE,
    }

    def finish(status: str, reason: str, missing=(), expected=None) -> dict:
        return {**result, 'status': status, 'reason': reason,
                'missing_conditions': sorted(set(missing)), 'expected_treatment': expected}

    if definition['framework'] != context.framework:
        return finish('unsupported', '该主题尚无适用于当前会计制度的执行规则。')
    published = date.fromisoformat(definition['publication_date'])
    # MOF publication dates refer to the Chinese civil day, irrespective of the
    # offset used to encode the caller's knowledge cutoff.
    available_at = datetime.combine(published, time.min, tzinfo=timezone(timedelta(hours=8)))
    if context.period < definition['publication_date'][:7] or context.as_of < available_at:
        return finish('unsupported', '调查期间或获知时间早于归档参考资料的公开日期；不使用后来发布的问答。')

    values = {
        name: condition.value if condition.confirmation == 'human_confirmed' else 'unknown'
        for name, condition in observation.conditions.items()
    }
    missing = {name for name in PREDICATES[observation.topic] if values.get(name, 'unknown') == 'unknown'}
    expected = None
    if observation.topic == 'resale_inventory':
        if values.get('ordinary_course') == 'no' or values.get('held_for_sale') == 'no':
            return finish('unsupported', '非日常活动或非待售用途超出本规则；不能据此推定为固定资产。')
        if values.get('economic_benefits_probable') == 'no' or values.get('cost_reliably_measurable') == 'no':
            missing.update(name for name in ('economic_benefits_probable', 'cost_reliably_measurable')
                           if values.get(name) == 'no')
            return finish('insufficient', '当前条件不支持确认存货；需要进一步专业判断，不能自动费用化。', missing)
        if not missing:
            expected = 'inventory'
    elif observation.topic == 'routine_repair':
        if values.get('routine_repair') == 'no' or values.get('capitalization_criteria_met') == 'yes':
            return finish('unsupported', '非日常修理或满足资本化条件的支出不属于本规则。')
        beneficiary = observation.beneficiary if observation.beneficiary_confirmation == 'human_confirmed' else 'unknown'
        targets = {'inventory_production': 'inventory_cost', 'administration': 'management_expense',
                   'dedicated_sales': 'selling_expense'}
        if beneficiary not in targets:
            missing.add('beneficiary')
        if not missing:
            expected = targets[beneficiary]
    elif observation.topic == 'previous_expensed_prototype':
        if values.get('previous_expensing_was_correct') == 'no':
            return finish('unsupported', '原费用化有差错，应进入独立差错更正判断路径。')
        if values.get('later_sale_contract_or_sale') == 'no':
            return finish('unsupported', '尚无后续销售合同或销售事件，不适用本后续销售规则。')
        if not missing:
            expected = 'keep_prior_expense'
    elif observation.topic == 'vat_credit_refund':
        if values.get('vat_credit_refund_under_2022_policy') == 'no':
            return finish('unsupported', '未满足本归档问答对应的留抵退税政策适用条件。')
        if values.get('received') == 'yes' and values.get('repaid_and_credit_continues') == 'yes':
            return finish('unsupported', '收到与缴回属于不同方向，应拆分事件后分别检查。')
        if values.get('received') == 'no' and values.get('repaid_and_credit_continues') == 'no':
            return finish('insufficient', '尚无可确认的收到或缴回事件。', missing | {'received', 'repaid_and_credit_continues'})
        if not missing:
            expected = ('tax_refunds_received' if context.framework == 'CAS' else 'other_operating_cash_received') \
                if values['received'] == 'yes' else 'taxes_paid'
    elif observation.topic == 'short_term_lease':
        if values.get('lease_term_not_over_12_months') == 'no':
            return finish('unsupported', '租赁期超过十二个月，不属于本问答所述情形。')
        if values.get('contains_purchase_option') == 'no':
            return finish('unsupported', '不含购买选择权；是否为短期租赁须先合理确定租赁期，本规则不作判断。')
        if not missing:
            expected = 'right_of_use_asset_and_lease_liability'
    elif observation.topic == 'lease_cash_flow':
        simplified = values.get('simplified_short_term_or_low_value_lease')
        variable = values.get('variable_payment_outside_lease_liability')
        financing = [values.get('lease_liability_principal_or_interest'), values.get('prepaid_rent_or_lease_deposit')]
        if simplified == 'yes':
            # Simplified short-term/low-value lease payments, incl. their prepaid rent
            # and deposits, are operating outflows; other facts do not change this.
            missing.clear()
            expected = 'operating_cash_outflow'
        elif variable == 'yes' and 'yes' in financing:
            return finish('unsupported', '一笔付款同时包含可变付款与租赁负债或预付、保证金性质，应拆分后分别检查。')
        elif variable == 'yes':
            # Operating only once the simplified-lease path is ruled out.
            missing = {'simplified_short_term_or_low_value_lease'} if simplified != 'no' else set()
            if not missing:
                expected = 'operating_cash_outflow'
        elif 'yes' in financing:
            # Financing needs both operating paths explicitly ruled out.
            missing = {name for name, value in (('simplified_short_term_or_low_value_lease', simplified),
                                                ('variable_payment_outside_lease_liability', variable))
                       if value != 'no'}
            if not missing:
                expected = 'financing_cash_outflow'
        elif simplified == 'no' and variable == 'no' and financing == ['no', 'no']:
            return finish('unsupported', '并非本问答所列的租赁相关付款。')
    elif observation.topic in SIMPLE_RULES:
        # Every condition must be human-confirmed "yes"; any "no" leaves the Q&A scope.
        if any(values.get(name) == 'no' for name in PREDICATES[observation.topic]):
            return finish('unsupported', SIMPLE_RULES[observation.topic][1])
        if not missing:
            expected = SIMPLE_RULES[observation.topic][0]
    elif observation.topic == 'personal_tax_withholding_fee':
        if any(value == 'no' for value in values.values()):
            return finish('unsupported', '并非已确认的个人所得税扣缴义务人收到该法定扣缴手续费的情形。')
        if not missing:
            expected = 'other_income_and_operating_revenue'
    if missing:
        return finish('insufficient', '必要业务条件未获人工确认；模型建议和来源陈述不能替代确认。', missing)
    if observation.reported_treatment == 'unknown':
        return finish('insufficient', '条件已确认，但缺少当前账务处理的来源证据。', ['reported_treatment'], expected)
    if observation.reported_treatment == expected:
        return finish('normal', '当前来源绑定的处理与本归档问答的有限规则一致。', expected=expected)
    return finish('supported', '已确认条件下，当前处理与本归档问答的规定路径不一致。', expected=expected)
