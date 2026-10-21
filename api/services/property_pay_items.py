"""
A property's pay items reported across IDRs (Slice V5): each pay item's total, and what each IDR reports of it.

A flag or curb segment that two open IDRs both cover is listed under each but counted once in the total: it is built
once. An approved IDR's claim is the only one a flag has, so it never doubles.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

CENT = Decimal("0.01")


def _rounded(amount: Decimal) -> float:
    """
    Round an amount for the response: 2 decimal places, halves up.
    Takes the exact amount.
    Returns it as a float.
    """
    return float(amount.quantize(CENT, rounding=ROUND_HALF_UP))


def pay_item_summary(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group a property's reported lines by pay item: the total (a flag or segment once, however many IDRs cover it), and
    one entry per IDR with its number, work date, status, inspector and quantity, newest work date first.
    Takes the rows of property_pay_item_lines.
    Returns the pay items (contract_item_id, pay_item_ref, budget_code, description, unit, total, idrs) by item number then budget code.
    """
    items: dict[UUID, dict[str, Any]] = {}
    for line in lines:
        item = items.setdefault(line["contract_item_id"], {
            "contract_item_id": line["contract_item_id"], "pay_item_ref": line["item_no"],
            "budget_code": line["budget_code"], "description": line["description"], "unit": line["unit"],
            "total": Decimal(0), "counted": set(), "idrs": {},
        })
        amount = Decimal(str(line["amount"]))
        element = (line["source"], line["element_id"])
        if element not in item["counted"]:
            item["counted"].add(element)
            item["total"] += amount
        entry = item["idrs"].setdefault(line["idr_id"], {
            "idr_id": line["idr_id"], "idr_number": line["idr_number"], "report_date": line["report_date"],
            "status": line["status"], "inspector": line["reporter_name"], "quantity": Decimal(0),
        })
        entry["quantity"] += amount
    out = []
    for item in sorted(items.values(), key=lambda i: (i["pay_item_ref"] or "", i["budget_code"] or "")):
        entries = sorted(item["idrs"].values(), key=lambda e: (e["report_date"], e["idr_number"] or ""), reverse=True)
        out.append({
            **{key: item[key] for key in ("contract_item_id", "pay_item_ref", "budget_code", "description", "unit")},
            "total": _rounded(item["total"]),
            "idrs": [{**entry, "quantity": _rounded(entry["quantity"])} for entry in entries],
        })
    return out
