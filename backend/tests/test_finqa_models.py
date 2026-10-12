import json

import httpx
import pytest
from app.model_gateway import DeepSeekGateway
from benchmarks.finqa_models import NumericInstruction, NumericProposal, _write, run_pilot, runtime_messages
from pydantic import ValidationError
from datasets.finqa import collect, json_bytes


def source_record():
    return {'id': 'EXAMPLE/2021/page_4.pdf-0', 'pre_text': ['Revenue was 30 in 2021.'],
            'post_text': ['Revenue was 20 in 2020.'], 'table': [['year', 'revenue'], ['2021', '30'], ['2020', '20']],
            'qa': {'question': 'What is the difference?', 'program': 'subtract(30, 20)',
                   'program_re': 'subtract(30, 20)', 'exe_ans': 10,
                   'gold_inds': {'PRIVATE_GOLD_KEY': 'PRIVATE_GOLD_TEXT'},
                   'answer': 'PRIVATE_ANSWER_SENTINEL'}}


@pytest.fixture
def dataset(tmp_path):
    target = tmp_path / 'dataset'
    raw = json_bytes([source_record()])
    license_data = ('MIT License\nCopyright (c) 2021 Zhiyu Chen\n'
                    'The above copyright notice and this permission notice shall be included\n').encode()
    result = collect(target, transport=httpx.MockTransport(lambda request: httpx.Response(
        200, content=license_data if str(request.url).endswith('/LICENSE') else raw)))
    assert result['status'] == 'collected'
    return target


def provider(proposal):
    return httpx.Response(200, json={'model': 'deepseek-flash', 'usage': {'prompt_tokens': 10,
        'completion_tokens': 10, 'total_tokens': 20, 'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 10},
        'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(proposal)}}]})


ANSWER = {'status': 'answer', 'steps': [{'operation': 'subtract', 'left': '30', 'right': '20'}],
          'evidence_ids': ['pre_text:0', 'post_text:0'], 'limitations': []}


@pytest.mark.parametrize('step', [
    {'operation': '__import__', 'left': 'os', 'right': 'system'},
    {'operation': 'add', 'left': '1', 'right': '2), subtract(4, 3'},
    {'operation': 'add', 'left': '1', 'right': '2); print(3'},
    {'operation': 'add', 'left': 1, 'right': '2'},
    {'operation': 'add', 'left': '1', 'right': '2', 'code': 'arbitrary'},
])
def test_typed_instruction_cannot_add_code_multiple_operations_or_extra_fields(step):
    with pytest.raises(ValueError):
        NumericInstruction.model_validate(step)


def test_structured_steps_preserve_lexemes_and_reject_legacy_or_abstention_programs():
    value = NumericProposal.model_validate({**ANSWER, 'steps': [
        {'operation': 'subtract', 'left': '30.00', 'right': '20'},
        {'operation': 'divide', 'left': '#0', 'right': 'const_2'}]})
    assert value.steps[0].left == '30.00' and value.steps[1].left == '#0'
    with pytest.raises(ValidationError):
        NumericProposal.model_validate({**ANSWER, 'status': 'insufficient'})
    with pytest.raises(ValidationError):
        NumericProposal.model_validate({**ANSWER, 'program': 'subtract(30, 20)'})


def test_atomic_report_retries_only_transient_file_lock(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'report.json'
    _write(path, {'status': 'running'}, new=True)
    original = Path.replace
    attempts = []

    def temporarily_locked(source, target):
        attempts.append((source, target))
        if len(attempts) == 1:
            raise PermissionError('offline reader lock')
        return original(source, target)

    monkeypatch.setattr(Path, 'replace', temporarily_locked)
    _write(path, {'status': 'finished', 'paid_requests': 3})
    assert len(attempts) == 2 and json.loads(path.read_text()) == {'status': 'finished', 'paid_requests': 3}


def test_permanent_report_lock_remains_failure(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'report.json'
    _write(path, {'status': 'running'}, new=True)
    attempts = []

    def locked(source, target):
        attempts.append(source)
        raise PermissionError('offline permanent lock')

    monkeypatch.setattr(Path, 'replace', locked)
    with pytest.raises(PermissionError):
        _write(path, {'status': 'finished'})
    assert len(attempts) == 5 and json.loads(path.read_text()) == {'status': 'running'}


@pytest.mark.asyncio
@pytest.mark.parametrize('offset', [-1, True, 883, 1])
async def test_invalid_or_empty_offset_never_dispatches(dataset, tmp_path, offset):
    attempts = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: attempts.append(request) or provider(ANSWER))) as client:
        with pytest.raises(ValueError, match='offset'):
            await run_pilot(dataset, tmp_path / 'pilot.json', max_cases=1, sample_offset=offset,
                            gateway=DeepSeekGateway('fixture-key', client=client))
    assert attempts == [] and not (tmp_path / 'pilot.json').exists()


@pytest.mark.asyncio
async def test_model_sees_observed_document_not_gold_program_answers_or_retrieval_labels(dataset, tmp_path):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert 'PRIVATE_' not in json.dumps(body)
        assert 'subtract(30, 20)' not in body['messages'][1]['content']
        assert 'tools' not in body and body['thinking'] == {'type': 'disabled'}
        return provider(ANSWER)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_pilot(dataset, tmp_path / 'pilot.json', max_cases=1,
                                 gateway=DeepSeekGateway('SECRET_KEY_SENTINEL', client=client))
    assert len(bodies) == 1 and report['paid_requests'] == 1
    assert report['summary']['exact_matches'] == 1 and report['summary']['tokens'] == 20
    assert report['cases'][0]['grounding']['semantic_status'] == 'unverified'
    assert 'PRIVATE_' not in json.dumps(report) and 'SECRET_KEY_SENTINEL' not in json.dumps(report)


@pytest.mark.asyncio
async def test_missing_truth_stays_unscorable_and_paid_usage_is_retained(dataset, tmp_path):
    (dataset / 'evaluator/dev.json').unlink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: provider(ANSWER))) as client:
        report = await run_pilot(dataset, tmp_path / 'pilot.json', max_cases=1,
                                 gateway=DeepSeekGateway('fixture', client=client))
    assert report['cases'][0]['error']['code'] == 'truth_unavailable'
    assert not report['cases'][0]['scorable'] and report['paid_requests'] == 1
    assert report['summary']['exact_match_rate'] is None and report['summary']['tokens'] == 20


@pytest.mark.asyncio
async def test_global_request_rejection_never_dispatches_or_consumes_case_reservation(dataset, tmp_path):
    attempts = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: attempts.append(request) or provider(ANSWER))) as client:
        report = await run_pilot(dataset, tmp_path / 'pilot.json', max_cases=1, max_paid_requests=0,
                                 gateway=DeepSeekGateway('fixture', client=client))
    assert attempts == [] and report['paid_requests'] == 0
    assert report['cases'][0]['usage'] == {'calls': 0, 'tokens': 0, 'tools': 0, 'reserved_tokens': 0}
    assert report['cases'][0]['error']['code'] == 'budget_exhausted'


