"""Hand-specified source graphs; no model, production calculator, or truth files."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.business_chain import business_chain
from app.investigation import BusinessQuery, EvidenceMemory, ToolContext, planning_tools
from app.model_gateway import RunBudget
from datasets.generate import demo_bundle


def row(identity, **extra):
    return {"id": identity, "evidence_id": f"ev-{identity}", "source_id": f"source-{identity}",
            "kind": "journal", "counterparty": "Private vendor A", "currency": "CNY", "book_id": "book-A",
            "business_date": "2026-09-01", "observed_at": "2026-09-02T00:00:00Z", **extra}


def context(rows):
    return ToolContext(rows, {"framework": "CAS", "version": "hand-case", "book_id": "book-A",
                             "snapshot_cutoff": "2026-09-30T23:59:59Z"},
                       {"record_ids": [], "evidence_ids": []}, RunBudget())


def ids(result):
    return [item["id"] for item in result["records"]]


def assert_source_edges(result, rows):
    lookup = {item["id"]: item for item in rows}
    assert len(result["relationships"]) <= max(0, result["returned_count"] - 1)
    for edge in result["relationships"]:
        left, right = lookup[edge["from_record_id"]], lookup[edge["to_record_id"]]
        field = edge["source_field"]
        assert field in {"order_id", "invoice_id"}
        assert left[field] == right[field] == edge["source_value"]
        assert left["counterparty"] == right["counterparty"] and left["currency"] == right["currency"]
        assert edge["relation_type"] == "source_declared"
        assert edge["from_evidence_id"] == left["evidence_id"] and edge["to_evidence_id"] == right["evidence_id"]


@pytest.mark.parametrize("selector,seeds", [({"order_id": "PO-001"}, 13),
                                           ({"invoice_id": "INV-001"}, 11),
                                           ({"reference": "INV-001"}, 1)])
def test_actual_demo_recovers_order_and_receipt_from_thirteen_declared_rows(selector, seeds):
    rows = demo_bundle()["records"]  # No evaluator labels or supplemental records.
    original = deepcopy(rows)
    result = business_chain(rows, **selector)
    assert result["seed_count"] == seeds and result["total"] == 13 and not result["truncated"]
    assert set(ids(result)) == {item["id"] for item in rows}
    assert {item["kind"] for item in result["records"]} == {"order", "receipt", "invoice", "bank", "journal"}
    assert sum(item["kind"] == "journal" for item in result["records"]) == 8
    assert rows == original
    assert_source_edges(result, rows)


def test_anchor_evidence_and_multiple_invoices_preserve_voucher_line_ids():
    rows = [row("o", kind="order", order_id="O"), row("i1", kind="invoice", order_id="O", invoice_id="I1"),
            row("i2", kind="invoice", order_id="O", invoice_id="I2"),
            row("j1", order_id="O", invoice_id="I1", voucher_id="V1", account="1405"),
            row("j2", order_id="O", invoice_id="I1", voucher_id="V1", account="2202"),
            row("j3", order_id="O", invoice_id="I2", voucher_id="V2", account="1405"),
            row("j4", order_id="O", invoice_id="I2", voucher_id="V2", account="2202")]
    result = business_chain(rows, anchor_evidence_id="ev-i1")
    assert result["seed_count"] == 1 and result["total"] == 7
    assert ids(result)[0] == "i1" and set(ids(result)) == {"o", "i1", "i2", "j1", "j2", "j3", "j4"}
    assert [item["id"] for item in result["records"] if item.get("voucher_id") == "V1"] == ["j1", "j2"]
    assert_source_edges(result, rows)


def collision_rows():
    return [row("a1", order_id="O"), row("a2", order_id="O"),
            row("b1", order_id="O", counterparty="Private vendor B"),
            row("b2", order_id="O", counterparty="Private vendor B"),
            row("c1", order_id="O", currency="USD"), row("c2", order_id="O", currency="USD")]


def test_reused_source_number_returns_identity_groups_without_merging():
    rows = collision_rows()
    all_groups = business_chain(rows, order_id="O")
    assert all_groups["association_status"] == "ambiguous" and all_groups["total"] == 6
    assert all_groups["ambiguity_count"] == 1
    groups = all_groups["ambiguities"][0]["groups"]
    assert {tuple(group["returned_record_ids"]) for group in groups} == {("a1", "a2"), ("b1", "b2"), ("c1", "c2")}
    assert_source_edges(all_groups, rows)
    anchored = business_chain(rows, anchor_evidence_id="ev-a1")
    assert ids(anchored) == ["a1", "a2"] and anchored["ambiguities"][0]["group_count"] == 3
    # The same source number across identities is reported, never an expansion edge.
    assert anchored["association_status"] == "ambiguous"
    assert sum(len(group["returned_record_ids"]) for group in anchored["ambiguities"][0]["groups"]) == 2


@pytest.mark.parametrize("missing", [{"counterparty": None}, {"counterparty": ""}, {"counterparty": " "},
                                      {"currency": None}, {"currency": ""}, {"currency": " "}])
def test_missing_identity_never_expands_or_bridges(missing):
    rows = [row("a", order_id="O", **missing), row("b", order_id="O", **missing), row("c", order_id="O")]
    result = business_chain(rows, anchor_evidence_id="ev-a")
    assert ids(result) == ["a"] and result["relationships"] == []
    assert result["unexpanded_missing_identity_count"] == 1
    complete = business_chain(rows, anchor_evidence_id="ev-c")
    assert ids(complete) == ["c"]  # Never borrows a known identity for incomplete rows.


def test_reference_amount_date_and_cross_field_equal_strings_are_not_edges():
    rows = [row("a", reference="REF", order_id="SHARED", amount="100.00"),
            row("b", reference="SHARED", amount="100.00"),
            row("c", invoice_id="SHARED", amount="100.00"),
            row("d", reference="REF", order_id="OTHER", amount="100.00")]
    result = business_chain(rows, anchor_evidence_id="ev-a")
    assert ids(result) == ["a"] and result["relationships"] == []
    # Equal reference selects seeds only; it does not make a declared edge.
    result = business_chain(rows, reference="REF")
    assert ids(result) == ["a", "d"] and result["relationships"] == []


def test_multiple_selectors_intersect_and_unknown_anchor_does_not_broaden():
    rows = [row("a", order_id="O", invoice_id="I"), row("b", order_id="O", invoice_id="J")]
    assert business_chain(rows, anchor_evidence_id="ev-a", invoice_id="J")["total"] == 0
    assert business_chain(rows, anchor_evidence_id="unknown")["total"] == 0
    assert business_chain(rows, invoice_id="I", order_id="other")["total"] == 0


def hop_rows():
    # A --order O1--> B --invoice I2--> C --order O2--> D.
    return [row("a", order_id="O1", invoice_id="I1"), row("b", order_id="O1", invoice_id="I2"),
            row("c", order_id="O2", invoice_id="I2"), row("d", order_id="O2", invoice_id="I3")]


@pytest.mark.parametrize("hops,expected", [(0, ["a"]), (1, ["a", "b"]), (2, ["a", "b", "c"])])
def test_two_hop_ceiling_and_path_provenance(hops, expected):
    rows = hop_rows()
    result = business_chain(rows, anchor_evidence_id="ev-a", max_hops=hops)
    assert ids(result) == expected and result["hops_reached"] == hops
    assert result["record_hops"] == {identity: index for index, identity in enumerate(expected)}
    assert_source_edges(result, rows)


def test_return_limit_reports_full_bounded_discovery_and_never_returns_dangling_edges():
    result = business_chain(hop_rows(), anchor_evidence_id="ev-a", limit=1)
    assert ids(result) == ["a"] and result["total"] == 3 and result["truncated"]
    assert result["hop_counts"] == {"0": 1, "1": 1, "2": 1} and result["hops_reached"] == 2
    assert result["relationships"] == [] and result["record_hops"] == {"a": 0}


def test_seeds_precede_expansions_and_reordering_cannot_change_truncation():
    rows = [row("z", reference="TARGET", order_id="O"), row("x", reference="TARGET", order_id="O"),
            row("a", order_id="O"), row("w", order_id="O")]
    original = deepcopy(rows)
    result = business_chain(rows, reference="TARGET", limit=2)
    assert ids(result) == ["x", "z"] and result["seed_count"] == 2 and result["total"] == 4
    assert result == business_chain(list(reversed(rows)), reference="TARGET", limit=2)
    assert rows == original


def test_relationships_and_ambiguity_group_payload_converge_with_record_limit():
    dense = [row(f"r-{index:03}", order_id="O", invoice_id="I") for index in range(100)]
    result = business_chain(dense, reference=None, order_id="O", limit=7)
    assert len(result["relationships"]) == 6 and result["total"] == 100 and result["truncated"]
    assert_source_edges(result, dense)
    collisions = [row(f"r-{index:03}", order_id="O", counterparty=f"Secret party {index}") for index in range(100)]
    result = business_chain(collisions, order_id="O", limit=3)
    assert result["ambiguities"][0]["group_count"] == 100 and result["ambiguities_truncated"]
    assert sum(len(item["groups"]) for item in result["ambiguities"]) <= 3
    assert "Secret party" not in json.dumps({key: value for key, value in result.items() if key != "records"})
    assert len(json.dumps({key: value for key, value in result.items() if key != "records"})) < 5000


def test_tool_output_metadata_and_rows_both_use_public_party_aliases():
    rows = collision_rows()
    result = context(rows).execute("get_business_chain", {"order_id": "O"})
    encoded = json.dumps(result)
    assert "Private vendor" not in encoded
    aliases = {item["counterparty"] for item in result["records"]}
    assert {group["counterparty"] for group in result["ambiguities"][0]["groups"]} <= aliases
    assert result["total"] == 6


def test_anchor_is_available_in_planner_and_incremental_memory_keeps_safe_metadata():
    rows = hop_rows()
    scoped, memory = context(rows), EvidenceMemory()
    args = BusinessQuery(anchor_evidence_id="ev-a")
    assert planning_tools(scoped, memory)["get_business_chain"].availability(args) == "available"
    result = memory.execute(scoped, "get_business_chain", args.model_dump())
    assert result["record_evidence_ids"] == ["ev-a", "ev-b", "ev-c"]
    assert result["record_hops"] == {"a": 0, "b": 1, "c": 2}
    assert memory.execute(scoped, "get_business_chain", args.model_dump())["cache_hit"] is True


@pytest.mark.parametrize("field", ["order_id", "invoice_id", "reference", "anchor_evidence_id"])
def test_blank_query_identifiers_are_rejected(field):
    with pytest.raises(ValidationError):
        BusinessQuery.model_validate({field: " "})


@pytest.mark.parametrize("extra", [{"book_id": "book-B"}, {"observed_at": "2026-10-01T00:00:00Z"},
                                    {"observed_at": None}])
def test_tool_context_rejects_foreign_or_future_rows_before_chain_expansion(extra):
    with pytest.raises(ValueError):
        context([row("a", order_id="O"), row("b", order_id="O", **extra)])


def test_shared_helper_also_does_not_bridge_explicit_book_boundaries():
    rows = [row("a", order_id="O"), row("b", order_id="O", book_id="book-B")]
    assert ids(business_chain(rows, anchor_evidence_id="ev-a")) == ["a"]


@pytest.mark.parametrize("bad", [{}, {"order_id": ""}, {"order_id": "O", "max_hops": 3},
                                {"order_id": "O", "limit": 0}])
def test_invalid_requests_are_not_unfiltered_queries(bad):
    with pytest.raises(ValueError):
        business_chain([row("a", order_id="O")], **bad)


def test_duplicate_source_row_ids_are_rejected_instead_of_collapsed():
    with pytest.raises(ValueError, match="unique"):
        business_chain([row("a", order_id="O"), row("a", order_id="O")], order_id="O")


def test_journal_query_remains_an_exact_filter():
    rows = [row("b", kind="bank", order_id="O", reference="R"), row("a", order_id="O", reference="R"),
            row("c", order_id="O", reference="OTHER")]
    assert ids(context(rows).execute("query_journal_lines", {"reference": "R"})) == ["a"]


def test_api_automatic_expansion_is_safe_and_explicit_references_remain(monkeypatch, tmp_path):
    # On a standalone test import, config's relative .env lookup sees only the
    # empty temporary directory. No key, live database, or real file is opened.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    from app import main

    rows = [row("a", order_id="O", invoice_id="I"), row("b", order_id="O"),
            row("c", order_id="O", counterparty="Private vendor B"),
            row("d", order_id="O", currency="USD"), row("e", order_id="O", counterparty=""),
            row("f", order_id="OTHER"), row("g", order_id="OTHER")]
    snap = SimpleNamespace(id="snap", book_id="book-A", policy={})
    case = SimpleNamespace(id="case", snapshot_id="snap", book_id="book-A",
                           finding={"record_ids": ["a"], "evidence_ids": ["ev-f"]})
    # Legacy run without a frozen policy context: the loader must find none.
    objects = {("Snapshot", "snap"): snap, ("Case", "case"): case,
               ("Book", "book-A"): SimpleNamespace(id="book-A")}
    for item in rows:
        objects["Record", item["id"]] = SimpleNamespace(id=item["id"], evidence_id=item["evidence_id"],
                document_id="doc", source_id=item["source_id"], kind=item["kind"], locator={"row": item["id"]})
    objects["Document", "doc"] = SimpleNamespace(id="doc", name="source.json", synthetic=True)
    runs = [SimpleNamespace(id="run", case_id="case",
                            result={"claims": [{"evidence_ids": ["ev-c", "foreign-ev", "future-ev"]}]})]
    session = SimpleNamespace(get=lambda cls, identity: objects.get((cls.__name__, identity)),
                              scalars=lambda _: runs, scalar=lambda _: None)
    monkeypatch.setattr(main, "snapshot_records", lambda *_: deepcopy(rows))
    before = deepcopy(rows)
    result = main.evidence_rows(session, case)
    # a expands b; explicit finding f expands g. Explicit claim c is retained,
    # but its foreign identity does not trigger automatic merging of d or e.
    assert {item["record_id"] for item in result} == {"a", "b", "c", "f", "g"}
    assert rows == before
