"""
Budget against actual, by contract value: what a project's approved quantities are worth at their contract items'
unit prices, beside what those contract items were bid at.

compute_rollup works on rows already read (list_quantities_with_budget and list_project_contract_totals) and reads
nothing itself. Sums are kept exact and rounded once, where a figure is handed out.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

CENT = Decimal("0.01")
TENTH = Decimal("0.1")


def money(value: Optional[Decimal]) -> Optional[float]:
    """
    Round an amount of money for display.
    Takes the amount (None for one that isn't known).
    Returns it to 2 decimal places, halves rounded up, or None.
    """
    if value is None:
        return None
    return float(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))


def percent(actual: Decimal, budgeted: Decimal) -> Optional[float]:
    """
    Work out how much of a budget has been used.
    Takes the actual and the budgeted value.
    Returns the percentage to 1 decimal place, or None when nothing was budgeted.
    """
    if budgeted == 0:
        return None
    return float((Decimal(actual) / Decimal(budgeted) * 100).quantize(TENTH, rounding=ROUND_HALF_UP))


def compute_rollup(actuals: list[dict[str, Any]], contract_items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Roll a project's quantities up by contract value at three levels: per pay item, per budget code and for the project. Every contract item counts toward the budget, booked against or not; quantities no contract item matches have no value and are left out of every total.
    Takes the summed quantities (list_quantities_with_budget's rows) and every contract item of the project (list_project_contract_totals' rows).
    Returns {pay_items, totals_by_budget_code, project_total, unbudgeted_total}, values rounded to 2 decimal places and percentages to 1.
    """
    pay_items: list[dict[str, Any]] = []
    actual_by_code: dict[str, Decimal] = {}
    unbudgeted_codes: set[Optional[str]] = set()

    for row in actuals:
        item = {
            "pay_item_ref": row["pay_item_ref"],
            "description": row["description"],
            "unit": row["unit"],
            "budget_code": row["budget_code"],
            "actual_quantity": float(row["actual_quantity"]),
            "actual_value": None,
            "budgeted_quantity": None,
            "budgeted_value": None,
            "unit_price": None,
            "percent": None,
            "unbudgeted": bool(row["unbudgeted"]),
        }
        if row["unbudgeted"]:
            unbudgeted_codes.add(row["budget_code"])
        else:
            actual_value = row["actual_quantity"] * row["unit_price"]
            budgeted_value = row["budgeted_quantity"] * row["unit_price"]
            actual_by_code[row["budget_code"]] = actual_by_code.get(row["budget_code"], Decimal(0)) + actual_value
            item.update(
                actual_value=money(actual_value),
                budgeted_quantity=float(row["budgeted_quantity"]),
                budgeted_value=money(budgeted_value),
                unit_price=float(row["unit_price"]),
                percent=percent(actual_value, budgeted_value),
            )
        pay_items.append(item)

    budgeted_by_code: dict[str, Decimal] = {}
    for contract_item in contract_items:
        code = contract_item["budget_code"]
        budgeted_by_code[code] = budgeted_by_code.get(code, Decimal(0)) + contract_item["budgeted_value"]

    totals_by_budget_code = [
        {
            "budget_code": code,
            "actual_value": money(actual_by_code.get(code, Decimal(0))),
            "budgeted_value": money(budgeted_by_code[code]),
            "percent": percent(actual_by_code.get(code, Decimal(0)), budgeted_by_code[code]),
            "has_unbudgeted": code in unbudgeted_codes,
        }
        for code in sorted(budgeted_by_code)
    ]

    project_actual = sum(actual_by_code.values(), Decimal(0))
    project_budgeted = sum(budgeted_by_code.values(), Decimal(0))

    return {
        "pay_items": pay_items,
        "totals_by_budget_code": totals_by_budget_code,
        "project_total": {
            "actual_value": money(project_actual),
            "budgeted_value": money(project_budgeted),
            "percent": percent(project_actual, project_budgeted),
        },
        "unbudgeted_total": {
            "actual_value": None,
            "row_count": sum(1 for item in pay_items if item["unbudgeted"]),
        },
    }
