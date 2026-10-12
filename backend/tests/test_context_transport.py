"""Offline integrity regressions for the model-only context transport.

Handwritten expectations cover evidence, negative checks, order and collisions.
The optional local-report check reads observable sections only, never truth/.
"""

from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from app.context_transport import FORMAT, deserialize_context, pack_context, serialize_context, unpack_context


def wire_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())


def complex_context():
    passage = "Only the current snapshot can support a claim; missing documents remain missing. " * 4
    need = "Obtain the signed acceptance document for the current transaction and preserve the fixed snapshot."
    hypothesis = {"explanation": passage, "status": "unresolved", "evidence_ids": ["ev-1", "ev-2"],
                  "missing_evidence": [need]}
    request = {"tool": "request_evidence", "result": {
        "status": "requested", "description": need, "reason": passage,
        "requires_new_snapshot": True, "retrieved": False}}
    check = {"tool": "verify_claims", "result": {"claims": [
        {"citations_valid": True, "calculations_verified": False, "calculation_status": "not_verified",
         "semantic_status": "unverified_model_proposal", "text": passage},
        {"citations_valid": True, "calculations_verified": None, "calculation_status": "not_requested",
         "semantic_status": "unverified_model_proposal", "text": passage}]}}
    query = {"query_id": "visible-query", "tool": "get_business_chain",
             "arguments": {"anchor_evidence_id": "ev-1"},
             "predictions": [{"hypothesis_id": "one", "label": "split"},
                             {"hypothesis_id": "two", "label": "missing"}]}
    claim = {"text": passage, "rule_id": "VISIBLE_RULE", "evidence_ids": ["ev-1", "ev-2"],
             "calculation": {"amount": "9007199254740993.01"}, "assessment": "insufficient"}
    return {
        "framework": "CAS", "policy_version": "handwritten-v1", "period": "2026-09",
        "finding": {"record_ids": ["row-1", "row-2"], "evidence_ids": ["ev-1", "ev-2"]},
        "prior_proposals": [{"status": "insufficient", "claims": [claim], "hypotheses": [hypothesis],
                             "missing_evidence": [need], "evidence_plan": {"queries": [query]}}],
        "prior_proposal_index": [{"index": 0, "missing_evidence": [need]}],
        "unresolved_hypotheses": [hypothesis], "missing_evidence": [need, need],
        "evidence_selections": [{"selected_query": query, "candidates": [query, query],
                                 "hypotheses": [hypothesis], "execution": {"performed": False}}],
        "shared_evidence": {
            "records": [
                {"id": "row-1", "evidence_id": "ev-1", "kind": "journal", "amount": "100.00",
                 "description": passage, "reference": None},
                {"kind": "journal", "evidence_id": "ev-2", "id": "row-2", "amount": "0.00",
                 "reference": "", "description": passage},
                {"id": "row-3", "evidence_id": "ev-3", "kind": "receipt", "amount": "-0.01",
                 "description": passage},
                {"id": "row-4", "evidence_id": "ev-4", "kind": "receipt", "amount": "-0.01",
                 "description": passage, "reference": None},
            ],
            "query_results": [{"result": {"record_evidence_ids": ["ev-1", "ev-2"],
                                          "total": 2, "truncated": False}}],
            "rule_definitions": {"definition-0": {"rule_id": "VISIBLE_RULE", "conditions": passage}},
            "reference_passages": [{"source_id": "policy-1", "text": passage, "effective_to": None}],
            "program_observations": [request, request, check],
        },
        "untrusted_document": {"$ref": 0, "$records": [[0, 0]], "$object": [["fake", True]]},
        "literal_nested_markers": [{"$ref": 999}, {"$object": "plain data"}, {"$records": False}],
        "typed_values": [False, 0, None, "0", 0.0, -0.0, "中文原文不能截断"],
    }


def test_complete_context_roundtrip_preserves_evidence_checks_and_record_order():
    value = complex_context()
    original = deepcopy(value)
    packet = pack_context(value)
    decoded = unpack_context(json.loads(json.dumps(packet, ensure_ascii=False)))

    assert packet["format"] == FORMAT and packet["pool"]
    assert decoded == original and value == original
    assert [record["evidence_id"] for record in decoded["shared_evidence"]["records"]] == [
        "ev-1", "ev-2", "ev-3", "ev-4"]
    assert [list(record) for record in decoded["shared_evidence"]["records"]] == [
        list(record) for record in original["shared_evidence"]["records"]]
    assert len(packet["record_tables"]) == 2
    assert "reference" not in decoded["shared_evidence"]["records"][2]
    assert decoded["shared_evidence"]["records"][3]["reference"] is None
    observations = decoded["shared_evidence"]["program_observations"]
    assert len(observations) == 3 and observations[0] == observations[1]
    assert observations[0]["result"]["retrieved"] is False
    checks = observations[2]["result"]["claims"]
    assert checks[0]["calculations_verified"] is False and checks[1]["calculations_verified"] is None
    assert decoded["typed_values"][:4] == [False, 0, None, "0"]
    assert type(decoded["typed_values"][0]) is bool and type(decoded["typed_values"][1]) is int
    assert math.copysign(1, decoded["typed_values"][5]) == -1
    assert wire_size(packet) < wire_size(original)


