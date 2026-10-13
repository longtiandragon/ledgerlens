"""Third-party oracle tests are optional and report missing Beancount as skips."""
from copy import deepcopy
from dataclasses import replace
from importlib import metadata

import pytest

from benchmarks.beancount_differential import (_restrict_source, compare_fixture, fixtures,
                                                oracle, run)


@pytest.fixture
def beancount_available():
    module = pytest.importorskip("beancount", reason="Optional Beancount differential dependency is absent")
    assert metadata.version("beancount") == "3.2.3"
    return module


@pytest.mark.usefixtures("beancount_available")
@pytest.mark.parametrize("fixture", fixtures(), ids=lambda case: case.identity)
def test_independent_third_party_and_production_subsets_agree(fixture):
    original = deepcopy(fixture.records)
    result = compare_fixture(fixture)
    assert result["status"] == "passed"
    assert result["differences"] == []
    assert result["validation_classification_agrees"] is True
    assert result["original_records_unchanged"] is True
    assert fixture.records == original


@pytest.mark.usefixtures("beancount_available")
def test_exact_cents_are_checked_by_third_party_loader_balance_assertions_and_inventory():
    fixture = fixtures()[0]
    expected = oracle(fixture.source, fixture.cutoff_exclusive)
    assert expected["valid"] is True
    assert expected["balances"] == {"Assets:Bank": "0.30", "Equity:Capital": "-0.30"}
    assert "ops.validation.validate" in expected["api"]
    assert "ops.summarize.balance_by_account" in expected["api"]
    assert "core.inventory.Inventory.get_currency_units" in expected["api"]


@pytest.mark.usefixtures("beancount_available")
def test_zero_balance_after_normal_split_payment_is_not_lost():
    fixture = next(case for case in fixtures() if case.identity == "normal-split-payment")
    expected = oracle(fixture.source, fixture.cutoff_exclusive)
    assert expected["balances"] == {"Assets:Bank": "20000.00", "Equity:Capital": "-120000.00",
                                    "Assets:Inventory": "100000.00", "Liabilities:Payable": "0.00"}


@pytest.mark.usefixtures("beancount_available")
def test_multi_month_cutoff_uses_prior_business_and_explicit_opening_once():
    cases = {case.identity: case for case in fixtures()}
    january = cases["multi-month-january"]
    march = cases["multi-month-march"]
    january_result = compare_fixture(january)
    march_result = compare_fixture(march)
    assert january.source == march.source
    assert january_result["oracle"]["balances"]["Assets:Bank"] == "1050.00"
    assert march_result["oracle"]["balances"]["Assets:Bank"] == "1000.30"
    assert january_result["oracle"]["balances"]["Income:Sales"] == "-200.00"
    assert march_result["oracle"]["balances"]["Income:Sales"] == "-150.00"
    assert all(record["business_date"][:7] == "2026-03" for record in march.records)


@pytest.mark.usefixtures("beancount_available")
def test_one_cent_imbalance_is_not_suppressed_by_inferred_tolerance():
    fixture = next(case for case in fixtures() if not case.oracle_valid)
    result = compare_fixture(fixture)
    assert result["oracle"]["valid"] is False
    assert result["oracle"]["balances"] is None
    assert "ValidationError" in result["oracle"]["error_types"]
    assert result["production_imbalance_vouchers"] == ["V-ONE-CENT-GAP"]
    assert result["balance_agrees"] is None


@pytest.mark.usefixtures("beancount_available")
def test_unrelated_third_party_validation_failure_cannot_count_as_expected_imbalance():
    fixture = fixtures()[-1]
    changed = replace(fixture, source=fixture.source + "2000-01-01 open Assets:Bank CNY\n")
    result = compare_fixture(changed)
    assert result["status"] == "failed"
    assert result["oracle"]["imbalance_vouchers"] == ["V-ONE-CENT-GAP"]
    assert result["oracle"]["other_error_types"] == ["ValidationError"]
    assert result["validation_classification_agrees"] is False


@pytest.mark.usefixtures("beancount_available")
def test_negative_fixture_requires_the_same_rejected_voucher_not_just_any_failure():
    fixture = fixtures()[-1]
    changed = replace(fixture, source=fixture.source.replace("V-ONE-CENT-GAP", "V-OTHER-GAP"))
    result = compare_fixture(changed)
    assert result["status"] == "failed"
    assert result["oracle"]["imbalance_vouchers"] == ["V-OTHER-GAP"]
    assert result["production_imbalance_vouchers"] == ["V-ONE-CENT-GAP"]


@pytest.mark.usefixtures("beancount_available")
def test_balanced_wrong_account_is_explicitly_outside_business_oracle_capability():
    fixture = next(case for case in fixtures() if case.business_error_not_detectable)
    result = compare_fixture(fixture)
    assert result["oracle"]["valid"] is True
    assert result["production_imbalance_vouchers"] == []
    assert result["business_error_detection"] == "outside_oracle_capability"


