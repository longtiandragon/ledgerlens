"""Offline acquisition tests use invented fixture prose, never official answers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.knowledge import build_corpus
from datasets.official_cases import (PARSER_VERSION, collect, coverage, load_manifest,
                                     load_question, parse_qa, validate_url, verify_local_collection)


QUESTION = "问：测试企业在约定场景下应如何处理费用？"
ANSWER = "答：这是只供解析测试的独立虚构答复，不用于任何会计规则的标准答案。"


def source(identity="mof-test"):
    return {"id": identity, "url": f"https://kjs.mof.gov.cn/{identity}.htm", "title": "虚构解析夹具",
            "expected_question": QUESTION, "framework": "CAS", "publisher": "财政部",
            "kind": "official_answer", "redistribution": "source_manifest_only",
            "applicable_conditions": "只供离线解析测试，不属于已获取官方案例。"}


def html(question=QUESTION, answer=ANSWER, heading=None, publisher="财政部", date="2021-11-02 15:42:00"):
    heading = question if heading is None else heading
    return (f'<html><head><meta name="SiteName" content="中华人民共和国财政部">'
            f'<meta name="SiteIDCode" content="bm14000001"><meta name="ArticleTitle" content="{question}">'
            f'<meta name="PubDate" content="{date}"><meta name="ContentSource" content="{publisher}">'
            f'</head><body><h2 class="title_con">{heading}</h2>'
            f'<div class="TRS_Editor"><p>{answer}</p></div></body></html>').encode("utf-8")


def manifest(tmp_path, entries=None):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"schema_version": PARSER_VERSION, "sources": entries or [source()]}),
                    encoding="utf-8")
    return path


def test_parse_actual_question_answer_contract_and_published_date():
    parsed = parse_qa(html(), source())
    assert parsed["question"] == QUESTION
    assert parsed["answer"] == ANSWER
    assert parsed["published_at"] == "2021-11-02"
    assert parsed["answer_locator"] == "div.TRS_Editor"


@pytest.mark.parametrize("publisher", ["", "会计司", "财政部"])
def test_official_department_and_empty_original_content_source_are_preserved(publisher):
    parsed = parse_qa(html(publisher=publisher), source())
    assert parsed["original_content_source"] == publisher
    assert parsed["publisher"] == "财政部"
    assert parsed["publisher_verification"] == "official_HTTPS_origin_and_site_metadata"


def test_missing_site_identity_cannot_be_repaired_from_declared_manifest():
    raw = html().replace(b'bm14000001', b'other-site')
    with pytest.raises(ValueError, match="identity"):
        parse_qa(raw, source())


@pytest.mark.parametrize("data", [
    b"<html><body>502 Bad Gateway</body></html>",
    html(question="实施问答目录"),
    html(answer="答：缺少内容"),
    html(answer="不含答复标志的长正文，不可以冒充官方已经给出结论的具体问答案例。"),
    html(heading="问：不同的另一个问题？"),
    html(publisher="其他网站"),
    html(date="not-a-date"),
    html(answer=ANSWER + " 答：又一答复，需有独立的提取定位和人工评审。"),
    html(answer=ANSWER + "\ufffd"),
    b"\xff\xfe\xfd",
])
def test_directory_error_corrupt_or_ambiguous_page_is_not_counted(data):
    with pytest.raises((ValueError, UnicodeError)):
        parse_qa(data, source())


def test_question_must_match_declared_official_page():
    different = source()
    different["expected_question"] = "问：另外一个具体问题？"
    with pytest.raises(ValueError, match="manifest"):
        parse_qa(html(), different)


@pytest.mark.parametrize("url", [
    "http://kjs.mof.gov.cn/test", "https://example.com/test", "https://user@kjs.mof.gov.cn/test",
    "https://kjs.mof.gov.cn:9443/test", "https://kjs.mof.gov.cn/test#answer",
    "https://kjs.mof.gov.cn.evil.invalid/test",
])
def test_official_url_allowlist(url):
    with pytest.raises(ValueError, match="official"):
        validate_url(url)


@pytest.mark.parametrize("mutation", [
    {"id": "../escape"}, {"kind": "policy"}, {"framework": "UNKNOWN"},
    {"applicable_conditions": ""}, {"redistribution": "redistribute_original"},
])
def test_manifest_rejects_invalid_source_contract_before_writing(tmp_path, mutation):
    entry = {**source(), **mutation}
    output = tmp_path / "out"
    with pytest.raises(ValueError):
        collect(manifest(tmp_path, [entry]), output)
    assert not output.exists()


def test_duplicate_url_does_not_create_extra_official_case(tmp_path):
    duplicate = {**source("second"), "url": source()["url"]}
    with pytest.raises(ValueError, match="Duplicate URL"):
        load_manifest(manifest(tmp_path, [source(), duplicate]))


def test_collection_preserves_original_hash_and_isolates_answer(tmp_path):
    original = html()
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=original))
    output = tmp_path / "out"
    [result] = collect(manifest(tmp_path), output, transport=transport)
    assert result["status"] == "fetched"
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert Path(result["raw_path"]).read_bytes() == original
    question = load_question(Path(result["observed_path"]))
    assert question["question"] == QUESTION
    assert "answer" not in question and "raw_path" not in question and "parsed_path" not in question
    assert ANSWER not in json.dumps(question, ensure_ascii=False)
    answer = json.loads(Path(result["answer_path"]).read_text(encoding="utf-8"))
    assert answer["evaluator_only"] is True and answer["answer"] == ANSWER
    assert answer["question_sha256"] == question["question_sha256"]
    with pytest.raises(ValueError, match="evaluator"):
        load_question(Path(result["answer_path"]))
    observed_index = json.loads((output / "observed" / "manifest.json").read_text(encoding="utf-8"))
    assert observed_index["zone"] == "question_only"
    assert observed_index["cases"][0]["path"] == str(Path(result["observed_path"]).resolve())
    assert build_corpus(output / "acquisition-report.json", tmp_path / "corpus.json") == []
    public = coverage([result])
    assert public["collected_official_qa_count"] == 1
    assert public["official_accounting_tests_passed"] is None
    assert public["runtime_answer_indexed_count"] == 0
    assert ANSWER not in json.dumps(public, ensure_ascii=False)


def test_question_loader_rejects_extra_answer_field_and_wrong_hash(tmp_path):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=html()))
    [result] = collect(manifest(tmp_path), tmp_path / "out", transport=transport)
    path = Path(result["observed_path"])
    value = load_question(path)
    extra = {**value, "answer": ANSWER}
    path.write_text(json.dumps(extra), encoding="utf-8")
    with pytest.raises(ValueError, match="evaluator"):
        load_question(path)
    value["question_sha256"] = "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_question(path)


def test_redirect_cannot_leave_official_origin_allowlist(tmp_path):
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.com/answer"})

    [result] = collect(manifest(tmp_path), tmp_path / "out", transport=httpx.MockTransport(handler))
    assert result["status"] == "failed"
    assert urls == [source()["url"]]
    assert result["attempts"][0]["code"] == "ValueError"


def test_502_attempts_recorded_before_success_and_404_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("datasets.official_cases.time.sleep", lambda _: None)
    responses = iter([httpx.Response(502), httpx.Response(200, content=html())])
    [result] = collect(manifest(tmp_path), tmp_path / "out", transport=httpx.MockTransport(lambda _: next(responses)))
    assert result["status"] == "fetched"
    assert result["attempts"][0]["http_status"] == 502
    assert len(result["attempts"]) == 2
    [failed] = collect(manifest(tmp_path), tmp_path / "out2", transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    assert failed["status"] == "failed" and len(failed["attempts"]) == 1
    assert failed["error"]["http_status"] == 404


def test_http_200_error_body_remains_failed_observation(tmp_path):
    [result] = collect(manifest(tmp_path), tmp_path / "out",
                       transport=httpx.MockTransport(lambda _: httpx.Response(200, text="502 Bad Gateway")))
    assert result["status"] == "failed" and Path(result["raw_path"]).exists()
    assert "answer_path" not in result and "observed_path" not in result
    assert coverage([result])["collected_official_qa_count"] == 0


def test_source_size_bound_and_partial_runs_preserve_unselected_sources(tmp_path, monkeypatch):
    entries = [source("first"), source("second")]
    path = manifest(tmp_path, entries)
    output = tmp_path / "out"
    good = httpx.MockTransport(lambda _: httpx.Response(200, content=html()))
    collect(path, output, identifiers=["first"], transport=good)
    monkeypatch.setattr("datasets.official_cases.MAX_BYTES", 10)
    [second] = collect(path, output, identifiers=["second"], transport=good)
    assert second["status"] == "failed"
    results = json.loads((output / "acquisition-report.json").read_text(encoding="utf-8"))
    assert {r["id"]: r["status"] for r in results} == {"first": "fetched", "second": "failed"}
    assert len((output / "acquisition-history.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_updated_page_has_separate_immutable_artifact_version(tmp_path):
    path = manifest(tmp_path)
    output = tmp_path / "out"
    first_data = html()
    second_data = html(answer=ANSWER + " 此处新增另一条解析说明。")
    [first] = collect(path, output, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=first_data)))
    old_answer = Path(first["answer_path"]).read_bytes()
    [second] = collect(path, output, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=second_data)))
    assert first["sha256"] != second["sha256"]
    assert Path(first["raw_path"]).read_bytes() == first_data
    assert Path(first["answer_path"]).read_bytes() == old_answer
    assert Path(second["raw_path"]).read_bytes() == second_data


def test_local_verifier_rechecks_bytes_and_question_answer_alignment(tmp_path):
    path = manifest(tmp_path)
    output = tmp_path / "out"
    [item] = collect(path, output, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=html())))
    assert verify_local_collection(path, output)["passed"] == 1
    answer_path = Path(item["answer_path"])
    answer = json.loads(answer_path.read_text(encoding="utf-8"))
    answer["answer"] = "被改动的参考答案，不能仅靠旧哈希而继续接受。"
    answer_path.write_text(json.dumps(answer), encoding="utf-8")
    assert verify_local_collection(path, output)["failed"] == 1
    Path(item["raw_path"]).write_bytes(html(answer=ANSWER + "页面已改变。"))
    result = verify_local_collection(path, output)
    assert result["failed"] == 1 and "Original page hash" in result["results"][0]["message"]


def test_public_coverage_keeps_failed_attempt_history_without_answer_text(tmp_path, monkeypatch):
    monkeypatch.setattr("datasets.official_cases.time.sleep", lambda _: None)
    path = manifest(tmp_path)
    output = tmp_path / "out"
    failed = collect(path, output, transport=httpx.MockTransport(lambda _: httpx.Response(502)), attempts=1)
    success = collect(path, output, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=html())))
    result = coverage(success, historical_reports=failed + success)
    assert result["failed_source_count"] == 0
    assert result["acquisition_attempt_history"] == {"attempts": 2, "failures": 1, "failure_codes": {"502": 1}}
    assert ANSWER not in json.dumps(result, ensure_ascii=False)


def test_real_manifest_is_unique_question_sources_without_answer_bodies():
    entries = load_manifest(Path("docs/official-case-sources.json"))
    assert len(entries) >= 30
    assert {entry["framework"] for entry in entries} == {"CAS", "SCAS"}
    assert all("answer" not in entry and "expected_answer" not in entry for entry in entries)
    assert len({entry["url"] for entry in entries}) == len(entries)
    original = deepcopy(entries)
    assert all("effective_from" in entry for entry in entries)
    assert entries == original
