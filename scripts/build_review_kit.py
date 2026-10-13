"""Build a printable professional-review kit and an Excel-friendly answer sheet.

The kit asks a qualified accountant to judge what only a professional can: whether
each encoded policy rule follows its official MOF answer, whether the author's
root-cause truth conventions are sound, and which simulation checks are missing.
It contains no real enterprise data and no model output. Labels come from the
same sources the product uses; nothing here changes a rule or an expected answer.
"""
import argparse
import csv
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT))

from app.policy_consistency import policy_rule_catalog  # noqa: E402
from benchmarks.official_policy_cases import SPECS  # noqa: E402
from datasets.generate_v2 import CATEGORIES, CAUSES  # noqa: E402

VERSION = 'professional-review-kit-v1'
STATUS = {'normal': '处理一致', 'supported': '存在有依据的差异', 'insufficient': '证据不足', 'unsupported': '超出支持范围'}
CONCLUSION = {'supported': '证据支持（存在应处理的差异）', 'normal': '正常差异', 'insufficient': '证据不足'}
KINDS = {'invoice': '发票', 'receipt': '收货单', 'order': '订单变更', 'bank': '银行流水', 'journal': '分录'}
CAUSE_NAMES = {'dup_true': '真实重复入账', 'dup_second_delivery': '第二次交货错挂同一发票号',
               'gap_late_document': '期内收货、单据期后取得', 'gap_in_transit': '货物在途（期后收货）',
               'gap_order_reduced': '订单数量减少', 'gap_undeterminable': '收货缺口无法判定',
               'normal_benign_text': '正常业务（无害备注）', 'invoice_voided_text': '发票作废重开',
               'price_adjustment_text': '约定折让需冲减'}
SIMULATION_CHECKS = [
    ('S1', '调整草稿借贷是否平衡（逐笔与合计）'), ('S2', '是否写入已锁定期间'), ('S3', '科目是否属于账套科目表'),
    ('S4', '草稿声明的科目影响与程序复算的余额变化是否逐分一致'),
    ('S5', '已知缺口：合法科目之间“业务用途错误”的平衡调整（例如应冲减存货却冲减费用）目前仍会通过'),
]
ANSWERS = '一致 / 部分一致 / 不一致 / 无法判断'


def ui_labels(name: str) -> dict:
    text = (ROOT / 'frontend/src/components/PolicyChecks.vue').read_text(encoding='utf-8')
    block = re.search(r'const ' + name + r'[^{]*\{(.*?)\n\}', text, re.S).group(1)
    return dict(re.findall(r"([a-z_0-9]+): '([^']*)'", block))


def rows():
    conditions, treatments = ui_labels('conditionLabels'), ui_labels('treatmentLabels')
    items = []
    for number, rule in enumerate(policy_rule_catalog(), 1):
        examples = [spec for spec in SPECS if spec[3] == rule['topic'] and spec[2] == rule['framework']][:3]
        items.append({
            'id': f'P{number:02d}', 'section': '政策规则', 'title': f"{rule['title']}（{rule['framework']}）",
            'rule_id': rule['rule_id'], 'source': rule['source']['url'], 'date': rule['publication_date'],
            'applies': rule['applicable_conditions'], 'outside': '；'.join(rule['unsupported_conditions']) or '—',
            'limits': '；'.join(rule['known_limits']),
            'conditions_label': '须人工确认的条件', 'conditions': [conditions.get(name, name) for name in rule['required_conditions']],
            'treatments': rule['treatment_captions'],
            'examples': [f"{spec[0]}：当前处理“{treatments.get(spec[4], spec[4])}”→ {STATUS[spec[5]]}"
                         + (f"（应为“{treatments.get(spec[6], spec[6])}”）" if spec[6] and spec[5] == 'supported' else '')
                         for spec in examples],
            'question': '规则的条件和处理路径是否与官方答复一致？是否遗漏影响结论的条件？'})
    for number, (cause, (signal, categories, without, with_, decisive)) in enumerate(CAUSES.items(), 1):
        items.append({
            'id': f'R{number:02d}', 'section': '原因调查真值口径', 'title': CAUSE_NAMES[cause],
            'applies': '类别：' + ('、'.join(CATEGORIES[c] for c in categories) or '无（无法判定）'),
            'conditions_label': '结论口径', 'conditions': [f"未取得决定性资料时结论：{CONCLUSION[without]}",
                           f"取得{('、'.join(KINDS[k] for k in decisive) or '（无需补证）')}后结论：{CONCLUSION[with_]}"],
            'question': '该原因的类别与结论口径在会计上是否合理？取得资料后的结论是否应不同？'})
    for code, text in SIMULATION_CHECKS:
        items.append({'id': code, 'section': '模拟复核规则', 'title': text,
                      'question': '该检查是否足够？复核人员通常还会检查什么？'})
    return items


