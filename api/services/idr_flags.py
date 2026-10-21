"""
An IDR's flags (R1f): which it lists and in what state, what they roll up to per pay item, the pay items they make
off-limits to manual entry, and the lines the export prints under "PIR Flags".

Before approval the list and the rollup are live, read from the flags as they are; once approved they are frozen: the
flags it claimed, and the from_flags quantity rows it wrote.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from api.queries.idr_flags import list_idr_flags
from api.queries.quantities import list_flag_quantities

# The heading the export prints over an IDR's flag lines, as a pay item row of its own
EXPORT_HEADING = "PIR Flags"
CENT = Decimal("0.01")


class IdrFlagError(Exception):
    """An IDR's flags couldn't be read."""


def is_frozen(idr: dict[str, Any]) -> bool:
    """
    Whether an IDR's flags are frozen: it is approved, so they are the ones it claimed.
    Takes the IDR dict.
    Returns True for an approved IDR.
    """
    return idr["status"] == "approved"


def _flags(idr: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Read the flags an IDR lists (see list_idr_flags).
    Takes the IDR dict.
    Returns the flag rows; raises IdrFlagError when they can't be read.
    """
    rows = list_idr_flags(idr["idr_id"], is_frozen(idr))
    if rows is None:
        raise IdrFlagError("Failed to load the IDR's flags")
    return rows


def _counted(flag: dict[str, Any]) -> bool:
    """
    Whether a flag counts for its IDR: attributed to it before approval, claimed by it after.
    Takes the flag row.
    Returns True when it counts.
    """
    return flag["state"] in ("attributed", "claimed")


def _rollup(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sum the flags that count per pay item, in its unit, rounded to 2 decimal places (halves up), as approval sums them.
    Takes the flag rows.
    Returns one line per pay item (pay_item_id, pay_item_ref, budget_code, description, unit, amount, flag_count), by item number then budget code.
    """
    lines: dict[UUID, dict[str, Any]] = {}
    for flag in flags:
        if not _counted(flag) or flag["amount"] is None:
            continue
        line = lines.setdefault(flag["pay_item_id"], {
            "pay_item_id": flag["pay_item_id"], "pay_item_ref": flag["pay_item_ref"],
            "budget_code": flag["budget_code"], "description": flag["pay_item_description"], "unit": flag["unit"],
            "amount": Decimal(0), "flag_count": 0,
        })
        line["amount"] += Decimal(flag["amount"])
        line["flag_count"] += 1
    for line in lines.values():
        line["amount"] = line["amount"].quantize(CENT, rounding=ROUND_HALF_UP)
    return sorted(lines.values(), key=lambda line: (line["pay_item_ref"] or "", line["budget_code"] or ""))


def _pay_items(idr: dict[str, Any], flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    An IDR's flag rollup: live before approval, the frozen from_flags rows after.
    Takes the IDR dict and its flag rows.
    Returns the lines (see _rollup); raises IdrFlagError when the frozen rows can't be read.
    """
    if not is_frozen(idr):
        return _rollup(flags)
    rows = list_flag_quantities(idr["idr_id"])
    if rows is None:
        raise IdrFlagError("Failed to load the IDR's flag quantities")
    return rows


def idr_flags(idr: dict[str, Any]) -> dict[str, Any]:
    """
    Gather what an IDR's flags panel shows: the flags with their states, the counts, the rollup and how many counted flags add nothing to it.
    Takes the IDR dict.
    Returns the dict an IdrFlags is made of; raises IdrFlagError when anything can't be read.
    """
    flags = _flags(idr)
    states = [flag["state"] for flag in flags]
    return {
        "idr_id": idr["idr_id"], "status": idr["status"], "task_order_id": idr.get("task_order_id"),
        "frozen": is_frozen(idr),
        "counts": {
            "attributed": sum(_counted(flag) for flag in flags), "excluded": states.count("excluded"),
            "claimed_elsewhere": states.count("claimed_elsewhere"), "total": len(flags),
        },
        "flags": flags,
        "pay_items": _pay_items(idr, flags),
        "unmapped": sum(_counted(flag) and flag["amount"] is None for flag in flags),
    }


def attributed_flag_ids(idr: dict[str, Any]) -> list[UUID]:
    """
    List the flags an IDR would claim if it were approved now: those attributed to it.
    Takes the IDR dict (not approved).
    Returns their uuids; raises IdrFlagError when they can't be read.
    """
    return [flag["flag_id"] for flag in _flags(idr) if flag["state"] == "attributed"]


def flag_pay_item_keys(idr: dict[str, Any]) -> set[tuple[str, str]]:
    """
    List the pay items an IDR's flags fill, which its reports can't also hold: the item number and budget code of every pay item of a flag that counts for it.
    Takes the IDR dict.
    Returns the (item number, budget code) pairs; raises IdrFlagError when the flags can't be read.
    """
    return {(flag["pay_item_ref"], flag["budget_code"]) for flag in _flags(idr)
            if _counted(flag) and flag["pay_item_id"] is not None}


def manual_entry_conflicts(idr: dict[str, Any], pay_items: Any) -> list[str]:
    """
    Find the pay items a report would hold by hand that the IDR's flags already fill (item number and budget code matched exactly, spaces around them ignored).
    Takes the IDR dict and the report's payItems as sent (anything that isn't a list counts as none).
    Returns a message per such item, in the report's order (empty when there is none); raises IdrFlagError when the flags can't be read.
    """
    entries = [item for item in pay_items if isinstance(item, dict)] if isinstance(pay_items, list) else []
    if not entries:
        return []
    filled = flag_pay_item_keys(idr)
    conflicts = []
    for item in entries:
        key = (str(item.get("itemNo") or "").strip(), str(item.get("budgetCode") or "").strip())
        if key in filled:
            conflicts.append(manual_entry_message(*key))
    return conflicts


def manual_entry_message(item_no: str, budget_code: str) -> str:
    """
    Say why a pay item can't be entered by hand.
    Takes its item number and budget code.
    Returns the message.
    """
    return (f"Pay item {item_no} ({budget_code}) comes from this IDR's PIR flags; "
            "remove it from the report, or exclude its flags from the IDR")


def flag_rollup(idr: dict[str, Any]) -> list[dict[str, Any]]:
    """
    An IDR's flag rollup per pay item: live before approval, the frozen from_flags rows after.
    Takes the IDR dict.
    Returns the lines (pay_item_ref, budget_code, description, unit, amount); raises IdrFlagError when they can't be read.
    """
    return _pay_items(idr, [] if is_frozen(idr) else _flags(idr))


def export_lines(idr: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Lay an IDR's flag rollup out as pay items for the export: a "PIR Flags" heading row, then one row per pay item (item number, budget code, amount, unit, description).
    Takes the IDR dict.
    Returns the rows, as a report's payItems are; empty when the rollup is; raises IdrFlagError when it can't be read.
    """
    lines = flag_rollup(idr)
    if not lines:
        return []
    return [{"description": EXPORT_HEADING}] + [
        {"itemNo": line["pay_item_ref"], "budgetCode": line["budget_code"],
         "payQuantity": f"{Decimal(line['amount']).quantize(CENT, rounding=ROUND_HALF_UP):f}", "unit": line["unit"],
         "description": line["description"]}
        for line in lines
    ]
