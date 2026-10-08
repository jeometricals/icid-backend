"""
Quantities: an IDR's pay items as rows of icid.quantities.

extract_rows reads the pay items off an IDR's reports as they stand (after any reviewer's edits) and readies one row
each: the amount parsed to a number, the unit in its short form. write_quantities replaces the IDR's rows with them.
Nothing calls either yet; final approval will.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

from api.queries.quantities import replace_quantities
from api.schemas.idr_report import ReportType

logger = logging.getLogger(__name__)

# The report types whose form has a Pay Items table. A Concrete Truck & Mix Info and a Concrete Cylinder Data report
# have none.
PAY_ITEM_REPORT_TYPES = (ReportType.GEN.value, ReportType.SWCB.value, ReportType.AC.value)

# A unit's short form, by the unit with its periods and spaces dropped, in capitals: "L.F." and "lf" are both LF.
# Reports keep the unit as the catalog or the inspector wrote it; totals only add up when a quantity's is canonical.
UNIT_FORMS = {"LF": "LF", "SF": "SF", "CY": "CY", "SY": "SY", "TN": "TN", "TON": "TN", "TONS": "TN", "T": "TN",
              "EA": "EA", "EACH": "EA", "LS": "LS"}

# A quantity as an inspector types one: digits, with or without commas between its thousands, and a decimal part
_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|[-+]?\.\d+")

# The units already reported as unknown, so each is logged once per process
_unknown_units: set[str] = set()


def _unit_key(unit: str) -> str:
    """
    Reduce a unit to what UNIT_FORMS is keyed by.
    Takes the unit's text.
    Returns it without periods or spaces, in capitals.
    """
    return re.sub(r"[.\s]", "", unit).upper()


def canonical_unit(unit: Any) -> Optional[str]:
    """
    Give a pay item's unit in its short form.
    Takes the unit as the report stores it, e.g. "L.F.", "Ton" or "each".
    Returns e.g. "LF", "TN" or "EA"; a unit that isn't in UNIT_FORMS comes back as written, trimmed (logged once per process); None when blank or missing.
    """
    text = str(unit).strip() if unit is not None else ""
    if not text:
        return None
    known = UNIT_FORMS.get(_unit_key(text))
    if known is None and text not in _unknown_units:
        _unknown_units.add(text)
        logger.warning("unknown pay-item unit %r kept as written", text)
    return known or text


def parse_amount(raw: Any) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Read a pay quantity as a number. Spaces around it, commas between its thousands and a known unit typed after it ("125.5 SF") are tolerated; nothing else is.
    Takes the quantity as the report stores it (text, or a number).
    Returns (the amount, the unit that was typed after it or None); the amount is None when the quantity is blank or isn't a number.
    """
    if raw is None or isinstance(raw, bool):
        return None, None
    text = str(raw).strip()
    number = _NUMBER.match(text)
    if number is None:
        return None, None
    suffix = text[number.end():].strip()
    if suffix and _unit_key(suffix) not in UNIT_FORMS:
        return None, None
    try:
        amount = Decimal(number.group(0).replace(",", ""))
    except InvalidOperation:
        return None, None
    return (amount, suffix or None) if amount.is_finite() else (None, None)


def _text(value: Any) -> Optional[str]:
    """
    Normalise one of a pay item's text fields.
    Takes the value.
    Returns the trimmed text, or None when blank or missing.
    """
    text = str(value).strip() if value is not None else ""
    return text or None


def extract_rows(idr: dict[str, Any], reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Read an IDR's pay items as quantity rows: every item of every General, SWCB and AC report but an auto-generated General, whose items are sums of the other reports'. An item whose quantity is blank or isn't a number is left out, with a warning.
    Takes the IDR row and its reports.
    Returns one row per item, in report and item order: project_id, idr_id, report_date, reporter_uuid, report_type, pay_item_ref (the item number, None without one), budget_code, description, amount (a Decimal) and unit (its short form).
    """
    rows = []
    for report in reports:
        data = report.get("report_data")
        items = data.get("payItems") if isinstance(data, dict) else None
        if report["report_type"] not in PAY_ITEM_REPORT_TYPES or report.get("is_auto_generated") \
                or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = item.get("payQuantity")
            amount, typed_unit = parse_amount(raw)
            if amount is None:
                logger.warning("skipping pay-item row on IDR %s, report %s: quantity %r is not a number",
                               idr["idr_id"], report.get("report_id"), raw)
                continue
            if typed_unit:
                logger.warning("pay-item row on IDR %s, report %s: quantity %r read as %s",
                               idr["idr_id"], report.get("report_id"), raw, amount)
            rows.append({
                "project_id": idr["project_id"],
                "idr_id": idr["idr_id"],
                "report_date": idr["report_date"],
                "reporter_uuid": idr["reporter_uuid"],
                "report_type": report["report_type"],
                "pay_item_ref": _text(item.get("itemNo")),
                "budget_code": _text(item.get("budgetCode")),
                "description": _text(item.get("description")),
                "amount": amount,
                "unit": canonical_unit(item.get("unit")),
            })
    return rows


def write_quantities(idr_id: UUID, rows: list[dict[str, Any]]) -> Optional[int]:
    """
    Replace an IDR's quantity rows with the given ones, in one statement.
    Takes the IDR uuid and the rows, from extract_rows (an empty list leaves the IDR with none).
    Returns how many rows were written, or None on failure.
    """
    return replace_quantities(idr_id, rows)