def write_html(items, path: Path):
    e = html.escape
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" '
             'content="width=device-width,initial-scale=1"><title>会计专业复核材料</title><style>'
             'body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;max-width:900px;margin:24px auto;padding:0 16px;'
             'color:#253c35;line-height:1.7}h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #ccd'
             '}section{border:1px solid #dde3d8;border-radius:6px;padding:10px 14px;margin:12px 0;break-inside:avoid}'
             'small{color:#6b7a70}.answer{border-top:1px dashed #b8c2b0;margin-top:8px;padding-top:6px}</style></head><body>',
             '<h1>账证智核 · 会计专业复核材料</h1>',
             '<p>这是一个学生的开源研究项目，用合成数据和财政部公开的实施问答检验会计复核辅助工具，不含任何企业真实数据。'
             '恳请您用约60–90分钟，就下列项目给出专业判断：在答卷（复核答卷.csv）的“复核结论”栏填写'
             f'“{ANSWERS}”之一，并在“意见”栏写明理由或遗漏之处。您的意见只用于改进规则和如实记录专业复核结果；'
             '是否署名由您决定。</p>']
    section = None
    for item in items:
        if item['section'] != section:
            section = item['section']
            parts.append(f'<h2>{e(section)}</h2>')
        parts.append(f"<section><strong>{e(item['id'])} · {e(item['title'])}</strong>")
        if item.get('source'):
            parts.append(f"<br><small>依据：<a href=\"{e(item['source'])}\">{e(item['source'])}</a>（发布 {e(item['date'])}）"
                         f" · {e(item['rule_id'])}</small>")
        if item.get('applies'):
            parts.append(f"<p>{e(item['applies'])}</p>")
        if item.get('conditions'):
            parts.append(f"<p>{e(item['conditions_label'])}：" + '；'.join(e(c) for c in item['conditions']) + '</p>')
        if item.get('treatments'):
            parts.append('<p>处理路径：' + '；'.join(e(v) for v in item['treatments'].values()) + '</p>')
        if item.get('outside'):
            parts.append(f"<p><small>不适用：{e(item['outside'])}；限制：{e(item['limits'])}</small></p>")
        if item.get('examples'):
            parts.append('<p><small>示例：' + '；'.join(e(x) for x in item['examples']) + '</small></p>')
        parts.append(f"<div class=\"answer\">请判断：{e(item['question'])}<br>复核结论：{ANSWERS}　　意见：</div></section>")
    parts.append('</body></html>')
    path.write_text(''.join(parts), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/review-kit')
    options = parser.parse_args()
    items = rows()
    options.output.mkdir(parents=True, exist_ok=True)
    write_html(items, options.output / 'review-kit.html')
    with (options.output / '复核答卷.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['编号', '部分', '项目', '复核结论（一致/部分一致/不一致/无法判断）', '意见', '复核人（可匿名）', '资格或职务', '日期'])
        for item in items:
            writer.writerow([item['id'], item['section'], item['title'], '', '', '', '', ''])
    summary = {'schema_version': VERSION, 'items': len(items),
               'sections': {name: sum(1 for i in items if i['section'] == name)
                            for name in dict.fromkeys(i['section'] for i in items)}}
    (options.output / 'kit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
