"""Fresh balanced synthetic replication, NOT independent business evidence (Apache-2.0).

Each split contains every wording slot of every family. No random one-of-family
draw can accidentally select only keyword-bearing wording. Truth is authored by
the construction, never supplied by a production detector. v2-v4 remain intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from datasets.generate_v2 import CAUSES, CATEGORIES, SPLITS, build_case as construct

VERSION = 'root-cause-v5.0.0'
VARIANTS = 4
TOPOLOGIES = ([34, 66], [22, 31, 47], [11, 19, 28, 42])
TEXT = {
    'order_reduced': {
        'development': ['签收会议附件：余货取消供应，双方签章确认。', '供货调整函：后半批不再发出，按入库量结清。',
                        '订货变更附件：未交部分终止履行，以已收数量为最终数量。', '采购签认单：尾批从合同范围删除，双方履约至此结束。'],
        'validation': ['供销联络函：取消最后一批供货，已送部分照常结算。', '履约确认附件：尾货不再交付，双方认可实际签收数量。',
                       '订货补充条款：剩余数量调减至零，最终货款按已交数量计付。', '双方签认备忘：未送部分解除履行义务，本单数量按仓库签收数收口。'],
        'sealed_test': ['交付确认附件：双方取消未发部分，仅就已经送达部分结算。', '订单签认回函：后续部分不再供货，结算数以入库记录为准。',
                        '供货协议附件：尚未交付的尾量予以核销，双方只履行已交付部分。', '订货协商函：合同剩余数量经双方签字调减为零，以实收量完结本单。'],
    },
    'invoice_voided': {
        'development': ['开票更正回执：这份原票已作废，同额替换票另送。', '票据交接函：抬头有误，已重开更正票替换。',
                        '开具方签认：旧号码的票据撤销登记，以新号码同额票据替代。', '票务处理附件：先前票据被收回并注销，换出的同额票据即将送达。'],
        'validation': ['票据核对函：原件信息填错，原发票作废后换发正确票。', '开具方记录：旧票已失效，同额更正票据随后交接。',
                       '销方交接备忘：前票撤销登记，另一个号码的等额票据取而代之。', '开票核实附件：错填抬头的票据已注销，请以随后送达的替代票核销。'],
        'sealed_test': ['票据更替说明：前一份票已作废，号码更正后的票随后送来。', '供应方签认单：原票已重开，同额替代票作为结算凭据。',
                        '开票方书面答复：此前号码对应的票据已撤回注销，另号等额票据替代。', '票据更正附件：旧票登记已经撤销，现以新号码出具的同额票据接替。'],
    },
    'price_adjustment': {
        'development': ['结算修订函：本批折让部分货款，冲减票随后补送。', '质量协商附件：价格下调，供方补开差额冲销凭证。',
                        '结算签认：交付质量不符，双方同意少收一部分价款，抵减票据后补。', '质保协商单：应付货款核减若干，销方寄送相应金额为负的票据。'],
        'validation': ['价款签认附件：质量补偿采用折让，供方出具冲销票。', '结算核对单：按约给予优惠，差额凭证由销方补齐。',
                       '交期协商备忘：因迟延交付减收部分价款，供方另送抵减票据。', '质检结算回函：本批货款扣减一部分，销方按扣减额出具冲销凭证。'],
        'sealed_test': ['销方结算说明：按约折让货款，差额开票冲减。', '价款补充协议：双方确认单价下调，差额票据随后送达。',
                        '货款核对附件：瑕疵补偿从本批价款中扣除，销方开出抵减凭证。', '交货协商回执：供方同意返还部分本单货款，并补送金额为负的结算票据。'],
    },
    'benign': {
        'development': ['联系附件：本月对账资料由原联系人接收。', '签收备注：仓库已留存运输回单。',
                        '供应方提示：附件请按页码归档。', '交接说明：随货资料已装订成册。'],
        'validation': ['资料交接附件：本批单据复印件已送档案室。', '供方联系便笺：回函请注明本单编号。',
                       '仓库工作记录：送货车辆信息已登记。', '对账联系说明：本次联系地址与合同一致。'],
        'sealed_test': ['单据签收附件：送货回单电子副本已归档。', '供方联系确认：核对人员仍为原合同联系人。',
                        '仓库交接便笺：纸质附件按约定顺序装订。', '结算联系附件：收件地址和联系人均与前次一致。'],
    },
}


def build_case(cause: str, split: str, variant: int):
    if variant not in range(VARIANTS):
        raise ValueError('v5 requires exactly four wording slots')
    return construct(cause, split, variant, version=VERSION, namespace='rc5', text=TEXT,
                     topologies=TOPOLOGIES, unit_offset=73_619)


def write_dataset(destination: Path) -> dict:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Refusing to overwrite a nonempty dataset directory')
    for folder in ('observed', 'on_demand', 'truth'):
        (destination / folder).mkdir(parents=True, exist_ok=True)
    cases = []
    for cause in CAUSES:
        for split in SPLITS:
            for slot in range(VARIANTS):
                observed, withheld, truth = build_case(cause, split, slot)
                hashes = {}
                for folder, data in [('observed', observed), ('on_demand', withheld), ('truth', truth)]:
                    raw = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                    (destination / folder / f"{observed['case_id']}.json").write_bytes(raw)
                    hashes[folder] = hashlib.sha256(raw).hexdigest()
                cases.append({'case_id': observed['case_id'], 'family_id': observed['family_id'], 'split': split,
                              'framework': observed['policy']['framework'], 'wording_slot': slot,
                              'sha256': hashes['observed'], 'file_sha256': hashes})
    sources = [Path(__file__), Path(__file__).with_name('generate_v2.py'), Path(__file__).with_name('generate.py')]
    manifest = {'version': VERSION, 'synthetic': True, 'case_count': len(cases), 'categories': CATEGORIES,
                'selection': 'All four wording slots per family; primary sealed run has 36 cases, repeated twice.',
                'evidence_budget': {'requests_per_case': 1, 'request_fields': ['kind', 'reference']},
                'truth_access': 'Evaluator only, after every prediction is persisted.',
                'limitations': ['Same-author synthetic replication of v2 mechanisms, not independent business data.',
                                'Only nine scenario families; repeats and wording slots are not independent families.',
                                'Do not tune on v2, v3, v4 or v5 sealed results.'],
                'generator_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                'cases': cases}
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (destination / 'manifest.json').write_bytes(raw)
    return {'case_count': len(cases), 'version': VERSION, 'manifest_sha256': hashlib.sha256(raw).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(write_dataset(parser.parse_args().output)))
