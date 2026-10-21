"""
An IDR's pay items by source (Slice V1): what its flags, its curb segments, the pay items entered on its sites and its
reports' own (hand-entered) pay items each add to a pay item, and the total. Also the check that keeps a report from
holding by hand a pay item its sites already hold, and the lines the export prints for a report's sites.

Pay items are matched by item number and budget code, spaces around them ignored, as the flags' check matches them.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional
from uuid import UUID

from api.schemas.property import display_address_of
from api.services.idr_flags import flag_rollup, is_frozen
from api.services.idr_segments import segment_rollup
from api.services.quantities import canonical_unit, extract_rows

# The heading the export prints over a report's site pay items, as a pay item row of its own
SITE_EXPORT_HEADING = "Imported Sites"
CENT = Decimal("0.01")
_SOURCES = ("flags", "segments", "sites", "manual")


def _key(item_no: Any, budget_code: Any) -> tuple[str, str]:
    """
    Key a pay item by its number and budget code, trimmed.
    Takes the two as stored or typed.
    Returns the (item number, budget code) pair, '' for either missing.
    """
    return (str(item_no or "").strip(), str(budget_code or "").strip())


def site_label(item: dict[str, Any]) -> str:
    """
    Label the site a site pay item is on, as its property is labelled.
    Takes the pay item dict with its site's property fields (idr_site_pay_items).
    Returns the label.
    """
    return display_address_of(item["kind"], item.get("address"), item.get("intersection_street_1"),
                              item.get("intersection_street_2"), item.get("quadrant"), item.get("facing_street"))


def _amount(value: Decimal) -> float:
    """
    Round an amount for the response: 2 decimal places, halves up.
    Takes the exact amount.
    Returns it as a float.
    """
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def pay_item_sources(idr: dict[str, Any], reports: list[dict[str, Any]], site_items: list[dict[str, Any]],
                     report_id: Optional[UUID] = None) -> dict[str, Any]:
    """
    Sum an IDR's pay items over their four sources: its flags and curb segments (live before approval, frozen after),
    the pay items entered on its sites, and its reports' own pay items (every General, SWCB and AC report but an
    auto-generated General; only report_id's when it is given). A source that adds nothing to a pay item is 0.
    Takes the IDR dict, its reports, its site pay items (idr_site_pay_items) and the report to take hand-entered items from (None for all).
    Returns the dict a PayItemSourcesData is made of, its pay items by item number then budget code; raises IdrFlagError or IdrSegmentError when the drawings can't be read.
    """
    lines: dict[tuple[str, str], dict[str, Any]] = {}

    def line(item_no: Any, budget_code: Any, description: Any, unit: Any) -> dict[str, Any]:
        """Find or start a pay item's line, keeping the first description and unit given for it."""
        entry = lines.setdefault(_key(item_no, budget_code), {
            "pay_item_ref": _key(item_no, budget_code)[0] or None, "budget_code": _key(item_no, budget_code)[1] or None,
            "description": None, "unit": None, **{source: Decimal(0) for source in _SOURCES},
            "site_lines": [], "manual_lines": [],
        })
        entry["description"] = entry["description"] or description
        entry["unit"] = entry["unit"] or canonical_unit(unit)
        return entry

    for source, rollup in (("flags", flag_rollup(idr)), ("segments", segment_rollup(idr))):
        for row in rollup:
            line(row["pay_item_ref"], row["budget_code"], row["description"], row["unit"])[source] += Decimal(str(row["amount"]))
    for item in site_items:
        entry = line(item["pay_item_ref"], item["budget_code"], item["description"], item["unit"])
        entry["sites"] += Decimal(str(item["quantity"]))
        entry["site_lines"].append({"amount": float(item["quantity"]), "report_id": item["report_id"],
                                    "site_id": item["report_site_id"], "property_id": item["property_id"],
                                    "label": site_label(item)})
    for report in reports:
        if report_id is not None and report["report_id"] != report_id:
            continue
        for row in extract_rows(idr, [report]):
            entry = line(row["pay_item_ref"], row["budget_code"], row["description"], row["unit"])
            entry["manual"] += row["amount"]
            entry["manual_lines"].append({"amount": float(row["amount"]), "report_id": report["report_id"]})

    items = []
    for entry in sorted(lines.values(), key=lambda e: (e["pay_item_ref"] or "", e["budget_code"] or "")):
        total = sum(entry[source] for source in _SOURCES)
        items.append({**entry, "total": _amount(total), **{source: _amount(entry[source]) for source in _SOURCES}})
    return {"idr_id": idr["idr_id"], "report_id": report_id, "frozen": is_frozen(idr), "pay_items": items}


def site_entry_conflicts(site_items: list[dict[str, Any]], pay_items: Any) -> list[str]:
    """
    Find the pay items a report would hold by hand that the IDR's sites already hold (entered on a site), so approving
    would count them twice.
    Takes the IDR's site pay items (idr_site_pay_items) and the report's payItems as sent (anything that isn't a list counts as none).
    Returns a message per such item, naming it and the sites holding it, in the report's order (empty when there is none).
    """
    entries = [item for item in pay_items if isinstance(item, dict)] if isinstance(pay_items, list) else []
    sites: dict[tuple[str, str], list[str]] = {}
    for item in site_items:
        held = sites.setdefault(_key(item["pay_item_ref"], item["budget_code"]), [])
        if site_label(item) not in held:
            held.append(site_label(item))
    conflicts = []
    for item in entries:
        key = _key(item.get("itemNo"), item.get("budgetCode"))
        if key in sites:
            conflicts.append(f"Pay item {key[0]} ({key[1]}) is entered on this IDR's imported "
                             f"{'site' if len(sites[key]) == 1 else 'sites'} {', '.join(sites[key])}; "
                             "remove it from the report, or from those sites")
    return conflicts


def site_export_lines(site_items: list[dict[str, Any]], report_id: UUID) -> list[dict[str, Any]]:
    """
    Lay the pay items entered on a report's sites out as pay items for the export: an "Imported Sites" heading row,
    then one row per pay item summed over the sites (item number, budget code, amount, unit, description), by item
    number then budget code.
    Takes the IDR's site pay items (idr_site_pay_items) and the report's uuid.
    Returns the rows, as a report's payItems are; empty when the report's sites hold none.
    """
    lines: dict[tuple[str, str], dict[str, Any]] = {}
    for item in site_items:
        if item["report_id"] != report_id:
            continue
        key = _key(item["pay_item_ref"], item["budget_code"])
        entry = lines.setdefault(key, {"itemNo": key[0], "budgetCode": key[1], "amount": Decimal(0),
                                       "unit": canonical_unit(item["unit"]), "description": item["description"]})
        entry["amount"] += Decimal(str(item["quantity"]))
    if not lines:
        return []
    return [{"description": SITE_EXPORT_HEADING}] + [
        {"itemNo": entry["itemNo"], "budgetCode": entry["budgetCode"], "payQuantity": f"{entry['amount'].normalize():f}",
         "unit": entry["unit"], "description": entry["description"]}
        for _, entry in sorted(lines.items())
    ]
