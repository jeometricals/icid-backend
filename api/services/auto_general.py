from typing import Any, Iterable, Optional
from uuid import UUID

from api.queries.idr_reports import (
    create_auto_general,
    delete_auto_general,
    get_general_report,
    list_non_general_main_reports,
    update_auto_general,
)
from api.queries.idrs import get_idr_by_id
from api.schemas.idr_report import ADDENDUM_TYPES, ReportType, label_for

# An auto-generated General appears once an IDR holds this many contributing
# reports; below it, an existing auto-General is removed.
AUTO_GENERAL_MIN_REPORTS = 2

# Always closes an auto-generated description: even a full summary is only a
# summary, so it points back to the source reports. Auto-Generals only — a
# manual General's description is the inspector's, untouched.
DESCRIPTION_FOOTER = "See the individual reports for the details of the work performed."


def _first_non_empty(values: Iterable[Any]) -> Any:
    """
    Pick the first value that is neither None nor a blank/whitespace-only string.
    Takes any iterable of values.
    Returns that value as-is, or "" if none qualifies.
    """
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value
        else:
            return value
    return ""


def _to_float(value: Any) -> Optional[float]:
    """
    Parse a pay quantity to a float, tolerating numeric strings.
    Takes any value (typically a string like "100" or "150.50").
    Returns the float, or None if it is blank or does not parse as a number.
    """
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _aggregate_description(children: list[dict[str, Any]]) -> str:
    """
    Compose the auto-General's Description of Work from the contributing children.
    Takes the contributing report rows in creation order.
    Returns one entry per report ("[Label]: [description]" or "[Label] work" when blank), blank-line separated, always ending with the fixed footer.
    """
    entries: list[str] = []
    for child in children:
        data = child.get("report_data") or {}
        label = label_for(child["report_type"])
        description = data.get("description")
        text = description.strip() if isinstance(description, str) else ""
        entries.append(f"{label}: {text}" if text else f"{label} work")
    entries.append(DESCRIPTION_FOOTER)
    return "\n\n".join(entries)


def _aggregate_pay_items(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Combine the contributing children's pay items, grouping by (itemNo, budgetCode).
    Takes the contributing report rows; quantities that all parse are summed and formatted to 2 decimals, otherwise the first non-empty raw value is kept.
    Returns one pay item dict per group, in first-seen order.
    """
    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    order: list[tuple[Any, Any]] = []

    for child in children:
        data = child.get("report_data") or {}
        for item in data.get("payItems") or []:
            if not isinstance(item, dict):
                continue
            key = (item.get("itemNo"), item.get("budgetCode"))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(item)

    combined: list[dict[str, Any]] = []
    for key in order:
        items = groups[key]
        item_no, budget_code = key
        quantities = [item.get("payQuantity") for item in items]
        parsed = [_to_float(quantity) for quantity in quantities]

        if parsed and all(value is not None for value in parsed):
            pay_quantity = "{:.2f}".format(sum(parsed))
        else:
            pay_quantity = _first_non_empty(quantities)

        combined.append({
            "itemNo": item_no,
            "budgetCode": budget_code,
            "description": _first_non_empty(item.get("description") for item in items),
            "payQuantity": pay_quantity,
            "quantityChk": _first_non_empty(item.get("quantityChk") for item in items),
        })

    return combined


def build_auto_general_data(children: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build the auto-General's report_data from the contributing (non-addendum, non-General) children.
    Takes the contributing report rows in creation order.
    Returns a dict with exactly the aggregated fields (description and payItems); no other keys, since regeneration full-replaces report_data and an auto-General is read-only.
    """
    return {
        "description": _aggregate_description(children),
        "payItems": _aggregate_pay_items(children),
    }


def _contributing_reports(idr_id: UUID) -> list[dict[str, Any]]:
    """
    Fetch the reports that feed the auto-summary: non-addendum, non-General, and not an addendum-by-nature type.
    Takes the IDR uuid.
    Returns the contributing report rows (empty when none).
    """
    reports = list_non_general_main_reports(idr_id) or []
    return [report for report in reports if report["report_type"] not in ADDENDUM_TYPES]


def regenerate_auto_general(idr_id: UUID) -> None:
    """
    Reconcile the IDR's auto-generated General against its contributing reports (cases C and E).
    Takes the IDR uuid; leaves an inspector-managed General untouched (case B) and never re-creates a dismissed one.
    Returns nothing; creates, updates or deletes the auto-General as needed.
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        return

    general = get_general_report(idr_id)

    # Case B: the inspector added the General themselves — the backend never touches it.
    if general is not None and not general["is_auto_generated"]:
        return

    children = _contributing_reports(idr_id)

    if len(children) >= AUTO_GENERAL_MIN_REPORTS:
        data = build_auto_general_data(children)
        if general is not None:
            update_auto_general(idr_id, data)          # Case C: refresh the auto-General.
        elif not idr["has_dismissed_auto_general"]:
            create_auto_general(idr_id, data)          # Case C: first auto-General.
        # Dismissed and none present: leave it be (case D aftermath).
    elif general is not None:
        delete_auto_general(idr_id)                    # Case E: too few children — remove the auto-General.
