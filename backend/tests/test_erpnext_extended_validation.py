from copy import deepcopy

import pytest

from scripts.validate_erpnext_extended import SCENARIOS, export_mock, inspect, source_rows


@pytest.mark.parametrize('scenario', SCENARIOS, ids=lambda s: s['id'])
async def test_handwritten_complex_gl_reference(scenario):
    exported, requests = await export_mock(source_rows(scenario))
    assert all(inspect(exported, scenario).values())
    assert all(request['method'] == 'GET' for request in requests)


@pytest.mark.parametrize('cancelled', [1, -1, None, '0', False, 'missing'])
async def test_cancelled_and_missing_status_rejected_even_if_backend_ignores_filter(cancelled):
    rows = source_rows(SCENARIOS[0])[:1]
    if cancelled == 'missing':
        rows[0].pop('is_cancelled')
    else:
        rows[0]['is_cancelled'] = cancelled
    with pytest.raises(ValueError, match='explicitly be active'):
        await export_mock(rows, honor_filter=False)


@pytest.mark.parametrize('tamper', ['amount', 'currency', 'source', 'infer_invoice'])
async def test_reference_validator_rejects_tampered_exports(tamper):
    exported, _ = await export_mock(source_rows(SCENARIOS[0]))
    exported = deepcopy(exported)
    if tamper == 'amount':
        exported['records'][0]['debit'] = '101.00'
    elif tamper == 'currency':
        exported['records'][0]['currency'] = 'USD'
    elif tamper == 'source':
        exported['records'][0]['source_id'] = 'made-up'
    else:
        exported['records'][0]['invoice_id'] = 'inferred'
    assert not all(inspect(exported, SCENARIOS[0]).values())
