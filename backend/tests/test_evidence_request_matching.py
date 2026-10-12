"""Chain matching for evidence-request candidates; hand-written records only."""
from app.evidence_requests import candidates

BEFORE = [{'id': 'o1', 'kind': 'order', 'order_id': 'PO-1', 'reference': 'PO-1'},
          {'id': 'r1', 'kind': 'receipt', 'order_id': 'PO-1', 'reference': 'RCV-1', 'source_id': 'RCV-1'},
          {'id': 'o2', 'kind': 'order', 'order_id': 'PO-2', 'reference': 'PO-2'}]
LATE_PO1 = {'id': 'r2', 'kind': 'receipt', 'order_id': 'PO-1', 'reference': 'RCV-2', 'source_id': 'RCV-2'}
LATE_PO2 = {'id': 'r3', 'kind': 'receipt', 'order_id': 'PO-2', 'reference': 'RCV-3', 'source_id': 'RCV-3'}
AFTER = [*BEFORE, LATE_PO1, LATE_PO2, {'id': 'i9', 'kind': 'invoice', 'order_id': 'PO-1', 'invoice_id': 'INV-9'}]


def test_only_new_records_of_the_kind_in_the_named_chain():
    assert candidates('receipt', 'PO-1', BEFORE, AFTER) == [LATE_PO1]
    assert candidates('receipt', 'RCV-1', BEFORE, AFTER) == [LATE_PO1]   # sibling document names the chain
    assert candidates('receipt', 'PO-2', BEFORE, AFTER) == [LATE_PO2]


def test_unrestricted_request_takes_every_new_record_of_the_kind_and_old_records_never_count():
    assert candidates('receipt', None, BEFORE, AFTER) == [LATE_PO1, LATE_PO2]
    assert candidates('receipt', None, BEFORE, BEFORE) == []
    assert candidates('order', 'PO-1', BEFORE, AFTER) == []