@pytest.mark.asyncio
async def test_invalid_operands_get_one_bounded_repair_and_failure_costs_are_retained(dataset, tmp_path):
    attempts = []
    wrong = {**ANSWER, 'steps': [{'operation': 'subtract', 'left': '30', 'right': '999'}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: attempts.append(request) or provider(wrong))) as client:
        report = await run_pilot(dataset, tmp_path / 'pilot.json', max_cases=1,
                                 gateway=DeepSeekGateway('fixture', client=client))
    row = report['cases'][0]
    assert len(attempts) == 2 and row['error']['code'] == 'numeric_model_output'
    assert row['usage']['tokens'] == 40 and row['validation_failures'] == [
        {'code': 'operand_not_in_cited_sources'}, {'code': 'operand_not_in_cited_sources'}]
    assert row['exact_at_5_decimal_places'] is False


@pytest.mark.asyncio
async def test_existing_report_blocks_rerun_before_key_or_data_loading(tmp_path):
    report = tmp_path / 'pilot.json'
    report.write_bytes(b'HISTORIC_RUN')
    with pytest.raises(ValueError, match='already exists'):
        await run_pilot(tmp_path / 'missing', report, max_cases=1)
    assert report.read_bytes() == b'HISTORIC_RUN'


@pytest.mark.asyncio
async def test_truth_is_loaded_only_after_runtime_has_finished(dataset, tmp_path, monkeypatch):
    from benchmarks import finqa_models
    original = finqa_models.load_answer_after_runtime
    state = {'runtime_finished': False}

    class Gateway:
        model = 'fixture'

        async def chat(self, *args, **kwargs):
            state['runtime_finished'] = True
            return {'message': {'content': json.dumps(ANSWER)}}

    def load_after(*args):
        assert state['runtime_finished']
        return original(*args)

    monkeypatch.setattr(finqa_models, 'load_answer_after_runtime', load_after)
    report = await run_pilot(dataset, tmp_path / 'pilot.json', max_cases=1, gateway=Gateway())
    assert report['summary']['exact_matches'] == 1


def test_runtime_encoder_rejects_unknown_answer_bearing_document_fields():
    doc = {'id': 'fixture', 'question': 'Question', 'pre_text': [], 'post_text': [],
           'table': [], 'locators': {}, 'qa': {'exe_ans': 999}}
    with pytest.raises(ValueError):
        runtime_messages(doc)
