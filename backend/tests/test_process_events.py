"""Independent hand-authored process fixtures, never claimed as real BPI cases."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json

import pytest

from backend.app.process_events import (
    NUMERIC_FIELD, adapt_traces, classify_process, get_process_trace, quality_report,
    query_process_events, resolve_process_evidence,
)
from backend.app.process_store import (
    list_process_snapshots, load_process_snapshot, read_source_traces, save_process_snapshot,
)


OBSERVED_AT = datetime(2026, 10, 8, 5, 0, tzinfo=UTC)
RAW_HASH = "a" * 64


def event(activity, value="42.0", timestamp="2018-01-01T00:00:00.000Z", **fields):
    return {"concept:name": activity, "time:timestamp": timestamp, NUMERIC_FIELD: value,
            "User": "fixture-anonymous-user", "org:resource": "fixture-resource", **fields}


def trace(events=None, *, identity="fixture-only-item", category="3-way match, invoice before GR",
          goods="true", gr_based="false", item_type="Standard"):
    return {"case_id": identity, "source": "BPI2019", "accounting_framework": None, "complete_trace": True,
            "attributes": {"concept:name": identity, "Purchasing Document": "fixture-only-document",
                           "Item": "10", "Company": "fixture-company", "Source": "fixture-source",
                           "Item Category": category, "Item Type": item_type, "Goods Receipt": goods,
                           "GR-Based Inv. Verif.": gr_based},
            "events": events if events is not None else [event("Create Purchase Order Item")]}


def source_bytes(traces):
    return ("\n".join(json.dumps(t, ensure_ascii=False) for t in traces) + "\n").encode("utf-8")


def adapt(traces, *, observed_at=OBSERVED_AT, raw_hash=RAW_HASH):
    return adapt_traces(traces, raw_sha256=raw_hash,
        subset_sha256=hashlib.sha256(source_bytes(traces)).hexdigest(), observed_at=observed_at)


def scope(snapshot):
    return {"dataset_scope_id": snapshot.dataset.dataset_scope_id, "snapshot_id": snapshot.dataset.snapshot_id}


@pytest.mark.parametrize("category,goods,gr_based,item_type,expected", [
    ("3-way match, invoice before GR", "true", "false", "Standard", "three_way_invoice_before_gr_allowed"),
    ("3-way match, invoice after GR", "true", "true", "Service", "three_way_gr_based_invoice_verification"),
    ("2-way match", "false", "false", "Standard", "two_way_no_goods_receipt_required"),
    ("Consignment", "true", "false", "Consignment", "consignment_invoice_outside_item_process"),
])
def test_documented_source_policies_use_exact_flags(category, goods, gr_based, item_type, expected):
    policy = classify_process(trace(category=category, goods=goods, gr_based=gr_based,
                                    item_type=item_type)["attributes"])
    assert policy.status == "classified" and policy.classification == expected
    assert policy.goods_receipt_flag is (goods == "true")
    assert policy.gr_based_invoice_verification_flag is (gr_based == "true")


@pytest.mark.parametrize("flag", ["False", "TRUE", " false", "", "0"])
def test_unknown_boolean_literals_are_not_truthy(flag):
    snapshot = adapt([trace(gr_based=flag)])
    assert snapshot.traces[0].policy.status == "needs_review"
    assert snapshot.traces[0].policy.gr_based_invoice_verification_flag is None
    assert snapshot.traces[0].trace_attributes["GR-Based Inv. Verif."] == flag


def test_conflicting_and_unknown_classification_remains_undetermined():
    conflict = classify_process(trace(category="Consignment", item_type="Service")["attributes"])
    unknown = classify_process(trace(category="new source policy")["attributes"])
    assert conflict.status == "needs_review" and conflict.classification == "undetermined"
    assert unknown.status == "unsupported" and unknown.classification == "undetermined"


def test_consignment_and_independent_two_way_fixture_do_not_create_missing_document_claims():
    snapshot = adapt([
        trace([event("Create Purchase Order Item", "0"), event("Record Goods Receipt", "0.0")],
              identity="fixture-consignment", category="Consignment", item_type="Consignment"),
        trace([event("Create Purchase Order Item"), event("Record Invoice Receipt"), event("Clear Invoice")],
              identity="fixture-two-way", category="2-way match", goods="false"),
    ])
    assert snapshot.traces[0].observations["absent_recorded_stages"] == ["invoice_receipt", "clearing"]
    assert snapshot.traces[1].observations["absent_recorded_stages"] == ["goods_receipt"]
    assert all(t.business_completion == "not_determined" for t in snapshot.traces)
    assert all(t.observations["absence_interpretation"] == "not_a_missing_document_or_payment_diagnosis"
               for t in snapshot.traces)
    assert [snapshot.events[i].source_numeric_text for i in (0, 1)] == ["0", "0.0"]
    assert quality_report(snapshot)["numeric_literal_zero_count"] == 2


def test_allowing_early_invoice_does_not_require_it_and_repeated_stages_are_counts_only():
    snapshot = adapt([trace([
        event("Record Goods Receipt", "20.0", "2018-01-01T00:00:00Z"),
        event("Record Goods Receipt", "22.0", "2018-01-02T00:00:00Z"),
        event("Record Invoice Receipt", "42.0", "2018-01-03T00:00:00Z"),
        event("Record Invoice Receipt", "42.0", "2018-01-04T00:00:00Z"),
        event("Clear Invoice", "42.0", "2018-01-05T00:00:00Z"),
    ])])
    row = snapshot.traces[0]
    assert row.policy.status == "classified"
    assert row.observations["phase_counts"] == {"clearing": 1, "goods_receipt": 2, "invoice_receipt": 2}
    assert row.observations["first_recorded_goods_invoice_time_relation"] == (
        "first_recorded_goods_before_first_recorded_invoice")
    assert row.observations["amount_aggregation"] == "not_performed_semantics_not_sufficiently_established"
    assert not quality_report(snapshot)["amount_aggregation_performed"]


def test_vendor_invoice_service_and_positive_cancellation_keep_separate_meanings():
    raw = trace([event("Vendor creates invoice"), event("Record Invoice Receipt"),
                 event("Record Service Entry Sheet"), event("Cancel Goods Receipt"), event("Clear Invoice")])
    before = deepcopy(raw)
    snapshot = adapt([raw])
    assert raw == before
    assert [e.phase for e in snapshot.events] == ["vendor_invoice", "invoice_receipt", "service_acceptance",
                                                 "cancellation", "clearing"]
    assert snapshot.events[3].source_numeric_text == "42.0"
    assert snapshot.events[4].event_type == "invoice_clearing_recorded"
    serialized = snapshot.model_dump(mode="json")
    for absent in ("currency", "accounting_framework", "debit", "credit", "invoice_id", "voucher_id", "bank_id"):
        assert absent not in serialized["dataset"] and all(absent not in e for e in serialized["events"])


def test_old_times_are_kept_equal_times_do_not_prove_order_and_observed_time_is_separate():
    snapshot = adapt([trace([event("Vendor creates invoice", timestamp="2001-02-23T08:09:10.111Z"),
                            event("Record Goods Receipt"), event("Record Invoice Receipt")])])
    assert snapshot.events[0].occurred_at.year == 2001
    assert snapshot.events[0].occurred_at_raw == "2001-02-23T08:09:10.111Z"
    assert snapshot.events[0].observed_at.year == 2026
    assert [e.source_event_ordinal for e in snapshot.events] == [1, 2, 3]
    assert snapshot.events[1].same_timestamp_event_ordinals == (2, 3)
    assert snapshot.events[2].causal_order_status == "same_timestamp_undetermined"
    assert snapshot.traces[0].observations["first_recorded_goods_invoice_time_relation"] == "same_timestamp_undetermined"


def test_unknown_activity_invalid_and_missing_values_are_preserved_with_quality_issues():
    raw_event = event(" Future activity ", "NaN", "2018-01-01T00:00:00")
    second = {"concept:name": "Create Purchase Order Item"}
    snapshot = adapt([trace([raw_event, second])])
    assert snapshot.events[0].activity_raw == " Future activity "
    assert snapshot.events[0].mapping_status == "unsupported"
    assert snapshot.events[0].event_attributes == raw_event
    assert snapshot.events[0].occurred_at is None
    assert snapshot.events[0].source_numeric_text == "NaN"
    assert snapshot.events[0].source_numeric_status == "invalid"
    assert snapshot.events[1].source_numeric_text is None
    assert snapshot.events[1].source_numeric_status == "missing"
    assert quality_report(snapshot)["status"] == "adapted_with_quality_issues"


def test_generated_id_is_stable_but_evidence_changes_with_source_subset_snapshot():
    first = adapt([trace()])
    second = adapt([trace()], observed_at=datetime(2026, 10, 9, tzinfo=UTC))
    revised = adapt([trace([event("Create Purchase Order Item", "43.0")])])
    assert first.dataset.snapshot_id == second.dataset.snapshot_id
    assert first.events[0].source_event_id == revised.events[0].source_event_id
    assert first.events[0].evidence_id != revised.events[0].evidence_id
    assert first.events[0].event_id_kind == "adapter_generated_not_source_native"
    assert first.events[0].relations[0].basis == "source_declared"
    assert first.events[0].relations[0].object_id == first.traces[0].purchase_item_id
    assert first.traces[0].relations[0].predicate == "purchase_item_belongs_to_purchase_document"


def test_every_locator_points_to_original_jsonl_fields_including_json_pointer_escapes():
    originals = [trace([event("Create Purchase Order Item", **{"fixture/key~": " raw  "})]),
                 trace([event("Record Invoice Receipt", "0.00")], identity="second")]
    snapshot = adapt(originals)
    for item in (*snapshot.traces, *snapshot.events):
        raw = originals[item.locator.jsonl_line - 1]
        assert raw["case_id"] == item.locator.source_trace_id
        fields = item.event_attributes if hasattr(item, "event_attributes") else item.trace_attributes
        for key, pointer in item.locator.field_pointers.items():
            found = raw
            for part in pointer.strip("/").split("/"):
                unescaped = part.replace("~1", "/").replace("~0", "~")
                found = found[int(unescaped)] if isinstance(found, list) else found[unescaped]
            assert found == fields[key]


def test_fixed_scope_queries_are_bounded_and_cross_dataset_snapshot_or_invented_evidence_rejected():
    snapshot = adapt([trace([event("Record Goods Receipt"), event("Record Invoice Receipt")])])
    query = query_process_events(snapshot, **scope(snapshot), limit=1)
    assert query["total"] == 2 and query["next_offset"] == 1 and len(query["items"]) == 1
    page2 = query_process_events(snapshot, **scope(snapshot), limit=1, offset=1)
    assert page2["next_offset"] is None and page2["items"][0]["event_type"] == "invoice_receipt_recorded"
    for kwargs in ({"dataset_scope_id": "ledger-book", "snapshot_id": snapshot.dataset.snapshot_id},
                   {"dataset_scope_id": snapshot.dataset.dataset_scope_id, "snapshot_id": "old"}):
        with pytest.raises(ValueError, match="scope mismatch"):
            query_process_events(snapshot, **kwargs)
    for limit in (0, 201, True):
        with pytest.raises(ValueError, match="Limit"):
            query_process_events(snapshot, **scope(snapshot), limit=limit)
    with pytest.raises(KeyError):
        resolve_process_evidence(snapshot, **scope(snapshot), evidence_id="invented")
    revised = adapt([trace([event("Record Goods Receipt", "100")])])
    with pytest.raises(KeyError):
        resolve_process_evidence(revised, **scope(revised), evidence_id=snapshot.events[0].evidence_id)
    result = get_process_trace(snapshot, **scope(snapshot), source_trace_id="fixture-only-item", limit=1)
    assert result["trace"]["source_trace_id"] == "fixture-only-item" and result["events"]["total"] == 2


def test_query_time_filters_preserve_utc_and_reject_naive_dates():
    snapshot = adapt([trace([event("Record Goods Receipt", timestamp="2018-01-01T01:00:00+01:00"),
                            event("Record Invoice Receipt", timestamp="2018-01-02T00:00:00Z")])])
    result = query_process_events(snapshot, **scope(snapshot), occurred_to=datetime(2018, 1, 1, tzinfo=UTC))
    assert result["total"] == 1 and result["items"][0]["occurred_at_raw"] == "2018-01-01T01:00:00+01:00"
    with pytest.raises(ValueError, match="timezone"):
        query_process_events(snapshot, **scope(snapshot), occurred_from=datetime(2018, 1, 1))


@pytest.mark.parametrize("change", ["incomplete", "foreign", "identity", "framework", "nonliteral"])
def test_invalid_source_structure_fails_without_fabrication(change):
    row = trace()
    if change == "incomplete":
        row["complete_trace"] = False
    elif change == "foreign":
        row["source"] = "other"
    elif change == "identity":
        row["case_id"] = "different"
    elif change == "framework":
        row["accounting_framework"] = "CAS"
    else:
        row["events"][0][NUMERIC_FIELD] = 0.0
    with pytest.raises(ValueError):
        adapt([row])


def test_duplicate_trace_id_and_naive_observed_time_rejected():
    with pytest.raises(ValueError, match="unique"):
        adapt([trace(), trace()])
    with pytest.raises(ValueError, match="timezone"):
        adapt([trace()], observed_at=datetime(2026, 10, 8))


def saved_fixture(tmp_path):
    raw = [trace([event("Create Purchase Order Item"), event("Record Invoice Receipt")])]
    source = tmp_path / "input.jsonl"
    source.write_bytes(source_bytes(raw))
    snapshot = adapt(raw)
    root = tmp_path / "store"
    saved = save_process_snapshot(root, snapshot, source)
    return root, source, snapshot, saved


def test_snapshot_persistence_is_idempotent_and_keeps_first_observation_time(tmp_path):
    root, source, snapshot, saved = saved_fixture(tmp_path)
    assert saved["status"] == "created"
    assert load_process_snapshot(root, snapshot.dataset.snapshot_id) == snapshot
    later = adapt(read_source_traces(source.read_bytes()), observed_at=datetime(2026, 10, 9, tzinfo=UTC))
    reused = save_process_snapshot(root, later, source)
    assert reused["status"] == "existing_verified"
    assert load_process_snapshot(root, snapshot.dataset.snapshot_id).dataset.observed_at == OBSERVED_AT
    manifests = list_process_snapshots(root)
    assert len(manifests) == 1 and manifests[0]["event_count"] == 2 and manifests[0]["title"]


@pytest.mark.parametrize("filename", ["snapshot.json", "source.jsonl", "quality-report.json"])
def test_changed_stored_bytes_are_rejected_not_silently_repaired(tmp_path, filename):
    root, _, snapshot, _ = saved_fixture(tmp_path)
    path = root / snapshot.dataset.snapshot_id / filename
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_process_snapshot(root, snapshot.dataset.snapshot_id)
    with pytest.raises(ValueError, match="hash mismatch"):
        list_process_snapshots(root)


def test_rehashed_self_report_cannot_forge_event_values_or_evidence_membership(tmp_path):
    root, _, snapshot, _ = saved_fixture(tmp_path)
    directory = root / snapshot.dataset.snapshot_id
    payload = json.loads((directory / "snapshot.json").read_bytes())
    payload["events"][0]["source_numeric_text"] = "999.0"
    changed = json.dumps(payload).encode()
    (directory / "snapshot.json").write_bytes(changed)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["snapshot_sha256"] = hashlib.sha256(changed).hexdigest()
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="replayed"):
        load_process_snapshot(root, snapshot.dataset.snapshot_id)


@pytest.mark.parametrize("identifier", ["../snapshot.json", "process_snapshot_../outside", "", "a" * 64])
def test_store_rejects_untrusted_path_identifiers(tmp_path, identifier):
    with pytest.raises(ValueError, match="identifier"):
        load_process_snapshot(tmp_path, identifier)


def test_jsonl_blank_lines_cannot_shift_source_locators():
    with pytest.raises(ValueError, match="blank lines"):
        read_source_traces(b'{}\n\n{}\n')


def test_offline_cli_stops_before_write_when_original_verification_fails(tmp_path, monkeypatch):
    from datasets import bpi2019_adapter

    def failed(*args):
        raise ValueError("fixture original MD5 mismatch")

    monkeypatch.setattr(bpi2019_adapter, "verify_local", failed)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="MD5"):
        bpi2019_adapter.import_verified_subset(tmp_path, tmp_path / "coverage.json", output)
    assert not output.exists()


def test_verified_cache_skips_only_replay_of_byte_identical_files(tmp_path, monkeypatch):
    from backend.app import process_store
    root, _, snapshot, _ = saved_fixture(tmp_path)
    first = load_process_snapshot(root, snapshot.dataset.snapshot_id)
    calls = []
    monkeypatch.setattr(process_store, "_verify_replay", lambda *args: calls.append(args) or first)
    assert load_process_snapshot(root, snapshot.dataset.snapshot_id) is first
    assert calls == []


@pytest.mark.parametrize("filename", ["snapshot.json", "source.jsonl", "quality-report.json"])
def test_warm_cache_still_rejects_later_byte_changes(tmp_path, filename):
    root, _, snapshot, _ = saved_fixture(tmp_path)
    load_process_snapshot(root, snapshot.dataset.snapshot_id)
    path = root / snapshot.dataset.snapshot_id / filename
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_process_snapshot(root, snapshot.dataset.snapshot_id)


def test_warm_cache_does_not_accept_a_rehashed_forgery(tmp_path):
    root, _, snapshot, _ = saved_fixture(tmp_path)
    load_process_snapshot(root, snapshot.dataset.snapshot_id)
    directory = root / snapshot.dataset.snapshot_id
    payload = json.loads((directory / "snapshot.json").read_bytes())
    payload["events"][0]["source_numeric_text"] = "999.0"
    changed = json.dumps(payload).encode()
    (directory / "snapshot.json").write_bytes(changed)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["snapshot_sha256"] = hashlib.sha256(changed).hexdigest()
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="replayed"):
        load_process_snapshot(root, snapshot.dataset.snapshot_id)
