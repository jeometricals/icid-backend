from typing import Any
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

# Main-report types that never feed the summary: the addendums by nature, and a Daily Site Patrol, a checklist of the
# site's upkeep with no description of work and no pay items. They don't count toward AUTO_GENERAL_MIN_REPORTS.
NON_CONTRIBUTING_TYPES = ADDENDUM_TYPES | {ReportType.DSP}

# Contributing types whose description stays out of the auto-General: the IDR export prints them on their own
# sheets. They still count toward AUTO_GENERAL_MIN_REPORTS.
MERGE_EXCLUDED_TYPES = frozenset({ReportType.AC.value})

# Always closes an auto-generated description: even a full summary is only a
# summary, so it points back to the source reports. Auto-Generals only — a
# manual General's description is the inspector's, untouched.
DESCRIPTION_FOOTER = "See the individual reports for the details of the work performed."


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


def build_auto_general_data(children: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build the auto-General's report_data from the contributing (non-addendum, non-General) children: a summary cover
    whose description points to each, leaving the MERGE_EXCLUDED_TYPES out (they still count). Its pay items are
    always empty: they live on the reports themselves (Slice X.3).
    Takes the contributing report rows in creation order.
    Returns a dict with exactly description and payItems ([]); no other keys, since regeneration full-replaces
    report_data and an auto-General is read-only.
    """
    merged = [child for child in children if child["report_type"] not in MERGE_EXCLUDED_TYPES]
    return {
        "description": _aggregate_description(merged),
        "payItems": [],
    }


def _contributing_reports(idr_id: UUID) -> list[dict[str, Any]]:
    """
    Fetch the reports that feed the auto-summary: non-addendum, non-General, and not of a NON_CONTRIBUTING_TYPES type.
    Takes the IDR uuid.
    Returns the contributing report rows (empty when none).
    """
    return contributing_reports(list_non_general_main_reports(idr_id) or [])


def contributing_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Keep the main reports that feed the auto-summary, leaving out the NON_CONTRIBUTING_TYPES.
    Takes non-addendum, non-General report rows.
    Returns those that contribute, in the order given.
    """
    return [report for report in reports if report["report_type"] not in NON_CONTRIBUTING_TYPES]


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
