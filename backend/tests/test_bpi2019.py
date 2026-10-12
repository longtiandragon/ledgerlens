"""Independent metadata, transfer and complete-trace boundaries for BPI2019."""
import hashlib
import json

import httpx
import pytest

from datasets.bpi2019 import (
    ARTICLE_ID, DOI, EXPECTED_BYTES, EXPECTED_MD5, FILE_ID, FILE_NAME, METADATA_URLS,
    collect, download_file, file_hashes, read_metadata, trace_statistics, validate_metadata, verify_local, write_subset,
)


def metadata(**changes):
    value = {"id": ARTICLE_ID, "title": "BPI Challenge 2019", "doi": DOI,
             "license": {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
             "files": [{"id": FILE_ID, "name": FILE_NAME, "size": EXPECTED_BYTES,
                        "computed_md5": EXPECTED_MD5, "is_link_only": False, "is_incomplete": False,
                        "download_url": f"https://ndownloader.figshare.com/files/{FILE_ID}"}]}
    value.update(changes)
    return value


def transfer_metadata(data):
    return {"expected_bytes": len(data), "expected_md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
            "download_url": f"https://ndownloader.figshare.com/files/{FILE_ID}"}


def test_official_metadata_identity_and_license():
    result = validate_metadata(metadata())
    assert result["doi"] == DOI
    assert result["expected_bytes"] == 728558522
    assert result["expected_md5"] == "4eb909242351193a61e1c15b9c3cc814"
    assert result["license"] == {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"}


@pytest.mark.parametrize("field,value", [("id", 1), ("title", "Other log"), ("doi", "10.9999/other"),
                                       ("license", {"name": "unknown", "url": None})])
def test_metadata_rejects_changed_identity_or_license(field, value):
    with pytest.raises(ValueError):
        validate_metadata(metadata(**{field: value}))


@pytest.mark.parametrize("field,value", [
    ("size", 10), ("size", True), ("computed_md5", "0" * 32), ("id", 1), ("name", "../other.xes"),
    ("is_incomplete", True), ("is_link_only", True), ("download_url", "https://example.org/log.xes"),
    ("download_url", "https://secret@ndownloader.figshare.com/files/24072995"),
    ("download_url", "https://ndownloader.figshare.com/files/1"),
])
def test_metadata_rejects_changed_source_file(field, value):
    value_to_test = metadata()
    value_to_test["files"][0][field] = value
    with pytest.raises(ValueError):
        validate_metadata(value_to_test)


def test_metadata_requires_one_unambiguous_file():
    value = metadata()
    value["files"].append(dict(value["files"][0]))
    with pytest.raises(ValueError, match="ambiguous"):
        validate_metadata(value)


def test_figshare_missing_completion_flag_remains_unknown_not_false():
    value = metadata()
    del value["files"][0]["is_incomplete"]
    assert validate_metadata(value)["published_file_complete"] is None


def test_metadata_fetch_checks_bounded_official_endpoint():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=metadata())))
    value, raw = read_metadata(client, METADATA_URLS[0])
    assert value["expected_md5"] == EXPECTED_MD5 and json.loads(raw)["id"] == ARTICLE_ID
    with pytest.raises(ValueError, match="Unknown official"):
        read_metadata(client, "https://example.org/article")


def test_file_hashes_have_independently_known_values(tmp_path):
    path = tmp_path / "bytes"
    path.write_bytes(b"abc")
    assert file_hashes(path) == {"bytes": 3, "md5": "900150983cd24fb0d6963f7d28e17f72",
                                "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}


def test_download_verified_only_after_complete_size_and_checksum(tmp_path):
    data = b"real fixture bytes"
    report, saved = {}, []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data)))
    result = download_file(client, transfer_metadata(data), tmp_path, report,
                           lambda: saved.append(json.loads(json.dumps(report))))
    assert result.read_bytes() == data
    assert report["download"]["status"] == "verified"
    assert report["download"]["md5_matches_official"] is True
    assert saved[0]["download"]["status"] == "downloading"
    assert not result.with_suffix(".xes.part").exists()


def test_download_checksum_failure_keeps_partial_and_failed_state(tmp_path):
    data = b"expected"
    report = {}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"wrong___")))
    with pytest.raises(ValueError, match="MD5"):
        download_file(client, transfer_metadata(data), tmp_path, report, lambda: None)
    assert report["download"]["status"] == "failed"
    assert report["download"]["bytes"] == 8
    assert report["download"]["md5_matches_official"] is False
    assert not (tmp_path / FILE_NAME).exists()


