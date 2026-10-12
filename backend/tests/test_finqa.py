"""Independent fixture values test acquisition boundaries, not FinQA accuracy."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.knowledge import build_corpus
from datasets.finqa import (DEV_URL, LICENSE_URL, MAX_BYTES, collect, coverage, fetch,
                            json_bytes, load_observed, main, project_dev, read_json, verify, write_json)


LICENSE = ("MIT License\nCopyright (c) 2021 Zhiyu Chen\n"
           "The above copyright notice and this permission notice shall be included\n").encode()


def record():
    return {"id": "EXAMPLE/2021/page_4.pdf-0", "pre_text": ["Revenue in 2021 was 30."],
            "post_text": ["Revenue in 2020 was 20."], "table": [["Year", "Revenue"], ["2021", "30"]],
            "qa": {"question": "What is the difference?", "program": "subtract(30, 20)",
                   "program_re": "subtract(30, 20)", "exe_ans": 10,
                   "answer": "EVALUATOR_ANSWER_SENTINEL", "explanation": "EVALUATOR_EXPLANATION_SENTINEL",
                   "gold_inds": {"text_0": "EVALUATOR_GOLD_SENTINEL"},
                   "model_input": ["EVALUATOR_MODEL_INPUT_SENTINEL"], "tfidftopn": {"gold": "LABEL"}},
            "table_ori": [["Other original field"]],
            "text_retrieved": ["RETRIEVED_SUBSET_SENTINEL"],
            "filename": "EXAMPLE/2021/page_4.pdf"}


def raw_data(records=None):
    return json_bytes(records if records is not None else [record()])


def transport(data=None, license_data=LICENSE):
    data = raw_data() if data is None else data

    def handler(request):
        assert str(request.url) in {DEV_URL, LICENSE_URL}
        return httpx.Response(200, content=data if str(request.url) == DEV_URL else license_data)

    return httpx.MockTransport(handler)


def collected(tmp_path):
    output = tmp_path / "local"
    report = collect(output, transport=transport())
    assert report["status"] == "collected"
    return output, report


def test_projection_uses_all_source_text_and_table_without_gold_selection():
    observed, evaluator = project_dev(raw_data())
    [case] = observed["records"]
    assert case["question"] == "What is the difference?"
    assert case["pre_text"] == ["Revenue in 2021 was 30."]
    assert case["post_text"] == ["Revenue in 2020 was 20."]
    assert case["table"] == [["Year", "Revenue"], ["2021", "30"]]
    assert case["locators"]["table"] == "$[0].table"
    assert set(case) == {"id", "question", "pre_text", "post_text", "table", "locators"}
    encoded = json.dumps(observed)
    for marker in ["EVALUATOR_ANSWER_SENTINEL", "EVALUATOR_EXPLANATION_SENTINEL", "EVALUATOR_GOLD_SENTINEL",
                   "EVALUATOR_MODEL_INPUT_SENTINEL", "RETRIEVED_SUBSET_SENTINEL", "subtract(30, 20)"]:
        assert marker not in encoded
        assert marker in json.dumps(evaluator)
    assert "question" not in evaluator["records"][0]["qa"]


def test_decimal_answer_keeps_original_lexeme_without_float_rounding():
    data = raw_data().replace(b'"exe_ans": 10', b'"exe_ans": 0.100000000000000000001')
    assert read_json(data)[0]["qa"]["exe_ans"] == Decimal("0.100000000000000000001")
    observed, evaluator = project_dev(data)
    assert evaluator["records"][0]["qa"]["exe_ans"] == "0.100000000000000000001"
    assert "0.100000000000000000001" not in json.dumps(observed)


def test_duplicate_json_key_cannot_change_an_answer_or_question_silently():
    data = raw_data().replace(b'"exe_ans": 10', b'"exe_ans": 10, "exe_ans": 999')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        project_dev(data)


@pytest.mark.parametrize("data", [b"", b"[]", b"{}", b"null", b"[null]", b"bad json", b"\xff"])
def test_missing_corrupt_or_wrong_top_level_data_is_not_usable(data):
    with pytest.raises((ValueError, UnicodeError)):
        project_dev(data)


@pytest.mark.parametrize("mutation", [
    {"id": ""}, {"pre_text": "not a list"}, {"post_text": [12]},
    {"table": []}, {"table": [["A", "B"], ["ragged"]]}, {"table": [["A", 12]]},
])
def test_invalid_observable_fields_are_not_repaired_by_default(mutation):
    with pytest.raises(ValueError):
        project_dev(raw_data([{**record(), **mutation}]))


@pytest.mark.parametrize("field,value", [
    ("question", ""), ("program", ""), ("program_re", None), ("gold_inds", []),
    ("exe_ans", True), ("exe_ans", None), ("exe_ans", ""),
])
def test_missing_or_invalid_evaluator_contract_is_not_a_successful_dev_task(field, value):
    fixture = record()
    fixture["qa"][field] = value
    with pytest.raises(ValueError):
        project_dev(raw_data([fixture]))


def test_private_test_question_only_shape_is_rejected():
    fixture = record()
    fixture["qa"] = {"question": "Question-only private test fixture"}
    with pytest.raises(ValueError, match="evaluator fields"):
        project_dev(raw_data([fixture]))


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_finite_json_numbers_are_rejected(number):
    with pytest.raises(ValueError, match="Non-finite"):
        project_dev(raw_data().replace(b'"exe_ans": 10', b'"exe_ans": ' + number))


def test_same_example_id_cannot_inflate_data_count():
    with pytest.raises(ValueError, match="Duplicate FinQA"):
        project_dev(raw_data([record(), deepcopy(record())]))


def test_real_style_id_is_not_used_as_a_filesystem_path(tmp_path):
    fixture = record()
    fixture["id"] = "../../EXAMPLE/page.pdf-0"
    output = tmp_path / "local"
    report = collect(output, transport=transport(raw_data([fixture])))
    assert report["status"] == "collected"
    assert load_observed(Path(report["observed"]["path"]))["records"][0]["id"] == fixture["id"]
    assert not (tmp_path / "EXAMPLE").exists()


def test_successful_collection_retains_original_hash_and_excludes_answers_from_knowledge(tmp_path):
    output, report = collected(tmp_path)
    assert Path(report["sources"][0]["raw_path"]).read_bytes() == raw_data()
    assert report["sources"][0]["sha256"] == hashlib.sha256(raw_data()).hexdigest()
    assert report["sources"][1]["sha256"] == hashlib.sha256(LICENSE).hexdigest()
    result = verify(output)
    assert result["status"] == "verified" and result["records"] == 1
    assert result["accounting_validation"] is None
    assert build_corpus(output / "knowledge-source-manifest.json", tmp_path / "corpus.json") == []
    public = coverage(report, result)
    assert public["runtime_policy_knowledge_registered"] is False
    assert public["component_accuracy_measured"] is None
    assert public["accounting_investigation_accuracy_measured"] is None
    assert "raw_path" not in json.dumps(public)
    assert "EVALUATOR_ANSWER_SENTINEL" not in json.dumps(public)


@pytest.mark.parametrize("extra", ["answer", "qa", "program", "gold_inds", "raw_path", "model_input"])
def test_runtime_loader_rejects_evaluator_or_unknown_fields(tmp_path, extra):
    observed, _ = project_dev(raw_data())
    observed["records"][0][extra] = "MUST_NOT_ENTER_RUNTIME"
    path = tmp_path / "observed.json"
    write_json(path, observed)
    with pytest.raises(ValueError, match="evaluator|unknown"):
        load_observed(path)


def test_runtime_loader_rejects_raw_and_evaluator_files(tmp_path):
    output, report = collected(tmp_path)
    for path in [report["sources"][0]["raw_path"], report["evaluator"]["path"]]:
        with pytest.raises(ValueError, match="evaluator"):
            load_observed(Path(path))


def test_runtime_loader_rejects_wrong_source_locator(tmp_path):
    observed, _ = project_dev(raw_data())
    observed["records"][0]["locators"]["question"] = "$[0].qa.answer"
    path = tmp_path / "observed.json"
    write_json(path, observed)
    with pytest.raises(ValueError, match="locator"):
        load_observed(path)


def test_runtime_loader_rejects_non_string_source_hash(tmp_path):
    observed, _ = project_dev(raw_data())
    observed["source_sha256"] = 123
    path = tmp_path / "observed.json"
    write_json(path, observed)
    with pytest.raises(ValueError, match="provenance"):
        load_observed(path)


@pytest.mark.parametrize("artifact", ["observed", "evaluator", "dataset", "license"])
def test_verify_detects_changed_original_and_projected_files(tmp_path, artifact):
    output, report = collected(tmp_path)
    item = (report[artifact] if artifact in {"observed", "evaluator"}
            else next(s for s in report["sources"] if s["role"] == artifact))
    path = Path(item.get("path", item.get("raw_path")))
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash"):
        verify(output)


def test_rehashing_corrupt_projection_cannot_make_it_match_the_source(tmp_path):
    output, report = collected(tmp_path)
    path = Path(report["observed"]["path"])
    observed = read_json(path.read_bytes())
    observed["records"][0]["table"][1][1] = "300"
    report["observed"]["sha256"] = write_json(path, observed)
    write_json(output / "acquisition-report.json", report)
    with pytest.raises(ValueError, match="immutable"):
        verify(output)


def test_verify_cli_refreshes_failed_status_without_overwriting_acquisition(tmp_path, monkeypatch, capsys):
    output, report = collected(tmp_path)
    original_report = (output / "acquisition-report.json").read_bytes()
    Path(report["observed"]["path"]).write_bytes(b"{}")
    public = tmp_path / "coverage.json"
    monkeypatch.setattr("sys.argv", ["finqa", "--verify-only", "--output", str(output), "--coverage", str(public)])
    assert main() == 1
    assert (output / "acquisition-report.json").read_bytes() == original_report
    assert read_json(public.read_bytes())["status"] == "verification_failed"
    assert read_json((output / "verification-report.json").read_bytes())["status"] == "failed"
    assert "EVALUATOR_ANSWER_SENTINEL" not in capsys.readouterr().out


def test_declared_artifact_cannot_escape_its_zone_even_with_matching_hash(tmp_path):
    output, report = collected(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(Path(report["observed"]["path"]).read_bytes())
    report["observed"]["path"] = str(outside)
    write_json(output / "acquisition-report.json", report)
    with pytest.raises(ValueError, match="zone"):
        verify(output)


def test_knowledge_manifest_cannot_be_repurposed_to_include_answer_paths(tmp_path):
    output, report = collected(tmp_path)
    manifest = [{"id": "finqa-dev", "status": "fetched", "framework": "CAS", "kind": "policy",
                 "url": DEV_URL, "parsed_path": report["evaluator"]["path"]}]
    write_json(output / "knowledge-source-manifest.json", manifest)
    with pytest.raises(ValueError, match="exclusion"):
        verify(output)


def test_http_200_invalid_data_is_preserved_and_not_counted(tmp_path):
    output = tmp_path / "local"
    report = collect(output, transport=transport(b"<html>upstream error</html>"))
    assert report["status"] == "failed" and "record_count" not in report
    assert Path(report["sources"][0]["raw_path"]).exists()
    assert not (output / "observed" / "dev.json").exists()
    with pytest.raises(ValueError, match="failed"):
        verify(output)


def test_license_mismatch_does_not_turn_data_into_usable_tasks(tmp_path):
    output = tmp_path / "local"
    report = collect(output, transport=transport(license_data=b"Unknown license"))
    assert report["status"] == "failed"
    assert not (output / "observed" / "dev.json").exists()


def test_network_errors_are_retained_without_credential_bearing_message(tmp_path):
    def handler(request):
        raise httpx.ConnectError("proxy://user:SECRET_CREDENTIAL@host", request=request)

    report = collect(tmp_path / "local", transport=httpx.MockTransport(handler))
    assert report["status"] == "failed" and report["failure"]["type"] == "ConnectError"
    assert "SECRET_CREDENTIAL" not in json.dumps(report)


def test_source_redirect_cannot_fetch_other_splits_or_hosts(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": DEV_URL.replace("dev.json", "test.json")})

    report = collect(tmp_path / "local", transport=httpx.MockTransport(handler))
    assert report["status"] == "failed" and calls == [DEV_URL]


def test_public_or_private_test_source_cannot_be_requested():
    with httpx.Client(transport=transport()) as client:
        for name in ["test.json", "private_test.json", "train.json"]:
            with pytest.raises(ValueError, match="official FinQA dev"):
                fetch(client, DEV_URL.replace("dev.json", name))


def test_download_declared_length_is_bounded_before_buffering():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=b"tiny", headers={"content-length": str(MAX_BYTES + 1)}))) as client:
        with pytest.raises(ValueError, match="size bound"):
            fetch(client, DEV_URL)


def test_download_stream_actual_length_is_bounded_without_length_header():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345"))) as client:
        with pytest.raises(ValueError, match="size bound"):
            fetch(client, DEV_URL, maximum=4)
