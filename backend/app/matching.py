"""Bounded, exact candidate matching. Candidates never constitute reconciliation."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import combinations
from time import monotonic
from typing import Any

from .domain import money

MAX_SOLUTIONS = 128
MAX_INPUT_RECORDS = 250
MAX_GROUPS = 32
MAX_TOTAL_CANDIDATES = 512
REQUEST_SECONDS = 5.0


def _cents(row: dict, flow: str = "payable") -> int:
    if row.get("kind") == "journal":
        # A voucher has two sides; comparing both sides would allocate the same
        # payment twice. Only cash/bank-account entries represent cash movement.
        if not str(row.get("account", "")).startswith(("1001", "1002")):
            return 0
        raw = money(row.get("debit", "0")) - money(row.get("credit", "0"))
        raw = -raw if flow == "payable" else raw
    else:
        raw = money(row["amount"]) if "amount" in row else money(row.get("debit", "0")) - money(row.get("credit", "0"))
    # Bank amounts always mean cash-in positive / cash-out negative. The flow
    # changes only the matching coordinate system, never the source meaning.
    if row.get("kind") == "bank":
        direction = row.get("direction", "")
        # "refund" identifies a business operation, not a cash direction: a
        # supplier refund is incoming while a customer refund is outgoing.
        expected_sign = {"incoming": 1, "receipt": 1, "outgoing": -1, "payment": -1}.get(direction)
        if expected_sign is not None and raw and (1 if raw > 0 else -1) != expected_sign:
            raise ValueError("Bank direction contradicts signed amount; source data requires correction")
        raw = -raw if flow == "payable" else raw
    return int(raw * 100)


def _key(row: dict) -> tuple[str, str, str]:
    return (str(row.get("currency", "CNY")), str(row.get("counterparty", "")).strip().casefold(),
            str(row.get("order_id", "")))


def _within_window(rows: list[dict], days: int = 45) -> bool:
    dates = [date.fromisoformat(r["business_date"]) for r in rows if r.get("business_date")]
    return not dates or (max(dates) - min(dates)).days <= days


def _enumerate(left: list[int], right: list[int], time_limit: float = 2.0) -> tuple[list[tuple[tuple[int, ...], tuple[int, ...]]], bool, str]:
    deadline = monotonic() + time_limit
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        results = []
        right_sums: dict[int, list[tuple[int, ...]]] = defaultdict(list)
        for n in range(1, len(right) + 1):
            for subset in combinations(range(len(right)), n):
                if monotonic() >= deadline:
                    return results, True, "bounded_enumeration"
                right_sums[sum(right[i] for i in subset)].append(subset)
        for n in range(1, len(left) + 1):
            for subset in combinations(range(len(left)), n):
                if monotonic() >= deadline:
                    return results, True, "bounded_enumeration"
                for rhs in right_sums.get(sum(left[i] for i in subset), []):
                    if len(results) >= MAX_SOLUTIONS:
                        return results, True, "bounded_enumeration"
                    results.append((subset, rhs))
        return results, False, "bounded_enumeration"
    model = cp_model.CpModel()
    lhs = [model.new_bool_var(f"l{i}") for i in range(len(left))]
    rhs = [model.new_bool_var(f"r{i}") for i in range(len(right))]
    model.add(sum(lhs) >= 1)
    model.add(sum(rhs) >= 1)
    model.add(sum(v * x for v, x in zip(left, lhs)) == sum(v * x for v, x in zip(right, rhs)))
    results = []

    class Collector(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self):
            results.append((tuple(i for i, x in enumerate(lhs) if self.value(x)),
                            tuple(i for i, x in enumerate(rhs) if self.value(x))))
            if len(results) >= MAX_SOLUTIONS:
                self.stop_search()

    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = time_limit
    status = solver.solve(model, Collector())
    truncated = status not in {cp_model.OPTIMAL, cp_model.INFEASIBLE} or len(results) >= MAX_SOLUTIONS
    return sorted(results), truncated, "ortools_cp_sat"


def match_transactions(left: list[dict], right: list[dict], max_group: int = 6,
                       flow: str = "payable") -> dict[str, Any]:
    if flow not in {"payable", "receivable"}:
        raise ValueError("flow must be payable or receivable")
    if not 1 <= max_group <= 12:
        raise ValueError("max_group must be in [1, 12]")
    if len(left) + len(right) > MAX_INPUT_RECORDS:
        return {"candidates": [], "partial_candidates": [], "flow": flow, "status": "bounded", "backend": "none",
                "unmatched": {"left": sorted(str(r["id"]) for r in left), "right": sorted(str(r["id"]) for r in right)},
                "limited_groups": [{"reason": "request_record_limit", "maximum": MAX_INPUT_RECORDS}],
                "committed_allocations": [], "note": "请求超过记录上限，请按账户、主体或期间缩小范围。"}
    deadline = monotonic() + REQUEST_SECONDS
    all_ids = [str(r["id"]) for r in [*left, *right]]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Transaction identifiers must be unique across both sides")
    left_groups: dict[tuple, list[dict]] = defaultdict(list)
    right_groups: dict[tuple, list[dict]] = defaultdict(list)
    for rows, groups in [(left, left_groups), (right, right_groups)]:
        for row in rows:
            value = _cents(row, flow)
            if value:
                groups[(*_key(row), 1 if value > 0 else -1)].append(row)
    candidates, partial_candidates, limited_groups, backends = [], [], [], set()
    for group_index, key in enumerate(sorted(set(left_groups) & set(right_groups))):
        if group_index >= MAX_GROUPS or monotonic() >= deadline or len(candidates) >= MAX_TOTAL_CANDIDATES:
            limited_groups.append({"reason": "request_budget_limit", "maximum_groups": MAX_GROUPS,
                                   "maximum_candidates": MAX_TOTAL_CANDIDATES, "maximum_seconds": REQUEST_SECONDS})
            break
        ls, rs = sorted(left_groups[key], key=lambda r: r["id"]), sorted(right_groups[key], key=lambda r: r["id"])
        if len(ls) > max_group or len(rs) > max_group:
            limited_groups.append({"currency": key[0], "counterparty": key[1], "order_id": key[2],
                                   "reason": "group_size_limit", "left_count": len(ls), "right_count": len(rs)})
            continue
        if not key[1] and not key[2]:
            # Matching on amount alone cannot establish a business relationship.
            limited_groups.append({"currency": key[0], "reason": "missing_counterparty_and_order"})
            continue
        solutions, truncated, backend = _enumerate([abs(_cents(r, flow)) for r in ls], [abs(_cents(r, flow)) for r in rs],
                                                    max(0.001, min(2.0, deadline - monotonic())))
        backends.add(backend)
        if truncated:
            limited_groups.append({"currency": key[0], "counterparty": key[1], "order_id": key[2], "reason": "solution_limit"})
        for li, ri in solutions:
            if len(candidates) >= MAX_TOTAL_CANDIDATES:
                limited_groups.append({"reason": "request_candidate_limit", "maximum": MAX_TOTAL_CANDIDATES})
                break
            selected = [ls[i] for i in li] + [rs[i] for i in ri]
            if not _within_window(selected):
                continue
            total = sum(abs(_cents(ls[i], flow)) for i in li)
            candidates.append({"left_ids": [ls[i]["id"] for i in li], "right_ids": [rs[i]["id"] for i in ri],
                               "amount": f"{total // 100}.{total % 100:02d}", "currency": key[0], "order_id": key[2],
                               "allocation_status": "candidate", "requires_review": True})
        # Partial allocation is explicit and never silently considered settled.
        if not solutions and len(ls) == len(rs) == 1 and key[2] and _within_window(ls + rs):
            lv, rv = abs(_cents(ls[0], flow)), abs(_cents(rs[0], flow))
            partial_candidates.append({"left_ids": [ls[0]["id"]], "right_ids": [rs[0]["id"]],
                                       "amount": f"{min(lv, rv) // 100}.{min(lv, rv) % 100:02d}",
                                       "remaining_left": f"{max(lv-rv, 0) // 100}.{max(lv-rv, 0) % 100:02d}",
                                       "remaining_right": f"{max(rv-lv, 0) // 100}.{max(rv-lv, 0) % 100:02d}",
                                       "requires_review": True, "allocation_status": "partial_candidate"})
    candidates.sort(key=lambda c: (tuple(c["left_ids"]), tuple(c["right_ids"])))
    # A candidate that is exactly the union of two disjoint candidates (60+40
    # against 60+40, next to 60/60 and 40/40) is a coarser view of the same
    # pairing, not another explanation. Ambiguity is judged without them.
    keyed = {(frozenset(c["left_ids"]), frozenset(c["right_ids"])) for c in candidates}
    for candidate in candidates:
        left_ids, right_ids = frozenset(candidate["left_ids"]), frozenset(candidate["right_ids"])
        candidate["composite"] = any(part_left < left_ids and part_right < right_ids
                                     and (left_ids - part_left, right_ids - part_right) in keyed
                                     for part_left, part_right in keyed)
    membership: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        if candidate["composite"]:
            continue
        for identifier in [*candidate["left_ids"], *candidate["right_ids"]]:
            membership[identifier] += 1
    ambiguous = any(n > 1 for n in membership.values())
    return {"candidates": candidates, "partial_candidates": partial_candidates, "flow": flow,
            "unmatched": {"left": sorted(str(r["id"]) for r in left if str(r["id"]) not in membership),
                          "right": sorted(str(r["id"]) for r in right if str(r["id"]) not in membership)},
            "status": "bounded" if limited_groups else "ambiguous" if ambiguous else "candidates" if candidates else "unmatched",
            "backend": "+".join(sorted(backends)) or "none", "limited_groups": limited_groups,
            "committed_allocations": [], "note": "候选集合可能重叠；尚未核销，不得同时自动接受重叠候选。"}
