"""Run the first-edition scenario packs through the product API (Apache-2.0).

Every cell in datasets/scenario_packs.py is executed step by step against a
FastAPI client that the caller has already isolated (temporary data directory
and database, no model key). Each step records what was expected and what the
API returned; a cell passes only when every step passes. Nothing here changes
an expectation to fit an observation.
"""
from __future__ import annotations

import json
from collections import Counter

from datasets.scenario_packs import ACCOUNTS, CELLS, PERIOD

RUNNER_VERSION = 'scenario-packs-v1'
EXPORT_SECTIONS = ('## 接续关系', '## 补证事项', '## 处理草稿与模拟', '## 未决事项（当前）', '## 复核与审批')


class StepFailure(AssertionError):
    pass


def _check(condition: bool, message: str):
    if not condition:
        raise StepFailure(message)


def _ok(response, expected: int = 200):
    if response.status_code != expected:
        raise StepFailure(f'HTTP {response.status_code}: {response.text[:300]}')
    return response.json()


class CellRun:
    def __init__(self, client, cell: dict):
        self.client, self.cell = client, cell
        self.books: dict[str, str] = {}
        self.checks: dict[str, list[list[dict]]] = {}   # book -> list of check results (cases), oldest first
        self.current_book: str | None = None
        self.requests: dict[str, str] = {}
        self.simulations: dict[str, dict] = {}

    # -- setup ------------------------------------------------------------
    def book(self, key: str) -> str:
        if key not in self.books:
            spec = self.cell['books'][key]
            created = _ok(self.client.post('/api/v1/books', json={
                'name': f"场景验收 · {self.cell['id']} · {key}", 'framework': spec.get('framework', 'CAS'),
                'period': PERIOD, 'accounts': ACCOUNTS}), 201)
            self.books[key] = created['id']
            self.import_rows(key, spec['rows'])
        return self.books[key]

    def import_rows(self, key: str, rows: list[dict]):
        preview = _ok(self.client.post('/api/v1/imports', data={'book_id': self.books[key], 'synthetic': 'true'},
                                       files={'file': (f"{self.cell['id']}-{key}.json",
                                                       json.dumps(rows, ensure_ascii=False).encode(), 'application/json')}))
        _check(not preview['errors'], f"导入预览有错误：{preview['errors'][:3]}")
        _ok(self.client.post(f"/api/v1/imports/{preview['id']}/confirm"))

    # -- case lookup ------------------------------------------------------
    def case(self, rule: str, where: dict | None = None, snapshot: str = 'latest') -> dict:
        history = self.checks.get(self.current_book, [])
        _check(bool(history), '尚未执行检查')
        cases = history[0] if snapshot == 'first' else history[-1]
        found = [c for c in cases if c['rule_id'] == rule
                 and all(str(c.get('metrics', {}).get(k)) == str(v) for k, v in (where or {}).items())]
        _check(bool(found), f'找不到案件 {rule} {where or ""}')
        return found[0]

    # -- steps ------------------------------------------------------------
    def step_check(self, book: str, expect: list):
        self.current_book = book
        book_id = self.book(book)
        snapshot = _ok(self.client.post('/api/v1/snapshots', json={'book_id': book_id}))
        cases = _ok(self.client.post('/api/v1/check-runs', json={'book_id': book_id, 'snapshot_id': snapshot['id']}))['cases']
        self.checks.setdefault(book, []).append(cases)
        observed = sorted((c['rule_id'], c['status']) for c in cases)
        _check(observed == sorted(tuple(e) for e in expect), f'案件 {observed} != 预期 {sorted(tuple(e) for e in expect)}')
        return {'cases': observed, 'snapshot_id': snapshot['id']}

    def step_absent(self, rules: list):
        present = sorted({c['rule_id'] for c in self.checks[self.current_book][-1]} & set(rules))
        _check(not present, f'不应出现 {present}')
        return {'absent': rules}

    def step_investigate(self, rule: str, expect_fact: str, where: dict | None = None):
        case = self.case(rule, where)
        run = _ok(self.client.post(f"/api/v1/cases/{case['id']}/investigate", json={'mode': 'rules'}))
        finished = _ok(self.client.get(f"/api/v1/runs/{run['id']}"))
        _check(finished['status'] == 'complete', f"调查状态 {finished['status']}")
        fact = finished['result'].get('fact_status')
        _check(fact == expect_fact, f'调查事实状态 {fact} != {expect_fact}')
        _check(finished['usage'].get('calls', 0) == 0, '规则调查不应调用模型')
        return {'run_status': finished['status'], 'fact_status': fact}

    def step_draft(self, rule: str, expect_status: str, where: dict | None = None, expect_lines: int | None = None,
                   expect_required: list | None = None, expect_text: str | None = None):
        case = self.case(rule, where)
        draft = _ok(self.client.get(f"/api/v1/cases/{case['id']}/treatment-draft"))
        _check(draft['status'] == expect_status, f"草稿状态 {draft['status']} != {expect_status}")
        if expect_lines is not None:
            _check(len(draft['lines']) == expect_lines, f"草稿行数 {len(draft['lines'])} != {expect_lines}")
        if expect_required is not None:
            observed = [b['input_required'] for b in draft['line_basis']]
            _check(observed == expect_required, f'必填字段 {observed} != {expect_required}')
        if expect_text is not None:
            text = draft['summary'] + draft['reason'] + draft['framework_note']
            _check(expect_text in text, f'草稿说明缺少“{expect_text}”')
        return {'status': draft['status'], 'summary': draft['summary'], 'lines': len(draft['lines'])}

    def step_simulate(self, rule: str, expect_target: str, where: dict | None = None, fill: dict | None = None,
                      expect_resolved: list | None = None, expect_normal: list | None = None):
        case = self.case(rule, where)
        draft = _ok(self.client.get(f"/api/v1/cases/{case['id']}/treatment-draft"))
        lines = [dict(line) for line in draft['lines']]
        for index, fields in (fill or {}).items():
            lines[int(index)].update(fields)
        before = _ok(self.client.get(f"/api/v1/books/{self.books[self.current_book]}/records"))
        sim = _ok(self.client.post('/api/v1/simulations', json={
            'case_id': case['id'], 'adjustments': lines,
            'basis': {'source': 'treatment_draft', 'draft_digest': draft['digest']}}))
        after = _ok(self.client.get(f"/api/v1/books/{self.books[self.current_book]}/records"))
        _check(before == after, '模拟改变了原始记录')
        _check(sim['target']['status'] == expect_target, f"本案发现 {sim['target']['status']} != {expect_target}")
        _check(sim['comparison']['introduced'] == [], f"新引入问题 {[f['rule_id'] for f in sim['comparison']['introduced']]}")
        resolved = sorted(f['rule_id'] for f in sim['comparison']['resolved'])
        if expect_resolved is not None:
            _check(set(expect_resolved) <= set(resolved), f'已消除 {resolved} 未包含 {expect_resolved}')
        if expect_normal is not None:
            normal = sorted(f['rule_id'] for f in sim['comparison']['normal'])
            _check(normal == sorted(expect_normal), f'正常观察 {normal} != {expect_normal}')
        _check(sim['balanced'] and not sim['violations'], f"约束 {sim['violations']}")
        self.simulations['last'] = sim
        return {'target': sim['target']['status'], 'resolved': resolved, 'basis_source': sim['basis']['source'],
                'completed_inputs': len(sim['basis']['completed_inputs']), 'edited': sim['basis']['edited']}

    def step_request(self, rule: str, name: str, kind: str, description: str, reference: str | None = None,
                     where: dict | None = None):
        case = self.case(rule, where)
        created = _ok(self.client.post(f"/api/v1/cases/{case['id']}/evidence-requests",
                                       json={'kind': kind, 'reference': reference, 'description': description}), 201)
        self.requests[name] = created['id']
        return {'request': kind, 'status': created['status']}

    def step_supplement(self, book: str, rows: list):
        self.book(book)
        self.import_rows(book, rows)
        return {'imported': len(rows)}

    def step_lineage(self, rule: str, expect_state: str, snapshot: str = 'latest', where: dict | None = None):
        case = self.case(rule, where, snapshot)
        state = _ok(self.client.get(f"/api/v1/cases/{case['id']}"))['lineage']['state']
        _check(state == expect_state, f'接续状态 {state} != {expect_state}')
        return {'state': state}

    def step_fulfil(self, name: str, expect_candidates: int):
        requests = [r for book_cases in self.checks[self.current_book] for c in book_cases
                    for r in _ok(self.client.get(f"/api/v1/cases/{c['id']}/evidence-requests"))['requests']
                    if r['id'] == self.requests[name]]
        _check(bool(requests), '补证事项不存在')
        state = requests[0]
        _check(len(state['candidates']) == expect_candidates, f"候选 {len(state['candidates'])} != {expect_candidates}")
        done = _ok(self.client.post(f"/api/v1/evidence-requests/{state['id']}/fulfil", json={
            'snapshot_id': state['candidate_snapshot_id'], 'record_ids': [c['record_id'] for c in state['candidates']],
            'comment': '验收：人工确认候选资料'}))
        _check(done['status'] == 'fulfilled', f"补证状态 {done['status']}")
        return {'fulfilled': len(state['candidates'])}

    def step_review(self, rule: str, decision: str, expect_open: list, simulation: str | None = None,
                    where: dict | None = None):
        case = self.case(rule, where)
        chosen = self.simulations[simulation]['id'] if simulation else None
        row = _ok(self.client.post('/api/v1/reviews', json={
            'case_id': case['id'], 'expected_snapshot_id': case['snapshot_id'], 'decision': decision,
            'comment': '场景验收复核', 'simulation_id': chosen}))
        kinds = sorted(item['kind'] for item in row['open_items'])
        _check(Counter(kinds) == Counter(expect_open), f'冻结的未决事项 {kinds} != {sorted(expect_open)}')
        return {'decision': decision, 'open_items': kinds, 'simulation': bool(chosen)}

    def step_export(self, rule: str, where: dict | None = None, expect_basis: str | None = None,
                    expect_completed_inputs: int | None = None):
        case = self.case(rule, where)
        markdown = self.client.get(f"/api/v1/exports/cases/{case['id']}")
        _check(markdown.status_code == 200, f'Markdown 导出 HTTP {markdown.status_code}')
        missing = [s for s in EXPORT_SECTIONS if s not in markdown.text]
        _check(not missing, f'底稿缺少 {missing}')
        data = _ok(self.client.get(f"/api/v1/exports/cases/{case['id']}?format=json"))
        for key in ('evidence', 'snapshot', 'lineage', 'evidence_requests', 'treatment_draft', 'open_items', 'reviews'):
            _check(key in data, f'JSON 底稿缺少 {key}')
        _check(bool(data['reviews']), '底稿没有复核记录')
        if expect_basis is not None:
            _check(data['simulations'][-1]['basis']['source'] == expect_basis, '模拟来源未写入底稿')
        if expect_completed_inputs is not None:
            got = len(data['simulations'][-1]['basis']['completed_inputs'])
            _check(got == expect_completed_inputs, f'补齐字段 {got} != {expect_completed_inputs}')
        return {'evidence': len(data['evidence']), 'reviews': len(data['reviews'])}

    def run(self) -> dict:
        steps = []
        for kind, args in self.cell['steps']:
            try:
                detail = getattr(self, f'step_{kind}')(**args)
                steps.append({'step': kind, 'args': _plain(args), 'passed': True, 'observed': detail})
            except StepFailure as failure:
                steps.append({'step': kind, 'args': _plain(args), 'passed': False, 'failure': str(failure)})
                break
        passed = len(steps) == len(self.cell['steps']) and all(s['passed'] for s in steps)
        return {'id': self.cell['id'], 'pack': self.cell['pack'], 'condition': self.cell['condition'],
                'title': self.cell['title'], 'passed': passed, 'steps': steps, 'boundary': self.cell.get('boundary')}


def _plain(args: dict) -> dict:
    return {k: v for k, v in args.items() if k != 'rows'} | ({'rows': len(args['rows'])} if 'rows' in args else {})


def run_all(client, cells: list[dict] | None = None) -> dict:
    results = [CellRun(client, cell).run() for cell in (cells or CELLS)]
    return {'runner_version': RUNNER_VERSION, 'cell_count': len(results),
            'passed': sum(r['passed'] for r in results), 'failed': [r['id'] for r in results if not r['passed']],
            'matrix': {pack: {r['condition']: r['passed'] for r in results if r['pack'] == pack}
                       for pack in ('funds', 'purchase', 'period')},
            'cells': results,
            'scope': '手写合成账套逐格验收产品流程；不代表真实企业数据、行业覆盖率或模型效果。'}