def test_download_enforces_stream_limit_without_false_success(tmp_path):
    report = {}
    def oversized(request):
        # Omit Content-Length to exercise the streamed upper bound itself.
        return httpx.Response(200, stream=httpx.ByteStream(b"123456"))
    client = httpx.Client(transport=httpx.MockTransport(oversized))
    with pytest.raises(ValueError, match="byte limit"):
        download_file(client, transfer_metadata(b"12345"), tmp_path, report, lambda: None, max_bytes=5)
    assert report["download"]["status"] == "failed"
    assert report["download"]["md5_matches_official"] is False


def test_download_published_size_rejected_before_request(tmp_path):
    requested = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: requested.append(request)))
    with pytest.raises(ValueError, match="configured"):
        download_file(client, transfer_metadata(b"abc"), tmp_path, {}, lambda: None, max_bytes=2)
    assert requested == []


def test_resume_requires_exact_remaining_range(tmp_path):
    data = b"abcdefgh"
    (tmp_path / (FILE_NAME + ".part")).write_bytes(data[:3])
    requested = []
    def remaining(request):
        requested.append(request.headers["range"])
        return httpx.Response(206, headers={"Content-Range": "bytes 3-7/8"}, content=data[3:])
    client = httpx.Client(transport=httpx.MockTransport(remaining))
    report = {}
    path = download_file(client, transfer_metadata(data), tmp_path, report, lambda: None)
    assert requested == ["bytes=3-"]
    assert path.read_bytes() == data and report["download"]["status"] == "verified"


def test_wrong_resume_range_preserves_bytes_as_failure(tmp_path):
    (tmp_path / (FILE_NAME + ".part")).write_bytes(b"abc")
    client = httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(206, headers={"Content-Range": "bytes 0-7/8"}, content=b"abcdefgh")))
    report = {}
    with pytest.raises(ValueError, match="range"):
        download_file(client, transfer_metadata(b"abcdefgh"), tmp_path, report, lambda: None)
    assert report["download"]["bytes"] == 3 and report["download"]["status"] == "failed"


def test_server_ignoring_range_restarts_without_duplicating_prefix(tmp_path):
    data = b"abcdefgh"
    (tmp_path / (FILE_NAME + ".part")).write_bytes(b"abc")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data)))
    path = download_file(client, transfer_metadata(data), tmp_path, {}, lambda: None)
    assert path.read_bytes() == data


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"first"
        raise httpx.ReadError("fixture disconnect")


def test_midstream_network_failure_reports_written_bytes(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=BrokenStream())))
    report = {}
    with pytest.raises(httpx.ReadError):
        download_file(client, transfer_metadata(b"first___"), tmp_path, report, lambda: None)
    # Chunk aggregation can buffer sub-megabyte transfer data before error;
    # report the actual part file bytes, never the provider's declared size.
    assert report["download"]["bytes"] == (tmp_path / (FILE_NAME + ".part")).stat().st_size
    assert report["download"]["status"] == "failed" and not (tmp_path / FILE_NAME).exists()


def test_complete_trace_subset_preserves_all_events_and_source_strings(tmp_path):
    path = tmp_path / "source.xes"
    path.write_text('''<log xmlns="http://www.xes-standard.org/">
      <trace><string key="concept:name" value="purchase-A"/><string key="Vendor" value="V001"/>
        <event><string key="concept:name" value="Create Purchase Order Item"/><float key="Cumulative net worth (EUR)" value="1.10"/></event>
        <event><string key="concept:name" value="Record Goods Receipt"/></event>
        <event><string key="concept:name" value="Clear Invoice"/></event>
      </trace>
      <trace><string key="concept:name" value="purchase-B"/>
        <event><string key="concept:name" value="Create Purchase Order Item"/></event>
        <event><string key="concept:name" value="Record Invoice Receipt"/></event>
      </trace>
      <trace><string key="concept:name" value="purchase-C"/><event>''', encoding="utf-8")
    output = tmp_path / "subset.jsonl"
    report = write_subset(path, output, 2)
    traces = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [t["case_id"] for t in traces] == ["purchase-A", "purchase-B"]
    assert [e["concept:name"] for e in traces[0]["events"]] == [
        "Create Purchase Order Item", "Record Goods Receipt", "Clear Invoice"]
    assert traces[0]["events"][0]["Cumulative net worth (EUR)"] == "1.10"
    assert all(t["complete_trace"] is True and t["accounting_framework"] is None for t in traces)
    assert report["complete_traces"] == 2 and report["events"] == 5
    assert report["minimum_events_per_trace"] == 2 and report["maximum_events_per_trace"] == 3
    assert report["currency_inferred"] is False and report["anomaly_truth_available"] is False


def test_source_with_too_few_traces_is_not_a_complete_requested_subset(tmp_path):
    path = tmp_path / "source.xes"
    path.write_text('<log><trace><string key="concept:name" value="a"/></trace></log>', encoding="utf-8")
    with pytest.raises(ValueError, match="requested number"):
        write_subset(path, tmp_path / "subset.jsonl", 2)
    assert not (tmp_path / "subset.jsonl").exists()


