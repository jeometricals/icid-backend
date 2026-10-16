"""
Progress from flags (R1g): how much of each pay item a flag can fill is approved, in flight or unclaimed, by value at
its unit price, for a project, a task order or a property. A pay item is listed when a flag in scope is on it, or,
when asked for, whether or not.

compute_progress works on rows already read (list_flag_pay_items and list_flag_progress) and reads nothing itself.
Quantities are handed out unrounded; values are rounded to cents and percentages to 1 decimal place, once, where a
figure is handed out.
"""

from decimal import Decimal
from typing import Any, Iterable, Optional
from uuid import UUID

from api.schemas.property import display_address_of
from api.services.budget_rollup import money, percent

STATES = ("approved", "in_flight", "unclaimed")


def _address(row: dict[str, Any]) -> str:
    """
    Label a property from a row carrying its kind and address fields (property_kind or kind).
    Takes the row.
    Returns its display address.
    """
    return display_address_of(row.get("property_kind") or row["kind"], row["address"], row["intersection_street_1"],
                              row["intersection_street_2"], row["quadrant"], row["facing_street"])


def _bucket(quantity: Decimal, flag_count: int, unit_price: Decimal) -> dict[str, Any]:
    """
    Hand out what a pay item's flags in one state come to.
    Takes their summed quantity, their count and the pay item's unit price.
    Returns {quantity, flag_count, value}.
    """
    return {"quantity": float(quantity), "flag_count": flag_count, "value": money(quantity * unit_price)}


def _rollup(pay_items: list[dict[str, Any]], rows: Iterable[dict[str, Any]], include_empty: bool) -> dict[str, Any]:
    """
    Roll one scope's flag rows up per pay item and overall. A pay item no flag of the scope is on is listed only when asked for.
    Takes the pay items (list_flag_pay_items' rows), the scope's rows (list_flag_progress's) and whether to list every pay item.
    Returns {pay_items, unmapped_flag_count, overall}.
    """
    sums = {item["pay_item_id"]: {state: [Decimal(0), 0] for state in STATES} for item in pay_items}
    unmapped = 0
    for row in rows:
        if row["state"] == "unmapped":
            unmapped += row["flag_count"]
        elif row["pay_item_id"] in sums:
            total = sums[row["pay_item_id"]][row["state"]]
            total[0] += Decimal(row["quantity"])
            total[1] += row["flag_count"]

    lines, approved_value, live_value = [], Decimal(0), Decimal(0)
    for item in pay_items:
        price = Decimal(item["unit_price"])
        states = sums[item["pay_item_id"]]
        quantity = sum((states[state][0] for state in STATES), Decimal(0))
        count = sum(states[state][1] for state in STATES)
        approved_value += states["approved"][0] * price
        live_value += quantity * price
        if not (count or include_empty):
            continue
        lines.append({
            "pay_item_id": item["pay_item_id"], "item_no": item["item_no"], "budget_code": item["budget_code"],
            "description": item["description"], "unit": item["unit"], "unit_price": float(price),
            **{state: _bucket(*states[state], price) for state in STATES},
            "live_total": _bucket(quantity, count, price),
            "approved_pct": percent(states["approved"][0], quantity),
        })

    return {
        "pay_items": lines,
        "unmapped_flag_count": unmapped,
        "overall": {"approved_value": money(approved_value), "live_total_value": money(live_value),
                    "approved_pct": percent(approved_value, live_value)},
    }


def _unmapped_by_property(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Count the unmapped flags of each property that has any.
    Takes the flag rows.
    Returns {property_id, display_address, flag_count} per such property, by display address.
    """
    found: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        if row["state"] != "unmapped":
            continue
        entry = found.setdefault(row["property_id"], {
            "property_id": row["property_id"], "display_address": _address(row), "flag_count": 0})
        entry["flag_count"] += row["flag_count"]
    return sorted(found.values(), key=lambda entry: entry["display_address"])


def compute_progress(
    pay_items: list[dict[str, Any]], rows: list[dict[str, Any]], properties: Optional[list[dict[str, Any]]] = None,
    include_empty: bool = False,
) -> dict[str, Any]:
    """
    Roll a scope's flags up per pay item and overall, and, for a task order, per property; each rollup lists the pay items its own flags are on, or every one when asked for.
    Takes the pay items a flag can fill (list_flag_pay_items), the scope's flag rows (list_flag_progress), for a task order its live bundled properties (each with id, kind and address fields) in the order to list them, and whether to list every pay item.
    Returns {pay_items, unmapped_flag_count, unmapped_by_property, overall}, and with properties also {properties, site_count, sites_started}.
    """
    result = {**_rollup(pay_items, rows, include_empty), "unmapped_by_property": _unmapped_by_property(rows)}
    if properties is None:
        return result

    drilled = []
    for prop in properties:
        property_id = UUID(str(prop["id"]))
        own = [row for row in rows if row["property_id"] == property_id]
        drilled.append({"property_id": property_id, "display_address": _address(prop), **_rollup(pay_items, own, include_empty)})
    started = {row["property_id"] for row in rows if row["state"] == "approved" and row["flag_count"]}
    return {
        **result,
        "properties": drilled,
        "site_count": len(drilled),
        "sites_started": sum(entry["property_id"] in started for entry in drilled),
    }
