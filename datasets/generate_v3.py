"""Fresh root-cause data for narrow-contract evaluation (Apache-2.0).

New split wording, receipt topologies, amounts and identifiers. Reuses the
published accounting construction and evaluator definitions, not old records.
This is a new synthetic replication from the same author, not independent
real-world validation. No production detector supplies the truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from datasets.generate_v2 import CAUSES, CATEGORIES, SPLITS, build_case as construct

VERSION = 'root-cause-v3.0.0'
VARIANTS = 10
TOPOLOGIES = ([35, 65], [15, 25, 60], [12, 18, 28, 42])
TEXT = {
    'order_reduced': {
        'development': ['双方签字确认：订单余量取消，仅结已交数量。', '采购补充函：订单数量调整为本次实收数量。',
                        '供货协议补充页：尚未交付的份额不再执行。', '采购负责人备注：余下货物双方同意不再供应。'],
        'validation': ['采销双方已谈妥以入库部分作为整单终点。', '合同备忘录：后段供货义务经双方协商免除。',
                       '采购跟进记录：该批尾数从供货清单中划去，双方已签认。', '确认函：待交部分不再计入最终采购范围。'],
        'sealed_test': ['履约补充纪要：未送部分双方已合意解约。', '业务联络单：本单以已交批次收尾，后续数量归零。',
                        '供需双方确认书：原订而未交的份额解除交付责任。', '签章附页：最终订货量以目前累计实收量为限。'],
    },
    'invoice_voided': {
        'development': ['开票方确认：原发票已作废，以重开单据结算。', '供方财务函：原票信息错误，已重开有效票。',
                        '票据交接说明：此前发票失效，新票稍后交付。', '开票更正通知：旧票作废，请勿继续使用。'],
        'validation': ['供方办税员确认原号码已撤销，新号码另行寄送。', '财务交接簿：原开票凭据已由供方收回并换发。',
                       '供应商回执：原开票记录已注销，后续凭有效替代件核销。', '税票处理单：前次出具的票面有误，已另行换发。'],
        'sealed_test': ['开具方盖章说明：旧号码对应凭据现已无效，新的有效凭据待交。', '结算附件：首份发票已被开具方撤回，改用第二份。',
                        '票据流转单：原件已撤销，另号同额凭据接替原件。', '往来函：前次票面抬头有误，开票方已换号出具。'],
    },
    'price_adjustment': {
        'development': ['质量协商函：给予折让，以红字发票结清差额。', '双方签署降价协议：差额开负数发票。',
                        '供方确认：价格下调，稍后寄送红字发票。', '价款确认函：同意折让，需按新价格结算。'],
        'validation': ['结算纪要：货款扣掉双方约定的一部分，开票方补具冲减单。', '供方书面承诺：就本批瑕疵退还部分价款，票据后补。',
                       '采购签认：最终应付额低于原约定，差额单据另送。', '履约补偿协议：减少本次货款并另行出具抵减票。'],
        'sealed_test': ['双方签章：原结算额需减去质量补偿款，减额凭据随后提供。', '供方通知：此单少收已商定的金额，另票抵回。',
                        '结算备忘录：货款按协商结果让利，另行交付扣减票面。', '采购确认书：应付价款核减一部分，销方补送相应抵销票。'],
    },
    'benign': {
        'development': ['联络备注：请在工作时间接洽。', '交接说明：附件请交财务留存。',
                        '供应商来函：本次业务联系人未变。', '采购备注：包装箱已归档登记。'],
        'validation': ['交付备注：送货车辆牌号已登记。', '往来函件：复印件按部门分类保管。',
                       '办事记录：本单联系人下午在岗。', '附言：随货说明书已交使用部门。'],
        'sealed_test': ['联络函：对账材料请发送原收件地址。', '业务附言：来访登记已办结。',
                        '装箱备注：箱外标识已核对完毕。', '往来备忘录：下次联络仍使用现有邮箱。'],
    },
}


def build_case(cause: str, split: str, variant: int):
    return construct(cause, split, variant, version=VERSION, namespace='rc3', text=TEXT,
                     topologies=TOPOLOGIES, unit_offset=27_419)


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
                'limitations': ['Same synthetic fault mechanisms and author as v2; not real-world independent validation.',
                                'Sealed split must not be used to tune prompts, rules or keywords.'],
                'generator_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                'cases': cases}
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (destination / 'manifest.json').write_bytes(payload)
    return {'case_count': len(cases), 'version': VERSION, 'manifest_sha256': hashlib.sha256(payload).hexdigest(),
            'destination': str(destination.resolve())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.local/datasets-v3'))
    print(json.dumps(write_dataset(parser.parse_args().output), ensure_ascii=False))