@pytest.mark.parametrize("limit", [0, -1, 10001, True])
def test_invalid_trace_limit_never_silently_selects_one(limit):
    with pytest.raises(ValueError, match="Trace limit"):
        trace_statistics([], limit)


def test_metadata_failure_report_has_no_false_subset(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    report = collect(tmp_path / "local", tmp_path / "coverage.json", client=client)
    assert report["status"] == "failed" and report["subset"] is None
    assert len(report["metadata_sources"]) == 2
    assert all(m["status"] == "failed" for m in report["metadata_sources"])
    assert all(m["http_status"] == 503 for m in report["metadata_sources"])
    attempts = list((tmp_path / "local" / "attempts").glob("*.json"))
    assert len(attempts) == 1
    assert json.loads(attempts[0].read_text(encoding="utf-8"))["status"] == "failed"


@pytest.fixture
def independently_authored_local_corpus(tmp_path, monkeypatch):
    import datasets.bpi2019 as module
    raw = b'''<log><trace><string key="concept:name" value="A"/>
    <event><string key="concept:name" value="Create"/></event>
    <event><string key="concept:name" value="Receive"/></event></trace>
    <trace><string key="concept:name" value="B"/>
    <event><string key="concept:name" value="Clear"/></event></trace></log>'''
    md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    monkeypatch.setattr(module, "EXPECTED_BYTES", len(raw))
    monkeypatch.setattr(module, "EXPECTED_MD5", md5)
    (tmp_path / FILE_NAME).write_bytes(raw)
    official = metadata()
    official["files"][0].update(size=len(raw), computed_md5=md5)
    official_bytes = json.dumps(official).encode()
    (tmp_path / "metadata-4tu.json").write_bytes(official_bytes)
    traces = [{"case_id": "A", "attributes": {"concept:name": "A"},
               "events": [{"concept:name": "Create"}, {"concept:name": "Receive"}],
               "source": "BPI2019", "accounting_framework": None, "complete_trace": True},
              {"case_id": "B", "attributes": {"concept:name": "B"}, "events": [{"concept:name": "Clear"}],
               "source": "BPI2019", "accounting_framework": None, "complete_trace": True}]
    subset = "".join(json.dumps(trace) + "\n" for trace in traces).encode()
    (tmp_path / "subset.jsonl").write_bytes(subset)
    statistics = {"complete_traces": 2, "events": 3, "minimum_events_per_trace": 1, "maximum_events_per_trace": 2,
                  "selection": "first_complete_traces_in_source_order", "requested_traces": 2,
                  "accounting_framework": None, "currency_inferred": False, "journals_inferred": False,
                  "anomaly_truth_available": False}
    coverage = {"schema_version": module.SCHEMA_VERSION, "status": "collected",
                "metadata_sources": [{"url": METADATA_URLS[0], "status": "fetched",
                                      "sha256": hashlib.sha256(official_bytes).hexdigest()}],
                "download": {"sha256": hashlib.sha256(raw).hexdigest()},
                "subset": {**statistics, "bytes": len(subset), "sha256": hashlib.sha256(subset).hexdigest(),
                           "md5": hashlib.md5(subset, usedforsecurity=False).hexdigest()}}
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    return tmp_path, coverage_path, coverage, traces


def test_verify_only_checks_independent_complete_source_prefix(independently_authored_local_corpus):
    directory, coverage, _, _ = independently_authored_local_corpus
    result = verify_local(directory, coverage)
    assert result["status"] == "verified" and result["raw_md5_matches_official"] is True
    assert result["complete_traces"] == 2 and result["events"] == 3


@pytest.mark.parametrize("target", ["metadata-4tu.json", FILE_NAME, "subset.jsonl"])
def test_verify_only_rejects_changed_files(independently_authored_local_corpus, target):
    directory, coverage, _, _ = independently_authored_local_corpus
    with (directory / target).open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(ValueError):
        verify_local(directory, coverage)


def test_valid_hashes_do_not_hide_missing_event_or_changed_trace_order(independently_authored_local_corpus):
    directory, coverage_path, coverage, traces = independently_authored_local_corpus
    # Re-hash a tampered subset to test the source-prefix comparison, rather
    # than only the basic checksum guard. Source itself remains unchanged.
    traces.reverse()
    traces[1]["events"].pop()
    changed = "".join(json.dumps(trace) + "\n" for trace in traces).encode()
    (directory / "subset.jsonl").write_bytes(changed)
    coverage["subset"].update(bytes=len(changed), sha256=hashlib.sha256(changed).hexdigest(),
                              md5=hashlib.md5(changed, usedforsecurity=False).hexdigest())
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly preserve"):
        verify_local(directory, coverage_path)
