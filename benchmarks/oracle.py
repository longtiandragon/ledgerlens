"""Small independent rational oracle. Deliberately imports no application code."""
from fractions import Fraction


def reference_balances(records: list[dict]) -> dict[str, str]:
    totals: dict[str, Fraction] = {}
    for row in records:
        if row.get("kind") not in {"journal", "opening"}:
            continue
        account = row.get("account", "")
        if "debit" in row or "credit" in row:
            value = Fraction(row.get("debit", "0")) - Fraction(row.get("credit", "0"))
        else:
            value = Fraction(row.get("amount", "0"))
        totals[account] = totals.get(account, Fraction(0)) + value
    result = {}
    for key, value in sorted(totals.items()):
        cents = value * 100
        if cents.denominator != 1:
            raise ValueError("Subcent value is outside this reference fixture contract")
        integer = cents.numerator
        result[key] = ("-" if integer < 0 else "") + f"{abs(integer)//100}.{abs(integer)%100:02d}"
    return result
