"""Fresh root-cause data for the pre-registered hybrid comparison (Apache-2.0).

Same accounting construction, causes and evaluator definitions as v2/v3
(datasets.generate_v2.build_case), with new wording, receipt topologies,
amounts and identifiers. Written on 2026-10-20 after the deterministic-first
routing rule was fixed in the plan and before any v4 model request.

Wording rule, fixed before writing and checked by tests:
- every phrase is new (no v2 or v3 phrase is reused) and keeps the v2 meaning;
- in validation and sealed_test, exactly two of the four phrases per text cause
  contain a v2 keyword of that cause and two contain no v2 keyword at all, so
  the keyword baseline is neither favoured nor locked out;
- no phrase contains a keyword of another cause; benign notes contain none.

This is a synthetic replication by the same AI-assisted author, not independent
real-world validation. No production detector supplies the truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from datasets.generate_v2 import CAUSES, CATEGORIES, SPLITS, build_case as construct

VERSION = 'root-cause-v4.0.0'
VARIANTS = 10
TOPOLOGIES = ([45, 55], [10, 40, 50], [16, 24, 27, 33])
TEXT = {
    'order_reduced': {
        'development': ['供应商函件：剩余未发数量取消，按已到货部分结算。', '采购会议纪要：订单余量不再执行，双方已确认。',
                        '补充协议：订单数量调整为已到货数量。', '供方电话记录：最后一批不再生产，余单撤销。'],
        'validation': ['销售经理微信留言：尾批取消发运，账按实收结。', '采购变更单：未交部分双方同意不再供货。',
                       '合同履行确认：剩余份额经协商终止，以已交付量为准。', '供应商回函：余下批次停止履约，最终数量按入库数确定。'],
        'sealed_test': ['双方往来函：订单剩余部分予以取消。', '业务员备注：剩下的数量对方表示不再交付。',
                        '履约变更备忘：未到货部分双方合意撤回，结算以实收为限。', '供需协调记录：后续批量从订单中划除，本单至此收口。'],
    },
    'invoice_voided': {
        'development': ['开票方函告：原发票已作废，将另行重开。', '供方财务邮件：旧票失效，以新开发票为准。',
                        '票务说明：发票信息有误，供应商已重新开具。', '供应商备注：该票作废，替换发票稍后送达。'],
        'validation': ['供方会计电话：这张票号码错了，已作废并另开。', '开票更正单：原票失效，替换票据补寄。',
                       '供应商说明函：原开具凭据已经撤销，请改用换开后的票据。', '对方开票员留言：前一张票的税号填错，已在系统中冲销并换出新票。'],
        'sealed_test': ['开具方确认函：原发票作废处理，新票随后寄达。', '财务对接记录：首张票据已失效，供应商另出一张同额票据。',
                        '供方来电摘要：之前那张票抬头不对，已经收回，换号码出具了替代票。', '结算联系单：原票据由销售方撤销登记，以后补的同额凭证核销。'],
    },
    'price_adjustment': {
        'development': ['供应商函件：因质量问题给予折让，开具红字发票。', '价格协议：单价下调，差额开负数发票冲减。',
                        '供方通知：本批给予优惠，后续寄送红字票。', '质检协商记录：同意折让部分货款。'],
        'validation': ['供方销售邮件：本单单价下调，差额另开冲减票。', '协商纪要：同意给予折让，票据后补。',
                       '采购谈判记录：对方同意少收部分货款，并补送抵减凭证。', '供应商回执：按瑕疵比例退还部分价款，冲销票据另行交付。'],
        'sealed_test': ['供方确认函：本批价格予以优惠，差额开具相应票据冲回。', '结算附言：货款折让一部分，对方出具红字凭证。',
                        '业务洽谈纪要：因交付延迟，应付金额核减若干，销方补开金额为负的票据。', '质量处理单：双方确认扣减部分价款，扣减凭据随附件寄出。'],
    },
    'benign': {
        'development': ['供应商备注：发货单已随货交付。', '联系记录：对账联系人电话未变。',
                        '采购备注：送货时间为上午。', '供方来函：样品已另行寄送。'],
        'validation': ['往来备注：合同原件已归档。', '物流留言：车辆已于约定时间到达。',
                       '供应商说明：开户信息与上月一致。', '办公备注：请将回执交给仓库管理员。'],
        'sealed_test': ['供方邮件：下周起更换业务对接人，联系方式另告。', '仓库备注：入库单据已扫描存档。',
                        '业务联系函：本月对账照常进行。', '采购留言：请保留送货单复印件。'],
    },
}


def build_case(cause: str, split: str, variant: int):
    return construct(cause, split, variant, version=VERSION, namespace='rc4', text=TEXT,
                     topologies=TOPOLOGIES, unit_offset=58_331)


def write_dataset(destination: Path) -> dict:
    # Frozen datasets are append-only. Refuse even partially populated targets.
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Refusing to overwrite a nonempty dataset directory')
    for name in ('observed', 'on_demand', 'truth'):
        (destination / name).mkdir(parents=True, exist_ok=True)
    cases = []
    for cause in CAUSES:
        for split in SPLITS:
            for variant in range(VARIANTS):
                observed, withheld, truth = build_case(cause, split, variant)
                hashes = {}
                for folder, data in [('observed', observed), ('on_demand', withheld), ('truth', truth)]:
                    payload = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                    (destination / folder / f"{observed['case_id']}.json").write_bytes(payload)
                    hashes[folder] = hashlib.sha256(payload).hexdigest()
                cases.append({'case_id': observed['case_id'], 'family_id': observed['family_id'], 'split': split,
                              'framework': observed['policy']['framework'], 'sha256': hashes['observed'],
                              'file_sha256': hashes})
    sources = [Path(__file__), Path(__file__).with_name('generate_v2.py'), Path(__file__).with_name('generate.py')]
    manifest = {'version': VERSION, 'synthetic': True, 'case_count': len(cases), 'categories': CATEGORIES,
                'evidence_budget': {'requests_per_case': 1, 'request_fields': ['kind', 'reference']},
                'truth_access': 'Evaluator only, after all predictions.',
                'limitations': ['Same synthetic fault mechanisms as v2/v3, written by the same AI-assisted author '
                                'after the v3 results; not real-world independent validation.',
                                'Sealed split must not be used to tune prompts, rules, routing or keywords.'],
                'generator_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                'cases': cases}
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (destination / 'manifest.json').write_bytes(payload)
    return {'case_count': len(cases), 'version': VERSION, 'manifest_sha256': hashlib.sha256(payload).hexdigest(),
            'destination': str(destination.resolve())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.local/datasets-v4'))
    print(json.dumps(write_dataset(parser.parse_args().output), ensure_ascii=False))
