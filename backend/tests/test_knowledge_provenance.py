"""Independent source-binding attacks using only hand-authored temporary files.

The HTML bytes and expected parsed text below are written independently, not
produced by the collector or knowledge parser. No official answer archive,
application router, live database, network or model is accessed by these tests.
Protected metadata is ID, kind, URL, framework, title and effective_from.
Publisher/redistribution claims are not propagated from acquisition reports.
Time assertions cover declared start dates; expiry intervals are unsupported.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from app import knowledge


TITLE = '独立手写会计参考规范'
PARAGRAPH = '会计原始记录应当可以追溯来源。凭证引用须绑定原始资料，金额核对与规则计算应当单独验证。'
TEXT = TITLE + '\n' + TITLE + '\n' + PARAGRAPH
RAW_HTML = ('<!doctype html><html><head><meta charset="utf-8"><title>' + TITLE +
            '</title></head><body><h1>' + TITLE + '</h1><p>' + PARAGRAPH + '</p></body></html>').encode('utf-8')
ANSWER_TEXT = '问：是否应该把未知事项认定为事实？\n答：未知事项需要补充证据，不应自动认定为已经核验的事实。'
ANSWER_HTML = ('<!doctype html><html><head><meta charset="utf-8"><title>问：是否应该把未知事项认定为事实？'
               '</title></head><body><h1>问：是否应该把未知事项认定为事实？</h1>'
               '<div class="TRS_Editor">答：未知事项需要补充证据，不应自动认定为已经核验的事实。</div>'
               '</body></html>').encode('utf-8')
REFERENCE = {
    'id': 'handwritten-policy', 'title': TITLE,
    'url': 'https://kjs.mof.gov.cn/test/handwritten-policy.htm', 'framework': 'CAS', 'publisher': '财政部',
    'kind': 'policy', 'redistribution': 'source_manifest_only', 'effective_from': '2025-01-01',
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'public-sources'
    folder = root / REFERENCE['id']
    folder.mkdir(parents=True)
    checksum = hashlib.sha256(RAW_HTML).hexdigest()
    raw = folder / (checksum + '.html')
    raw.write_bytes(RAW_HTML)
    parsed = folder / 'parsed.json'
    write_json(parsed, [{'page': 1, 'text': TEXT}])
    registry = tmp_path / 'registry.json'
    registry_value = {'version': 'hand-authored-test-registry-v1', 'sources': [deepcopy(REFERENCE)]}
    report = root / 'acquisition-report.json'
    row = {**deepcopy(REFERENCE), 'status': 'fetched', 'sha256': checksum, 'bytes': len(RAW_HTML),
           'raw_path': str(raw), 'parsed_path': str(parsed), 'final_url': REFERENCE['url'],
           'content_type': 'text/html; charset=utf-8', 'parser_version': 'collector-2',
           'retrieved_at': '2026-09-30T00:00:00+00:00', 'temporal_status': 'declared'}
    write_json(registry, registry_value)
    write_json(report, [row])
    return {'root': root, 'folder': folder, 'raw': raw, 'parsed': parsed, 'registry': registry,
            'registry_value': registry_value, 'report': report, 'rows': [row],
            'output': tmp_path / 'knowledge/corpus.json'}


def build(source):
    write_json(source['registry'], source['registry_value'])
    write_json(source['report'], source['rows'])
    return knowledge.build_corpus(source['report'], source['output'],
                                  registry=source['registry'], source_root=source['root'])


def load(source):
    return knowledge.load_verified_corpus(source['output'], registry=source['registry'],
                                          source_root=source['root'])


def artifact(source):
    return json.loads(source['output'].read_text(encoding='utf-8'))


def test_valid_handwritten_source_builds_versioned_content_bound_corpus(source):
    chunks = build(source)
    assert len(chunks) == 1
    assert chunks[0]['source_id'] == REFERENCE['id']
    assert chunks[0]['text'] == TEXT
    assert chunks[0]['page'] == 1
    assert chunks[0]['offset'] == 0
    assert chunks[0]['url'] == REFERENCE['url']
    assert chunks[0]['sha256'] == hashlib.sha256(RAW_HTML).hexdigest()
    assert artifact(source)['schema_version'] == 'reference-corpus-v2'
    assert load(source) == chunks


@pytest.mark.parametrize('kind', [None, '', 'unknown', 'policy_unchecked', 'dataset_description'])
def test_missing_or_unknown_report_kind_cannot_admit_policy_text(source, kind):
    if kind is None:
        source['rows'][0].pop('kind')
    else:
        source['rows'][0]['kind'] = kind
    with pytest.raises(ValueError):
        build(source)
    assert not source['output'].exists()


@pytest.mark.parametrize('kind', [None, '', 'unknown'])
def test_missing_or_unknown_registry_kind_is_not_a_policy_allowlist(source, kind):
    source['registry_value']['sources'][0].pop('kind')
    if kind is not None:
        source['registry_value']['sources'][0]['kind'] = kind
    with pytest.raises(ValueError):
        build(source)


def test_registered_official_answer_mislabeled_as_policy_is_rejected(source):
    source['registry_value']['sources'][0]['kind'] = 'official_answer'
    source['registry_value']['sources'][0]['url'] = 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/test-answer.htm'
    source['rows'][0]['url'] = source['registry_value']['sources'][0]['url']
    source['rows'][0]['final_url'] = source['rows'][0]['url']
    with pytest.raises(ValueError):
        build(source)


def test_explicitly_registered_answer_is_excluded_without_opening_answer_file(source, monkeypatch):
    source['registry_value']['sources'][0]['kind'] = 'official_answer'
    source['rows'][0]['kind'] = 'official_answer'
    answer = source['root'] / 'official-cases/answers/answer.json'
    write_json(answer, [{'page': 1, 'text': ANSWER_TEXT}])
    source['rows'][0]['parsed_path'] = str(answer)
    original = Path.open

    def forbid_answer(path, *args, **kwargs):
        if path == answer:
            raise AssertionError('Excluded answer material was opened')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', forbid_answer)
    chunks = build(source)
    assert chunks == []
    assert load(source) == []


@pytest.mark.parametrize('replacement', ['answer_zone', 'another_source_folder'])
def test_legitimate_policy_metadata_does_not_authorize_another_parsed_path(source, replacement):
    folder = source['root'] / ('official-cases/answers' if replacement == 'answer_zone' else 'another-source')
    answer = folder / 'parsed.json'
    write_json(answer, [{'page': 1, 'text': ANSWER_TEXT}])
    source['rows'][0]['parsed_path'] = str(answer)
    with pytest.raises(ValueError):
        build(source)


def test_answer_text_in_canonical_parsed_path_must_match_raw_policy(source):
    write_json(source['parsed'], [{'page': 1, 'text': ANSWER_TEXT}])
    with pytest.raises(ValueError):
        build(source)


def test_answer_bearing_raw_and_parsed_cannot_hide_under_a_registered_policy_identity(source):
    checksum = hashlib.sha256(ANSWER_HTML).hexdigest()
    raw = source['folder'] / (checksum + '.html')
    raw.write_bytes(ANSWER_HTML)
    source['rows'][0].update(raw_path=str(raw), sha256=checksum, bytes=len(ANSWER_HTML))
    answer_parsed = ('问：是否应该把未知事项认定为事实？\n问：是否应该把未知事项认定为事实？\n'
                     '答：未知事项需要补充证据，不应自动认定为已经核验的事实。')
    write_json(source['parsed'], [{'page': 1, 'text': answer_parsed}])
    with pytest.raises(ValueError):
        build(source)


@pytest.mark.parametrize('field,value', [
    ('id', 'not-registered'), ('url', 'https://www.mof.gov.cn/different.htm'), ('framework', 'SCAS'),
    ('effective_from', '2024-01-01'), ('kind', 'policy_notice'), ('title', 'Another reference'),
])
def test_report_metadata_cannot_override_independent_registry(source, field, value):
    source['rows'][0][field] = value
    with pytest.raises(ValueError):
        build(source)


@pytest.mark.parametrize('field,value', [('publisher', 'Unknown publisher'),
                                      ('redistribution', 'redistribute_everything')])
def test_unused_report_claims_are_not_propagated_as_trusted_metadata(source, field, value):
    source['rows'][0][field] = value
    chunks = build(source)
    assert chunks[0]['text'] == TEXT
    assert field not in chunks[0]
    assert field not in chunks[0]['source_binding']['metadata']
    assert field not in artifact(source)['sources'][0]['metadata']
    assert load(source) == chunks


def test_redirected_final_url_cannot_claim_the_registered_source_url(source):
    source['rows'][0]['final_url'] = 'https://kjs.mof.gov.cn/different-original.htm'
    with pytest.raises(ValueError):
        build(source)


@pytest.mark.parametrize('place', ['registry', 'report'])
def test_duplicate_source_identity_is_rejected(source, place):
    if place == 'registry':
        source['registry_value']['sources'].append(deepcopy(REFERENCE))
    else:
        source['rows'].append(deepcopy(source['rows'][0]))
    with pytest.raises(ValueError):
        build(source)


@pytest.mark.parametrize('replacement', ['raw', 'parsed'])
def test_source_paths_cannot_escape_explicit_source_root(source, tmp_path, replacement):
    escaped = tmp_path / 'outside-source-root' / ('raw.html' if replacement == 'raw' else 'parsed.json')
    escaped.parent.mkdir()
    escaped.write_bytes(RAW_HTML if replacement == 'raw' else source['parsed'].read_bytes())
    source['rows'][0][replacement + '_path'] = str(escaped)
    with pytest.raises(ValueError):
        build(source)


@pytest.mark.parametrize('replacement', ['raw', 'parsed'])
def test_relative_dotdot_cannot_escape_source_root(source, tmp_path, replacement):
    escaped = tmp_path / ('escaped.html' if replacement == 'raw' else 'escaped.json')
    escaped.write_bytes(RAW_HTML if replacement == 'raw' else source['parsed'].read_bytes())
    source['rows'][0][replacement + '_path'] = '../' + escaped.name
    with pytest.raises(ValueError):
        build(source)


def test_raw_hash_claim_is_verified_against_original_bytes(source):
    source['raw'].write_bytes(RAW_HTML.replace('原始'.encode(), '替换'.encode()))
    with pytest.raises(ValueError):
        build(source)


@pytest.mark.parametrize('replacement', [[], [{'page': 1, 'text': 'Fabricated policy text'}],
    [{'page': 2, 'text': TEXT}], [{'page': 1, 'text': TEXT}, {'page': 1, 'text': TEXT}],
    [{'page': True, 'text': TEXT}], [{'page': 1, 'text': None}]])
def test_parsed_content_and_page_identity_are_reproduced_from_raw(source, replacement):
    write_json(source['parsed'], replacement)
    with pytest.raises(ValueError):
        build(source)


@pytest.mark.parametrize('part', ['raw', 'parsed', 'chunk'])
def test_loader_rechecks_raw_parsed_and_chunk_content_after_build(source, part):
    build(source)
    if part == 'raw':
        source['raw'].write_bytes(b'changed raw original')
    elif part == 'parsed':
        write_json(source['parsed'], [{'page': 1, 'text': 'changed parsed policy'}])
    else:
        value = artifact(source)
        value['chunks'][0]['text'] = ANSWER_TEXT
        write_json(source['output'], value)
    with pytest.raises(ValueError):
        load(source)


@pytest.mark.parametrize('field,value', [('source_id', 'unknown-policy'), ('url', 'https://example.org/wrong'),
    ('framework', 'SCAS'), ('page', 2), ('offset', 1), ('effective_from', '2024-01-01'), ('sha256', '0' * 64)])
def test_mutated_chunk_metadata_is_not_trusted_on_load(source, field, value):
    build(source)
    doc = artifact(source)
    doc['chunks'][0][field] = value
    write_json(source['output'], doc)
    with pytest.raises(ValueError):
        load(source)


def test_duplicate_chunk_ids_cannot_duplicate_reference_weight(source):
    build(source)
    doc = artifact(source)
    doc['chunks'].append(deepcopy(doc['chunks'][0]))
    write_json(source['output'], doc)
    with pytest.raises(ValueError):
        load(source)


def test_old_unbound_list_corpus_is_refused(source):
    source['output'].parent.mkdir()
    write_json(source['output'], [{'source_id': REFERENCE['id'], 'text': TEXT, 'framework': 'CAS',
                                 'url': REFERENCE['url'], 'chunk_id': 'old', 'sha256': 'old'}])
    with pytest.raises(ValueError):
        load(source)


def test_loader_uses_current_independent_registry_not_embedded_metadata(source):
    build(source)
    registry = source['registry_value']
    registry['sources'][0]['kind'] = 'official_answer'
    write_json(source['registry'], registry)
    with pytest.raises(ValueError):
        load(source)


@pytest.mark.parametrize('framework', ['CAS', 'SCAS'])
def test_both_framework_reference_remains_searchable_after_declared_start_date(source, framework):
    source['registry_value']['sources'][0]['framework'] = 'BOTH'
    source['rows'][0]['framework'] = 'BOTH'
    build(source)
    found = knowledge.search(load(source), '原始记录', framework, '2026-09')
    assert len(found) == 1
    assert found[0]['text'] == TEXT
    assert found[0]['notice']


def test_cas_reference_filters_framework_and_declared_start_month_only(source):
    build(source)
    corpus = load(source)
    assert knowledge.search(corpus, '原始记录', 'CAS', '2025-01')
    assert not knowledge.search(corpus, '原始记录', 'SCAS', '2026-09')
    assert not knowledge.search(corpus, '原始记录', 'CAS', '2024-12')
    assert corpus[0]['effective_to'] is None


@pytest.mark.parametrize('place', ['report', 'registry'])
def test_declared_expiry_is_refused_as_an_unsupported_interval(source, place):
    target = source['rows'][0] if place == 'report' else source['registry_value']['sources'][0]
    target['effective_to'] = '2026-01-01'
    with pytest.raises(ValueError):
        build(source)


def test_unknown_effective_date_is_preserved_as_unknown_not_invented(source):
    source['registry_value']['sources'][0]['effective_from'] = None
    source['rows'][0]['effective_from'] = None
    source['rows'][0]['temporal_status'] = 'unverified'
    build(source)
    chunks = load(source)
    assert chunks[0]['effective_from'] is None
    assert chunks[0]['temporal_status'] == 'unverified'


def test_defaults_use_fixed_registry_and_expected_local_source_root(source, monkeypatch):
    monkeypatch.setattr(knowledge, 'DEFAULT_REGISTRY', source['registry'])
    chunks = knowledge.build_corpus(source['report'], source['output'])
    assert chunks[0]['text'] == TEXT
    assert knowledge.load_verified_corpus(source['output']) == chunks


def test_failed_rebuild_does_not_replace_previously_verified_artifact(source):
    build(source)
    before = source['output'].read_bytes()
    source['rows'][0]['framework'] = 'SCAS'
    with pytest.raises(ValueError):
        build(source)
    assert source['output'].read_bytes() == before


def test_frozen_reference_preserves_verified_source_and_exact_text(source):
    chunk = build(source)[0]
    passage = knowledge.freeze_reference(chunk, registry=source['registry'])
    assert passage['source_id'] == chunk['chunk_id']
    assert passage['text'] == TEXT
    assert passage['url'] == REFERENCE['url']
    assert passage['framework'] == 'CAS'
    binding = passage['source_binding']
    assert binding['schema_version'] == 'frozen-reference-v1'
    assert binding['original_source_id'] == REFERENCE['id']
    assert binding['raw_sha256'] == hashlib.sha256(RAW_HTML).hexdigest()
    assert binding['chunk_sha256'] == hashlib.sha256(TEXT.encode('utf-8')).hexdigest()
    verified = knowledge.verify_frozen_references([passage], registry=source['registry'])
    assert verified == [passage]
    assert verified[0] is not passage


@pytest.mark.parametrize('field,value', [
    ('text', ANSWER_TEXT), ('url', 'https://example.org/answer'), ('framework', 'SCAS'),
    ('title', 'different source title'), ('source_id', '0' * 64), ('effective_from', '2024-01-01'),
    ('status', 'invented_confirmation'),
])
def test_frozen_passage_metadata_or_content_changes_are_rejected(source, field, value):
    chunk = build(source)[0]
    passage = knowledge.freeze_reference(chunk, registry=source['registry'])
    passage[field] = value
    with pytest.raises(ValueError):
        knowledge.verify_frozen_references([passage], registry=source['registry'])


@pytest.mark.parametrize('field,value', [('original_source_id', 'not-registered'), ('kind', 'official_answer'),
    ('raw_sha256', '0' * 64), ('parsed_sha256', '0' * 64), ('chunk_sha256', '0' * 64), ('page', 2),
    ('offset', 1), ('end', 1), ('registry_sha256', '0' * 64)])
def test_frozen_source_binding_changes_are_rejected(source, field, value):
    passage = knowledge.freeze_reference(build(source)[0], registry=source['registry'])
    passage['source_binding'][field] = value
    with pytest.raises(ValueError):
        knowledge.verify_frozen_references([passage], registry=source['registry'])


def test_unbound_historical_passages_do_not_gain_provenance_by_loading(source):
    old = {'source_id': 'old-chunk', 'text': TEXT, 'url': REFERENCE['url'], 'framework': 'CAS',
           'title': TITLE, 'effective_from': '2025-01-01', 'effective_to': None, 'status': 'declared'}
    with pytest.raises(ValueError):
        knowledge.verify_frozen_references([old], registry=source['registry'])


def test_empty_frozen_references_do_not_invent_source_material(source):
    assert knowledge.verify_frozen_references([], registry=source['registry']) == []


def test_changed_chunk_cannot_get_a_new_binding_from_freeze_reference(source):
    chunk = build(source)[0]
    chunk['text'] = ANSWER_TEXT
    with pytest.raises(ValueError):
        knowledge.freeze_reference(chunk, registry=source['registry'])


def test_frozen_verification_does_not_reopen_originals_or_lose_historical_copy(source, monkeypatch):
    passage = knowledge.freeze_reference(build(source)[0], registry=source['registry'])
    # Current files may change after a historical snapshot was frozen. This
    # proves frozen verification relies on the preserved copy, while the loader
    # tests above separately prove current corpus reuse rechecks current bytes.
    source['raw'].write_bytes(b'changed after the historical reference was frozen')
    write_json(source['parsed'], [{'page': 1, 'text': 'Changed after historical snapshot'}])
    original = Path.open

    def forbid_originals(path, *args, **kwargs):
        if path in {source['raw'], source['parsed']}:
            raise AssertionError('Frozen historical reference tried to read current source files')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', forbid_originals)
    result = knowledge.verify_frozen_references([passage], registry=source['registry'])
    assert result[0]['text'] == TEXT
    result[0]['source_binding']['metadata']['kind'] = 'official_answer'
    assert passage['source_binding']['metadata']['kind'] == 'policy'


def test_symlink_rejection_with_explicit_simulation_if_host_disallows_creation(source, tmp_path,
                                                                              monkeypatch, record_property):
    parsed = source['parsed']
    old_bytes = parsed.read_bytes()
    outside = tmp_path / 'outside-parsed.json'
    outside.write_bytes(old_bytes)
    parsed.unlink()
    try:
        parsed.symlink_to(outside)
        record_property('symlink_proof', 'real_filesystem_symlink')
    except OSError:
        # The regular file is restored; the link predicate is explicitly
        # simulated to verify fail-closed handling, not OS-level containment.
        parsed.write_bytes(old_bytes)
        original = Path.is_symlink

        def simulated(path):
            return True if path == parsed else original(path)

        monkeypatch.setattr(Path, 'is_symlink', simulated)
        record_property('symlink_proof', 'simulated_predicate_host_creation_denied')
    with pytest.raises(ValueError):
        build(source)
