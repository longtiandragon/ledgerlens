"""Bounded source-declared relationships inside an already selected snapshot.

Identifiers are compared only within their original field and complete identity.
This helper does not infer links from money, dates, names, or reference strings.
Returned rows remain source records; the relationship metadata is not an
assertion that their substantive accounting treatment is correct.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

LINK_FIELDS = ("order_id", "invoice_id")


def _present(value):
    return isinstance(value, str) and bool(value.strip())


def _identity(row):
    if not _present(row.get("counterparty")) or not _present(row.get("currency")):
        return None
    return row.get("book_id") or "", row["counterparty"], row["currency"]


def _public_identity(identity):
    # Same alias as ToolContext.public_record: metadata must not disclose names.
    if identity is None:
        return {"counterparty": None, "currency": None, "identity_complete": False}
    _, party, currency = identity
    return {"counterparty": "party-" + hashlib.sha256(party.encode()).hexdigest()[:12],
            "currency": currency, "identity_complete": True}


def business_chain(records: list[dict], *, order_id: str | None = None,
                   invoice_id: str | None = None, reference: str | None = None,
                   anchor_evidence_id: str | None = None, seed_record_ids=None,
                   limit: int = 50, max_hops: int = 2, include_metadata: bool = True) -> dict:
    """Exact seeds, followed by at most two hops of identity-scoped links.

    Multiple supplied selectors intersect. ``reference`` only selects seeds.
    Record IDs are never collapsed by voucher, source identifier, or invoice.
    Discovery is complete within the hop bound before the return limit, so
    ``total``/``truncated`` describe discovery rather than a guessed count.
    Model-facing edges form a forest and ambiguity metadata has a global
    group limit, keeping the response proportional to the returned row limit.
    ``seed_record_ids`` and ``include_metadata`` are internal UI options.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("Business chain limit must be a positive integer")
    if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 0 <= max_hops <= 2:
        raise ValueError("Business chain hop limit must be between zero and two")
    selectors = {key: value for key, value in
                 (("order_id", order_id), ("invoice_id", invoice_id), ("reference", reference),
                  ("evidence_id", anchor_evidence_id)) if value is not None}
    if any(not _present(value) for value in selectors.values()):
        raise ValueError("Business chain identifiers must be nonempty strings")
    if not selectors and seed_record_ids is None:
        raise ValueError("Business chain query requires an identifier")
    by_id = {}
    for row in records:
        identity = row.get("id")
        if not _present(identity) or identity in by_id:
            raise ValueError("Business chain requires unique nonempty record IDs")
        by_id[identity] = row
    explicit = None if seed_record_ids is None else set(seed_record_ids)
    seeds = sorted(row_id for row_id, row in by_id.items()
                   if (explicit is None or row_id in explicit)
                   and all(row.get(key) == value for key, value in selectors.items()))

    index, scopes = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for row_id in sorted(by_id):
        row = by_id[row_id]
        identity = _identity(row)
        for field in LINK_FIELDS:
            if _present(row.get(field)):
                value = row[field]
                # Missing identity cannot form a bridge, even to other unknowns.
                scope = identity if identity is not None else ("missing", row_id)
                scopes[field, value][scope].append(row_id)
                if identity is not None:
                    index[field, value, identity].append(row_id)

    hops = dict.fromkeys(seeds, 0)
    discovery, parents, frontier = list(seeds), {}, seeds
    expanded_keys = set()
    for hop in range(1, max_hops + 1):
        pending = {}
        for parent_id in frontier:
            row = by_id[parent_id]
            identity = _identity(row)
            if identity is None:
                continue
            for field in LINK_FIELDS:
                if not _present(row.get(field)):
                    continue
                key = field, row[field], identity
                if key in expanded_keys:
                    continue
                expanded_keys.add(key)
                for row_id in index[key]:
                    if row_id not in hops:
                        pending.setdefault(row_id, (parent_id, field, row[field]))
        frontier = sorted(pending)
        for row_id in frontier:
            hops[row_id] = hop
            parents[row_id] = pending[row_id]
        discovery.extend(frontier)
        if not frontier:
            break

    returned = discovery[:limit]
    result = {"records": [by_id[row_id] for row_id in returned], "total": len(discovery),
              "returned_count": len(returned), "truncated": len(discovery) > limit,
              "seed_count": len(seeds), "returned_seed_count": sum(hops[row_id] == 0 for row_id in returned),
              "max_hops": max_hops, "hops_reached": max(hops.values(), default=0),
              "hop_counts": {str(hop): sum(value == hop for value in hops.values())
                             for hop in range(max_hops + 1)}}
    if not include_metadata:
        return result

    returned_set = set(returned)
    forest = {row_id: row_id for row_id in returned}

    def root(row_id):
        while forest[row_id] != row_id:
            forest[row_id] = forest[forest[row_id]]
            row_id = forest[row_id]
        return row_id

    edges = []

    def add_edge(left, right, field, value):
        left_root, right_root = root(left), root(right)
        if left_root == right_root:
            return
        forest[right_root] = left_root
        edges.append({"relation_type": "source_declared", "source_field": field, "source_value": value,
                      "from_record_id": left, "to_record_id": right,
                      "from_evidence_id": by_id[left].get("evidence_id"),
                      "to_evidence_id": by_id[right].get("evidence_id"), "hop": hops[right]})

    # Preserve the path used to discover each non-seed before linking seeds.
    for row_id in returned:
        if row_id in parents:
            left, field, value = parents[row_id]
            if left in returned_set:
                add_edge(left, row_id, field, value)
    touched = defaultdict(list)
    for row_id in returned:
        row, identity = by_id[row_id], _identity(by_id[row_id])
        if identity is not None:
            for field in LINK_FIELDS:
                if _present(row.get(field)):
                    touched[field, row[field], identity].append(row_id)
    for (field, value, _), row_ids in sorted(touched.items()):
        for right in row_ids[1:]:
            add_edge(row_ids[0], right, field, value)

    discovered_keys = {(field, by_id[row_id][field]) for row_id in discovery
                       for field in LINK_FIELDS if _present(by_id[row_id].get(field))}
    ambiguous_keys = {key for key in discovered_keys if len(scopes[key]) > 1}
    returned_keys = {(field, by_id[row_id][field]) for row_id in returned
                     for field in LINK_FIELDS if _present(by_id[row_id].get(field))}
    ambiguities, group_budget = [], limit
    for field, value in sorted(ambiguous_keys & returned_keys):
        groups = []
        source_groups = sorted(scopes[field, value].items(), key=lambda pair: tuple(pair[1]))
        for scope, row_ids in source_groups[:group_budget]:
            visible = [row_id for row_id in row_ids if row_id in returned_set]
            identity = _identity(by_id[row_ids[0]])
            groups.append({**_public_identity(identity), "record_count": len(row_ids),
                           "returned_record_ids": visible,
                           "returned_evidence_ids": [by_id[row_id]["evidence_id"] for row_id in visible
                                                     if by_id[row_id].get("evidence_id")]})
        group_budget -= len(groups)
        ambiguities.append({"source_field": field, "source_value": value, "groups": groups,
                            "group_count": len(source_groups), "groups_truncated": len(groups) < len(source_groups)})
    result.update({"relationships": edges, "relationship_type": "source_declared",
                   "record_hops": {row_id: hops[row_id] for row_id in returned},
                   "association_status": "ambiguous" if ambiguous_keys else "source_declared" if discovery else "no_match",
                   "ambiguity_count": len(ambiguous_keys), "ambiguities": ambiguities,
                   "ambiguities_truncated": len(ambiguities) < len(ambiguous_keys)
                   or any(item["groups_truncated"] for item in ambiguities),
                   "unexpanded_missing_identity_count": sum(_identity(by_id[row_id]) is None for row_id in discovery),
                   "notice": "仅沿原始同字段订单/发票号及完整身份形成来源关系；同号跨身份分组、身份缺失不扩展。来源关系不证明会计处理正确。"})
    return result