def test_pools_are_stable_scoped_and_do_not_alias_original_or_decoded_objects():
    first = complex_context()
    packet = pack_context(first)
    assert pack_context(first) == packet
    second = {"snapshot": "other-snapshot", "shared_evidence": {"records": [
        {"id": "other-row", "evidence_id": "other-evidence", "amount": "1.00"}]}}
    other_packet = pack_context(second)
    assert "ev-1" not in json.dumps(other_packet)
    assert unpack_context(other_packet) == second
    decoded = unpack_context(packet)
    decoded["shared_evidence"]["program_observations"][0]["result"]["retrieved"] = True
    assert decoded["shared_evidence"]["program_observations"][1]["result"]["retrieved"] is False
    assert first["shared_evidence"]["program_observations"][0]["result"]["retrieved"] is False
    assert unpack_context(packet) == first


@pytest.mark.parametrize("value", [
    {}, {"shared_evidence": {"records": []}},
    {"shared_evidence": {"records": [{}, {}]}},
    {"shared_evidence": {"records": ["untyped documentary list", None]}},
    {"shared_evidence": {"records": {"$records": []}}},
    {"$ref": 0}, {"$object": [["$ref", 0]]},
])
def test_empty_and_marker_shaped_original_values_are_literal(value):
    assert unpack_context(pack_context(value)) == value


@pytest.mark.parametrize("reference", [-1, 0, 999, True, "0", None])
def test_fake_pool_references_are_rejected(reference):
    packet = pack_context({})
    packet["context"] = {"fake": {"$ref": reference}}
    with pytest.raises(ValueError, match="pool reference"):
        unpack_context(packet)


@pytest.mark.parametrize("pool", [[{"$ref": 0}], [{"$ref": 1}, "later"],
                                  [{"$ref": 1}, {"$ref": 0}]])
def test_self_forward_and_cyclic_pool_references_are_rejected(pool):
    packet = pack_context({})
    packet["pool"] = pool
    with pytest.raises(ValueError, match="pool reference"):
        unpack_context(packet)


def test_input_and_packet_cycles_and_non_json_types_are_rejected():
    value = {}
    value["loop"] = value
    with pytest.raises(ValueError, match="cycle"):
        pack_context(value)
    packet = pack_context({})
    packet["context"]["loop"] = packet
    with pytest.raises(ValueError, match="cycle"):
        unpack_context(packet)
    for invalid in ({1: "not a JSON key"}, {"invalid": float("nan")}, {"invalid": object()}):
        with pytest.raises(ValueError):
            pack_context(invalid)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(format="other-version"),
    lambda p: p.update(decode="forged instruction"),
    lambda p: p["context"].update(bad={"$ref": 0, "extra": True}),
    lambda p: p["context"].update(bad={"$records": [[0, 0]]}),
    lambda p: p["context"].update(bad={"$object": [["same", 1], ["same", 2]]}),
    lambda p: p["context"]["shared_evidence"]["records"]["$records"].pop(),
    lambda p: p["context"]["shared_evidence"]["records"]["$records"].append([0, 0]),
    lambda p: p["context"]["shared_evidence"]["records"]["$records"].append([999, 0]),
    lambda p: p["record_tables"][0]["columns"].append("extra-column"),
])
def test_malformed_marker_and_record_references_are_rejected(mutation):
    packet = pack_context({"shared_evidence": {"records": [
        {"id": "one", "evidence_id": "e-one"}, {"id": "two", "evidence_id": "e-two"}]}})
    mutation(packet)
    with pytest.raises(ValueError):
        unpack_context(packet)


def test_nesting_expansion_through_pool_references_is_bounded():
    packet = pack_context({})
    packet["pool"] = ["first"]
    for index in range(150):
        packet["pool"].append([{"$ref": index}])
    packet["context"] = {"nested": {"$ref": 150}}
    with pytest.raises(ValueError, match="nesting"):
        unpack_context(packet)


def test_small_packet_with_exponential_expansion_is_rejected_before_copying(monkeypatch):
    import app.context_transport as transport

    packet = pack_context({})
    packet["pool"] = ["long unchanged documentary sentence" * 4]
    for index in range(24):
        packet["pool"].append([{"$ref": index}, {"$ref": index}])
    packet["context"] = {"expanded": {"$ref": 24}}
    assert wire_size(packet) < 2_000

    def copying_would_be_too_late(_):
        pytest.fail("Expansion must be rejected before copying pool values")

    monkeypatch.setattr(transport, "deepcopy", copying_would_be_too_late)
    with pytest.raises(ValueError, match="byte/node bound"):
        unpack_context(packet)


