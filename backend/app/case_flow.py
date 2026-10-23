"""Case flow across snapshots and into review (Apache-2.0).

Cases are immutable findings of one check run on one frozen snapshot. When new
material arrives, the new snapshot is checked and each of its cases is linked
to the case of the previous check that it continues (same rule, shared source
records). A case the next check does not reproduce is reported as such; it is
never closed automatically. Open items gathered here are frozen into reviews.
"""
from __future__ import annotations

from sqlalchemy import func, select

from .db import Case, CaseLink, CheckRun, EvidenceRequest, Review, Run, Simulation, Snapshot
from .evidence_requests import requests_for_case

KIND_LABELS = {'order': '订单', 'receipt': '收货单', 'invoice': '发票', 'bank': '银行流水', 'journal': '记账凭证',
               'opening': '期初余额', 'trial_balance': '试算表'}
# A follow-up case on the new snapshot for a prior finding the check no longer
# reproduces. It is an observation to review, never an automatic closure.
CLEARED_RULE = 'PRIOR_FINDING_CLEARED'


def prior_check_run(s, book_id: str, snapshot: Snapshot) -> CheckRun | None:
    """The latest check run on the most recent earlier snapshot that was checked."""
    earlier = s.scalars(select(Snapshot).where(Snapshot.book_id == book_id, Snapshot.id != snapshot.id,
                                               Snapshot.created_at < snapshot.created_at)
                        .order_by(Snapshot.created_at.desc())).all()
    for candidate in earlier:
        run = s.scalars(select(CheckRun).where(CheckRun.snapshot_id == candidate.id)
                        .order_by(CheckRun.created_at.desc())).first()
        if run is not None:
            return run
    return None


def cleared_finding(prior: Case, prior_run: CheckRun, snapshot: Snapshot, scenario: str | None) -> dict:
    finding = prior.finding or {}
    visible = set(snapshot.record_ids)
    return {'rule_id': CLEARED_RULE, 'title': f"上次检查的发现未再出现：{finding.get('title')}", 'severity': 'low',
            'status': 'normal', 'scenario': scenario,
            'record_ids': sorted(i for i in finding.get('record_ids', []) if i in visible),
            'evidence_ids': list(finding.get('evidence_ids', [])),
            'explanation': (f"上次检查（快照 {prior_run.snapshot_id}）发现“{finding.get('title')}”，本快照按当前规则检查未再触发。"
                            '常见原因是补充了迟到资料或来源已更正；也可能是期间或科目表等政策变化。'
                            '系统不自动认定问题已解决：请核对前一案件的补证记录和资料变化后复核。'),
            'missing_evidence': [],
            'metrics': {'prior_case_id': prior.id, 'prior_rule_id': finding.get('rule_id'),
                        'prior_status': finding.get('status'), 'prior_snapshot_id': prior.snapshot_id}}


def continue_cases(s, check_run: CheckRun, snapshot: Snapshot, cases: list[Case],
                   scenario_of) -> tuple[list[CaseLink], list[Case]]:
    """Link new cases to the prior check and open follow-ups for findings it no longer shows."""
    prior_run = prior_check_run(s, check_run.book_id, snapshot)
    if prior_run is None:
        return [], []
    prior_cases = s.scalars(select(Case).where(Case.check_run_id == prior_run.id).order_by(Case.id)).all()
    links = link_cases(cases, prior_cases, prior_run, check_run)
    continued = {link.prior_case_id for link in links}
    cleared = []
    for prior in prior_cases:
        rule = prior.finding.get('rule_id')
        if prior.id in continued or prior.finding.get('status') == 'normal' or rule == CLEARED_RULE:
            continue
        case = Case(book_id=check_run.book_id, snapshot_id=snapshot.id, check_run_id=check_run.id,
                    finding=cleared_finding(prior, prior_run, snapshot, scenario_of(rule)))
        cleared.append((case, prior))
    s.add_all([case for case, _ in cleared])
    s.flush()
    for case, prior in cleared:
        links.append(CaseLink(book_id=case.book_id, case_id=case.id, prior_case_id=prior.id,
                              basis={'match': 'not_reproduced', 'rule_id': prior.finding.get('rule_id'),
                                     'shared_record_ids': case.finding['record_ids'],
                                     'prior_snapshot_id': prior.snapshot_id, 'prior_check_run_id': prior_run.id,
                                     'check_run_id': check_run.id}))
    s.add_all(links)
    return links, [case for case, _ in cleared]


def link_cases(cases: list[Case], prior_cases: list[Case], prior_run: CheckRun, check_run: CheckRun) -> list[CaseLink]:
    """Link each new case to the prior case with the same rule sharing the most records."""
    links = []
    for case in cases:
        rule = case.finding.get('rule_id')
        records = set(case.finding.get('record_ids', []))
        best, shared_best = None, set()
        for prior in prior_cases:
            if prior.finding.get('rule_id') != rule:
                continue
            shared = records & set(prior.finding.get('record_ids', []))
            if shared and len(shared) > len(shared_best):
                best, shared_best = prior, shared
        if best is None:
            continue
        same = set(best.finding.get('record_ids', [])) == records
        links.append(CaseLink(book_id=case.book_id, case_id=case.id, prior_case_id=best.id,
                              basis={'match': 'same_records' if same else 'shared_records', 'rule_id': rule,
                                     'shared_record_ids': sorted(shared_best), 'prior_snapshot_id': best.snapshot_id,
                                     'prior_check_run_id': prior_run.id, 'check_run_id': check_run.id}))
    return links


