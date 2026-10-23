from copy import deepcopy

import pytest

from app.matching import match_transactions


def tx(identifier, amount, **kwargs):
    return {"id": identifier, "kind": "invoice", "amount": amount, "currency": "CNY",
            "counterparty": "供应商", "order_id": "PO-001", "business_date": "2026-09-20", **kwargs}


def test_split_bank_payments_match_single_invoice_exactly():
    result = match_transactions([tx("invoice", "100000.00")],
                                [tx("pay-1", "-60000.00", kind="bank"), tx("pay-2", "-40000.00", kind="bank")])
    assert result["status"] == "candidates"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["amount"] == "100000.00"
    assert result["committed_allocations"] == []


def test_same_amount_different_payments_preserve_ambiguity():
    result = match_transactions([tx("invoice", "10.00")], [tx("a", "10.00"), tx("b", "10.00")])
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 2


def test_union_of_disjoint_pairs_is_composite_not_a_second_explanation():
    # Two payments against two bookings of different amounts pair one way only;
    # the 100 against 100 candidate merely merges the two pairs.
    result = match_transactions([tx("p60", "60.00"), tx("p40", "40.00")],
                                [tx("j60", "60.00", kind="journal", account="1002", debit="60.00", credit="0.00"),
                                 tx("j40", "40.00", kind="journal", account="1002", debit="40.00", credit="0.00")],
                                flow="receivable")
    assert result["status"] == "candidates"
    assert sorted((c["left_ids"], c["composite"]) for c in result["candidates"]) == [
        (["p40"], False), (["p40", "p60"], True), (["p60"], False)]
    assert result["unmatched"] == {"left": [], "right": []}


def test_equal_amounts_remain_ambiguous_even_with_composite_candidates():
    result = match_transactions([tx("p1", "50.00"), tx("p2", "50.00")],
                                [tx("j1", "50.00", kind="journal", account="1002", debit="50.00", credit="0.00"),
                                 tx("j2", "50.00", kind="journal", account="1002", debit="50.00", credit="0.00")],
                                flow="receivable")
    assert result["status"] == "ambiguous"
    assert sum(not c["composite"] for c in result["candidates"]) == 4


def test_refund_does_not_match_purchase_payment():
    result = match_transactions([tx("invoice", "10.00")], [tx("refund", "10.00", kind="bank")])
    assert result["candidates"] == []


@pytest.mark.parametrize("change", [{"currency": "USD"}, {"counterparty": "他人"}, {"order_id": "PO-002"},
                                    {"business_date": "2026-01-01"}])
def test_currency_party_order_and_date_blocking(change):
    assert match_transactions([tx("a", "10.00")], [tx("b", "10.00", **change)])["candidates"] == []


def test_partial_candidate_is_not_settled():
    result = match_transactions([tx("a", "100.00")], [tx("b", "60.00")])
    assert not result["candidates"]
    assert result["partial_candidates"][0]["remaining_left"] == "40.00"
    assert result["unmatched"] == {"left": ["a"], "right": ["b"]}


def test_large_groups_are_reported_bounded_not_silently_truncated():
    result = match_transactions([tx(f"l{i}", "1.00") for i in range(7)], [tx("r", "7.00")])
    assert result["status"] == "bounded"
    assert result["limited_groups"][0]["reason"] == "group_size_limit"


def test_one_cent_and_no_mutation():
    left = [tx("a", "0.30")]
    right = [tx("b", "0.10"), tx("c", "0.20")]
    before = deepcopy((left, right))
    result = match_transactions(left, right)
    assert result["candidates"][0]["amount"] == "0.30"
    assert (left, right) == before


def test_duplicate_identifier_is_rejected():
    with pytest.raises(ValueError):
        match_transactions([tx("same", "1.00")], [tx("same", "1.00")])


def test_bank_match_uses_only_cash_account_side_of_payment_voucher():
    rows = [tx("payable", "0.00", kind="journal", account="2202", debit="100.00", credit="0.00"),
            tx("cash", "0.00", kind="journal", account="1002", debit="0.00", credit="100.00")]
    result = match_transactions([tx("bank", "-100.00", kind="bank")], rows)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["right_ids"] == ["cash"]


