"""
An IDR's curb segments (curb billing): which it lists and in what state, what they roll up to per pay item, and the
pay items they make off-limits to manual entry, as idr_flags does for flags.

Before approval the list and the rollup are live, read from the segments as they are; once approved they are frozen:
the segments it claimed, and the from_segments quantity rows it wrote.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from api.queries.idr_segments import list_idr_segments
from api.queries.quantities import list_source_quantities
from api.services.idr_flags import CENT, is_frozen


class IdrSegmentError(Exception):
    """An IDR's segments couldn't be read."""


def _segments(idr: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Read the segments an IDR lists (see list_idr_segments).
    Takes the IDR dict.
    Returns the segment rows; raises IdrSegmentError when they can't be read.
    """
    rows = list_idr_segments(idr["idr_id"], is_frozen(idr))
    if rows is None:
        raise IdrSegmentError("Failed to load the IDR's segments")
    return rows


def _counted(segment: dict[str, Any]) -> bool:
    """
    Whether a segment counts for its IDR: attributed to it before approval, claimed by it after.
    Takes the segment row.
    Returns True when it counts.
    """
    return segment["state"] in ("attributed", "claimed")


def _rollup(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sum the segments that count per pay item, in LF, rounded to 2 decimal places (halves up), as approval sums them; a
    segment whose site overrides its pay item (overridden) adds nothing, the override billing instead.
    Takes the segment rows.
    Returns one line per pay item (pay_item_id, pay_item_ref, budget_code, description, unit, amount, segment_count), by item number then budget code.
    """
    lines: dict[UUID, dict[str, Any]] = {}
    for segment in segments:
        if not _counted(segment) or segment["amount"] is None or segment.get("overridden"):
            continue
        line = lines.setdefault(segment["pay_item_id"], {
            "pay_item_id": segment["pay_item_id"], "pay_item_ref": segment["pay_item_ref"],
            "budget_code": segment["budget_code"], "description": segment["pay_item_description"],
            "unit": segment["unit"], "amount": Decimal(0), "segment_count": 0,
        })
        line["amount"] += Decimal(segment["amount"])
        line["segment_count"] += 1
    for line in lines.values():
        line["amount"] = line["amount"].quantize(CENT, rounding=ROUND_HALF_UP)
    return sorted(lines.values(), key=lambda line: (line["pay_item_ref"] or "", line["budget_code"] or ""))


def segment_pay_items(idr: dict[str, Any], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    An IDR's segment rollup: live before approval, the frozen from_segments rows after.
    Takes the IDR dict and its segment rows (unused once it is approved).
    Returns the lines (see _rollup); raises IdrSegmentError when the frozen rows can't be read.
    """
    if not is_frozen(idr):
        return _rollup(segments)
    rows = list_source_quantities(idr["idr_id"], "from_segments")
    if rows is None:
        raise IdrSegmentError("Failed to load the IDR's segment quantities")
    return rows


def idr_segments(idr: dict[str, Any]) -> dict[str, Any]:
    """
    Gather what an IDR's segments panel shows: the segments with their states, the counts, the rollup and how many counted segments add nothing to it.
    Takes the IDR dict.
    Returns the dict an IdrSegments is made of; raises IdrSegmentError when anything can't be read.
    """
    segments = _segments(idr)
    states = [segment["state"] for segment in segments]
    return {
        "idr_id": idr["idr_id"], "status": idr["status"], "task_order_id": idr.get("task_order_id"),
        "frozen": is_frozen(idr),
        "counts": {
            "attributed": sum(_counted(segment) for segment in segments), "excluded": states.count("excluded"),
            "claimed_elsewhere": states.count("claimed_elsewhere"), "total": len(segments),
        },
        "segments": segments,
        "pay_items": segment_pay_items(idr, segments),
        "unmapped": sum(_counted(segment) and segment["amount"] is None for segment in segments),
    }


def attributed_segment_ids(idr: dict[str, Any]) -> list[UUID]:
    """
    List the segments an IDR would claim if it were approved now: those attributed to it.
    Takes the IDR dict (not approved).
    Returns their uuids; raises IdrSegmentError when they can't be read.
    """
    return [segment["segment_id"] for segment in _segments(idr) if segment["state"] == "attributed"]


def segment_pay_item_keys(idr: dict[str, Any]) -> set[tuple[str, str]]:
    """
    List the pay items an IDR's segments fill, which its reports can't also hold: the item number and budget code of every pay item of a segment that counts for it.
    Takes the IDR dict.
    Returns the (item number, budget code) pairs; raises IdrSegmentError when the segments can't be read.
    """
    return {(segment["pay_item_ref"], segment["budget_code"]) for segment in _segments(idr)
            if _counted(segment) and segment["pay_item_id"] is not None}


def manual_segment_conflicts(idr: dict[str, Any], pay_items: Any) -> list[str]:
    """
    Find the pay items a report would hold by hand that the IDR's curb segments already fill (item number and budget code matched exactly, spaces around them ignored).
    Takes the IDR dict and the report's payItems as sent (anything that isn't a list counts as none).
    Returns a message per such item, in the report's order (empty when there is none); raises IdrSegmentError when the segments can't be read.
    """
    entries = [item for item in pay_items if isinstance(item, dict)] if isinstance(pay_items, list) else []
    if not entries:
        return []
    filled = segment_pay_item_keys(idr)
    conflicts = []
    for item in entries:
        key = (str(item.get("itemNo") or "").strip(), str(item.get("budgetCode") or "").strip())
        if key in filled:
            conflicts.append(f"Pay item {key[0]} ({key[1]}) comes from this IDR's curb segments; "
                             "remove it from the report, or exclude its segments from the IDR")
    return conflicts


def segment_rollup(idr: dict[str, Any]) -> list[dict[str, Any]]:
    """
    An IDR's curb segment rollup per pay item: live before approval, the frozen from_segments rows after.
    Takes the IDR dict.
    Returns the lines (pay_item_ref, budget_code, description, unit, amount); raises IdrSegmentError when they can't be read.
    """
    return segment_pay_items(idr, [] if is_frozen(idr) else _segments(idr))


def export_segment_lines(idr: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Lay an IDR's segment rollup out as pay items for the export, one row per pay item (item number, budget code, amount, unit, description), to print under the "PIR Flags" heading with the flags' lines.
    Takes the IDR dict.
    Returns the rows, as a report's payItems are; empty when the rollup is; raises IdrSegmentError when it can't be read.
    """
    lines = segment_rollup(idr)
    return [
        {"itemNo": line["pay_item_ref"], "budgetCode": line["budget_code"],
         "payQuantity": f"{Decimal(line['amount']).quantize(CENT, rounding=ROUND_HALF_UP):f}", "unit": line["unit"],
         "description": line["description"]}
        for line in lines
    ]