def _count(s, model, case_id: str) -> int:
    return s.scalar(select(func.count()).select_from(model).where(model.case_id == case_id)) or 0


def summary(s, case: Case) -> dict:
    finding = case.finding or {}
    return {'case_id': case.id, 'snapshot_id': case.snapshot_id, 'check_run_id': case.check_run_id,
            'created_at': case.created_at, 'rule_id': finding.get('rule_id'), 'title': finding.get('title'),
            'status': finding.get('status'), 'runs': _count(s, Run, case.id),
            'simulations': _count(s, Simulation, case.id), 'reviews': _count(s, Review, case.id),
            'evidence_requests': _count(s, EvidenceRequest, case.id)}


def lineage(s, case: Case, stale: bool) -> dict:
    prior, seen, current = [], {case.id}, case
    while True:
        link = s.scalars(select(CaseLink).where(CaseLink.case_id == current.id).order_by(CaseLink.created_at)).first()
        earlier = s.get(Case, link.prior_case_id) if link else None
        if earlier is None or earlier.id in seen:
            break
        prior.append({**summary(s, earlier), 'match': link.basis.get('match')})
        seen.add(earlier.id)
        current = earlier
    successors = []
    for link in s.scalars(select(CaseLink).where(CaseLink.prior_case_id == case.id).order_by(CaseLink.created_at)):
        later = s.get(Case, link.case_id)
        if later is not None:
            successors.append({**summary(s, later), 'match': link.basis.get('match')})
    snapshot = s.get(Snapshot, case.snapshot_id)
    next_check = None
    if snapshot is not None:
        later_snapshots = select(Snapshot.id).where(Snapshot.book_id == case.book_id,
                                                    Snapshot.created_at > snapshot.created_at)
        next_check = s.scalars(select(CheckRun).where(CheckRun.snapshot_id.in_(later_snapshots))
                               .order_by(CheckRun.created_at)).first()
    continued = any(item['match'] != 'not_reproduced' for item in successors)
    state = ('continued' if continued else 'not_reproduced' if successors or next_check is not None
             else 'not_rechecked' if stale else 'current')
    return {'state': state, 'prior': prior, 'successors': successors,
            'next_check': {'check_run_id': next_check.id, 'snapshot_id': next_check.snapshot_id} if next_check else None}


def latest_finished_run(s, case_id: str) -> Run | None:
    return s.scalars(select(Run).where(Run.case_id == case_id, Run.status.not_in(['queued', 'running']))
                     .order_by(Run.created_at.desc())).first()


def open_items(s, case: Case, *, simulation: Simulation | None, draft: dict | None,
               latest_run: dict | None) -> list[dict]:
    """Everything a reviewer should see as unresolved; informational, never auto-closed."""
    items = []
    sources = [(case.id, '')]
    if (case.finding or {}).get('rule_id') == CLEARED_RULE:
        # The follow-up is reviewed on the new snapshot; the requests that may
        # explain the change live on the prior case.
        sources.append((case.finding['metrics']['prior_case_id'], '前一案件'))
    for case_id, origin in sources:
        for request in requests_for_case(s, case_id):
            if request['status'] == 'open':
                items.append({'kind': 'evidence_request', 'ref': request['id'], 'case_id': case_id,
                              'text': f"{origin}待取得{KIND_LABELS.get(request['kind'], request['kind'])}："
                                      f"{request['description']}" + ('（已有候选资料，待人工确认）' if request['candidates'] else ''),
                              'candidates': len(request['candidates'])})
    if latest_run and latest_run.get('status') in {'failed', 'budget_exhausted'}:
        items.append({'kind': 'investigation_incomplete', 'ref': latest_run['id'],
                      'text': '最近一次调查未成功完成（失败或预算耗尽），结论仅依据规则发现。'})
    if simulation is not None:
        result = simulation.result or {}
        for finding in (result.get('comparison') or {}).get('introduced', []):
            items.append({'kind': 'introduced_finding', 'ref': simulation.id, 'rule_id': finding['rule_id'],
                          'text': f"所选模拟方案引入新问题：{finding['title']}。{finding['explanation']}"})
        if (result.get('target') or {}).get('status') == 'persisting':
            items.append({'kind': 'target_persisting', 'ref': simulation.id, 'text': '所选模拟方案未消除本案发现。'})
        for violation in result.get('violations', []):
            items.append({'kind': 'simulation_violation', 'ref': simulation.id, 'text': f'所选模拟方案违反约束：{violation}'})
    elif draft is not None:
        if draft['status'] in {'needs_evidence', 'needs_input', 'conditional'}:
            items.append({'kind': 'treatment_pending', 'ref': draft['digest'],
                          'text': f"处理路径未确定：{draft['summary']}。{draft['reason']}"})
        elif draft['status'] == 'proposed':
            items.append({'kind': 'treatment_not_simulated', 'ref': draft['digest'],
                          'text': f"已有处理草稿“{draft['summary']}”，尚未选择模拟方案。"})
    return items