def test_receivable_sales_invoice_matches_incoming_cash_without_changing_source():
    left = [tx("sales-invoice", "100.00")]
    right = [tx("collection", "100.00", kind="bank", direction="incoming")]
    original = deepcopy((left, right))
    result = match_transactions(left, right, flow="receivable")
    assert result["flow"] == "receivable"
    assert result["candidates"][0]["amount"] == "100.00"
    assert (left, right) == original
    assert match_transactions(left, right, flow="payable")["candidates"] == []


def test_receivable_customer_refund_requires_negative_credit_note():
    refund = tx("refund", "-25.00", kind="bank", direction="outgoing")
    result = match_transactions([tx("credit-note", "-25.00")], [refund], flow="receivable")
    assert len(result["candidates"]) == 1
    assert match_transactions([tx("invoice", "25.00")], [refund], flow="receivable")["candidates"] == []


def test_supplier_refund_uses_incoming_sign_and_negative_payable_note():
    result = match_transactions([tx("vendor-credit", "-25.00")],
                                [tx("refund", "25.00", kind="bank", direction="refund")], flow="payable")
    assert len(result["candidates"]) == 1


def test_receivable_partial_collection_retains_unsettled_balance():
    result = match_transactions([tx("invoice", "100.00")],
                                [tx("collection", "60.00", kind="bank", direction="incoming")], flow="receivable")
    assert result["candidates"] == []
    assert result["partial_candidates"][0]["remaining_left"] == "40.00"
    assert result["committed_allocations"] == []


def test_receivable_combined_collection_matches_two_invoices():
    result = match_transactions([tx("inv-1", "60.00"), tx("inv-2", "40.00")],
                                [tx("combined", "100.00", kind="bank")], flow="receivable")
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["left_ids"] == ["inv-1", "inv-2"]


def test_receivable_split_collection_matches_one_invoice():
    result = match_transactions([tx("invoice", "100.00")],
                                [tx("collect-1", "60.00", kind="bank"), tx("collect-2", "40.00", kind="bank")],
                                flow="receivable")
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["right_ids"] == ["collect-1", "collect-2"]


def test_receivable_ledger_cash_debit_matches_positive_invoice():
    rows = [tx("cash", "0.00", kind="journal", account="1002", debit="100.00", credit="0.00"),
            tx("receivable", "0.00", kind="journal", account="1122", debit="0.00", credit="100.00")]
    result = match_transactions([tx("sales", "100.00")], rows, flow="receivable")
    assert result["candidates"][0]["right_ids"] == ["cash"]


def test_receivable_ledger_refund_credit_matches_negative_invoice():
    rows = [tx("cash", "0.00", kind="journal", account="1002", debit="0.00", credit="25.00"),
            tx("receivable", "0.00", kind="journal", account="1122", debit="25.00", credit="0.00")]
    result = match_transactions([tx("sales-credit", "-25.00")], rows, flow="receivable")
    assert result["candidates"][0]["right_ids"] == ["cash"]


def test_direction_contradiction_is_not_silently_normalized():
    with pytest.raises(ValueError, match="contradicts"):
        match_transactions([tx("sales", "100.00")],
                           [tx("cash", "-100.00", kind="bank", direction="incoming")], flow="receivable")


def test_bad_flow_is_rejected():
    with pytest.raises(ValueError, match="flow"):
        match_transactions([], [], flow="whatever")


def test_request_record_limit_even_when_each_group_is_small():
    left = [tx(f"a{i}", "1.00", order_id=f"order{i}") for i in range(130)]
    right = [tx(f"b{i}", "1.00", order_id=f"order{i}") for i in range(130)]
    result = match_transactions(left, right)
    assert result["status"] == "bounded"
    assert result["limited_groups"][0]["reason"] == "request_record_limit"
    assert not result["candidates"]


def test_request_group_limit_preserves_unsearched_unmatched_ids():
    left = [tx(f"a{i}", "1.00", order_id=f"order{i}") for i in range(40)]
    right = [tx(f"b{i}", "1.00", order_id=f"order{i}") for i in range(40)]
    result = match_transactions(left, right)
    assert result["status"] == "bounded"
    assert len(result["candidates"]) <= 32
    assert len(result["unmatched"]["left"]) >= 8