@pytest.mark.usefixtures("beancount_available")
def test_mutating_production_function_cannot_change_third_party_expected(monkeypatch):
    from app import domain

    monkeypatch.setattr(domain, "balances", lambda _: {"1002": "42.00", "4001": "-42.00"})
    result = compare_fixture(fixtures()[0])
    assert result["status"] == "failed"
    assert result["oracle"]["balances"]["Assets:Bank"] == "0.30"
    assert {difference["production"] for difference in result["differences"]} == {"42.00", "-42.00"}


@pytest.mark.usefixtures("beancount_available")
def test_balanced_record_amount_drift_is_detected_against_handwritten_other_representation():
    fixture = fixtures()[0]
    changed = deepcopy(fixture.records)
    changed[0]["debit"] = "0.20"
    changed[2]["credit"] = "0.40"
    result = compare_fixture(replace(fixture, records=changed))
    assert result["status"] == "failed"
    assert result["production_imbalance_vouchers"] == []
    assert result["oracle"]["balances"]["Assets:Bank"] == "0.30"
    assert result["actual_balances"]["Assets:Bank"] == "0.40"


@pytest.mark.usefixtures("beancount_available")
def test_record_order_does_not_change_fixed_third_party_expected():
    fixture = fixtures()[1]
    result = compare_fixture(replace(fixture, records=list(reversed(fixture.records))))
    assert result["status"] == "passed"
    assert result["oracle"]["balances"]["Assets:Bank"] == "20000.00"


def test_same_account_same_voucher_postings_have_distinct_source_ids():
    fixture = fixtures()[0]
    assert len({record["id"] for record in fixture.records}) == 3
    assert {record["voucher_id"] for record in fixture.records} == {"V-CENTS"}
    changed = deepcopy(fixture.records)
    changed[1]["id"] = changed[0]["id"]
    with pytest.raises(ValueError, match="unique"):
        compare_fixture(replace(fixture, records=changed))


@pytest.mark.parametrize("extra", [
    'include "other.bean"', 'plugin "os"',
    'option "tolerance_multiplier" "100"',
    "2026-01-01 * \"V\"\n  Assets:Bank\n  Equity:Capital -1.00 CNY",
    "2026-01-01 * \"V\"\n  Assets:Bank 1.00 USD\n  Equity:Capital -1.00 USD",
    "2026-01-01 * \"V\"\n  Assets:Bank 1.001 CNY\n  Equity:Capital -1.001 CNY",
    "2026-01-01 * \"V\"\n  Assets:Bank 1.00 CNY @ 2.00 USD\n  Equity:Capital -1.00 CNY",
])
def test_non_equivalent_features_are_rejected_before_third_party_loading(extra):
    with pytest.raises(ValueError, match="subset"):
        _restrict_source(fixtures()[0].source + extra)


@pytest.mark.parametrize("mutation", [
    {"currency": "USD"}, {"kind": "bank"}, {"debit": 0.1}, {"credit": None},
    {"debit": "-0.10"}, {"credit": "0.001"}, {"account": "UNMAPPED"},
    {"business_date": "2026-02-01", "period": "2026-02"}, {"period": "2026-02"},
])
def test_record_scope_and_explicit_amount_contract_checked_before_comparing(mutation):
    fixture = fixtures()[0]
    changed = deepcopy(fixture.records)
    changed[0].update(mutation)
    with pytest.raises(ValueError):
        compare_fixture(replace(fixture, records=changed))


def test_missing_optional_dependency_is_not_a_success(monkeypatch):
    def unavailable(_):
        raise metadata.PackageNotFoundError("beancount")

    monkeypatch.setattr(metadata, "version", unavailable)
    result = run()
    assert result["status"] == "dependency_unavailable"
    assert result["passed"] == 0 and result["cases"] == []
    assert result["accounting_standard_oracle"] is False


def test_unpinned_dependency_version_requires_new_validation(monkeypatch):
    monkeypatch.setattr(metadata, "version", lambda _: "99.0.0")
    # Simulate the entire installed package metadata, including on fresh hosts
    # where this optional dependency is absent. No oracle is loaded in this path.
    monkeypatch.setattr(metadata, "metadata", lambda _: {"License": "test fixture only"})
    result = run()
    assert result["status"] == "unsupported_dependency_version"
    assert result["passed"] == 0 and result["cases"] == []


@pytest.mark.usefixtures("beancount_available")
def test_full_report_carries_actual_counts_and_preserves_professional_boundary():
    result = run()
    assert result["status"] == "passed" and result["case_count"] == 9
    assert result["valid_balance_comparisons"] == 8
    assert result["negative_imbalance_checks"] == 1
    assert result["balance_difference_count"] == 0
    assert result["accounting_standard_oracle"] is False
    assert result["real_business_accuracy_measured"] is None