def test_valid_long_fulltext_is_preserved_within_expansion_bounds():
    fulltext = "完整政策原文。" * 40_000
    value = {"shared_evidence": {"reference_passages": [{"source_id": "long-policy", "text": fulltext}]},
             "readable_prior_document": fulltext}
    decoded = deserialize_context(serialize_context(value))
    assert decoded == value and decoded["readable_prior_document"] == fulltext


def test_deep_overlapping_pools_fall_back_without_rejecting_valid_fulltext():
    document = "original full policy passage" * 4_000
    for _ in range(100):
        document = {"nested": document}
    value = {"first": document, "second": document}
    packet = pack_context(value)
    assert unpack_context(packet) == value
    assert deserialize_context(serialize_context(value)) == value


def test_serialization_uses_packet_only_when_compact_utf8_bytes_are_smaller():
    small = {"framework": "CAS", "shared_evidence": {"records": []}}
    plain = json.dumps(small, ensure_ascii=False, separators=(",", ":"))
    assert serialize_context(small) == plain
    assert deserialize_context(plain) == small
    large = complex_context()
    serialized = serialize_context(large)
    assert json.loads(serialized)["format"] == FORMAT
    assert len(serialized.encode()) < wire_size(large)
    assert deserialize_context(serialized) == large


def test_top_level_format_is_reserved_and_nested_formats_and_markers_remain_literal():
    with pytest.raises(ValueError, match="reserved format"):
        serialize_context({"format": "document-v1"})
    value = {"document": {"format": FORMAT, "$ref": 123, "$records": [],
                          "$object": {"format": "original"}}}
    serialized = serialize_context(value)
    assert "format" not in json.loads(serialized)
    assert deserialize_context(serialized) == value
    repeated = {"documents": [value["document"]] * 100}
    assert deserialize_context(serialize_context(repeated)) == repeated


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '[]', 'null'])
def test_deserialization_rejects_nonobjects_nonfinite_and_duplicate_keys(text):
    with pytest.raises(ValueError):
        deserialize_context(text)


def test_local_v16_observable_brief_sections_shrink_without_truth_or_reasoning():
    root = Path(__file__).resolve().parents[2]
    report_path = root / ".local/reports/deepseek-pilot-v16.json"
    if not report_path.exists():
        pytest.skip("Local v16 observable report is not available")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    tested = 0
    public_fields = {"id", "evidence_id", "source_id", "kind", "business_date", "period", "observed_at",
                     "currency", "counterparty", "reference", "order_id", "invoice_id", "voucher_id",
                     "account", "debit", "credit", "amount", "description"}
    claim_fields = {"text", "evidence_ids", "rule_id", "calculation", "policy_source_ids", "assessment"}
    for row in report["cases"]:
        if row["method"] not in {"multi", "active"} or not row["model_proposals"]:
            continue
        observable_path = root / ".local/datasets/observed" / f"{row['case_id']}.json"
        if not observable_path.exists():
            continue
        observable = json.loads(observable_path.read_text(encoding="utf-8"))
        # Never forward evaluator labels, causes, request bodies or private model
        # reasoning. This reproduces observable brief sections, not a captured
        # request; record/query acquisition itself is tested separately.
        latest = row["model_proposals"][-1]
        prior = {key: deepcopy(latest[key]) for key in ("status", "hypotheses", "missing_evidence")}
        prior["claims"] = [{key: deepcopy(value) for key, value in claim.items() if key in claim_fields}
                           for claim in latest["claims"]]
        if latest.get("evidence_plan") is not None:
            prior["evidence_plan"] = deepcopy(latest["evidence_plan"])
        context = {
            "framework": row["framework"], "prior_proposals": [prior],
            "prior_proposal_index": [{"index": index, "role": proposal["role"],
                                      "missing_evidence": proposal["missing_evidence"]}
                                     for index, proposal in enumerate(row["model_proposals"])],
            "unresolved_hypotheses": [hypothesis for proposal in row["model_proposals"]
                                      for hypothesis in proposal["hypotheses"]
                                      if hypothesis["status"] == "unresolved"],
            "missing_evidence": [need for proposal in row["model_proposals"]
                                 for need in proposal["missing_evidence"]],
            "evidence_selections": deepcopy(row["evidence_selections"]),
            "shared_evidence": {
                "records": [{key: value for key, value in record.items() if key in public_fields}
                            for record in observable["records"]],
                "program_observations": [], "reference_passages": [], "rule_definitions": {},
            },
        }
        original = deepcopy(context)
        packet = pack_context(context)
        assert unpack_context(packet) == original and context == original
        assert wire_size(packet) < wire_size(context)
        tested += 1
    assert tested == 12
