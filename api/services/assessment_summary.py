"""
The Assessment Summary (R1i): a project's assessable quantities and amounts per property and month of construction,
an .xlsx workbook with one tab per month that has any, for the OE or RE to send DOT each month.

Each flag is measured exactly as the Construction Report measures it (construction_measure.flag_areas), so a
property's row adds up to its PDFs. A flag's month is its property's Date Constructed (page 1's title block), or the
day its IDR was approved when that isn't typed (counted in the tab's footer). 7" assessed as 4" counts in the 4"
columns at the 4" rate, noted in Remarks. Values are written whole; the cells show two decimals.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from api.schemas.property import display_address_of
from api.services.construction_measure import ZERO, flag_areas
from api.services.export_common import signed_date

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MONTH = re.compile(r"^(\d{4})-(\d{2})$")
SF_FORMAT = "#,##0.00"
MONEY_FORMAT = "$#,##0.00"
DASH = "—"
COLUMNS = ("Property Address", "CB", '4" Assessable (SF)', '4" Non-Assessable (SF)', '4" Total (SF)', '4" Amount ($)',
           '7" Assessable (SF)', '7" Non-Assessable (SF)', '7" Total (SF)', '7" Amount ($)', "Grand Total ($)",
           "Remarks")
COLUMN_WIDTHS = (34, 8, 14, 16, 12, 14, 14, 16, 12, 14, 15, 40)
# Where the amounts and the square feet are, by column number
MONEY_COLUMNS = (6, 10, 11)
SF_COLUMNS = (3, 4, 5, 7, 8, 9)
TOTALS_FILL = PatternFill("solid", fgColor="D9D9D9")
NO_ACTIVITY = "No activity"
EMPTY_PROJECT = "No approved IDRs in this project yet."
EMPTY_RANGE = "No approved IDRs in the selected range."


class MonthError(ValueError):
    """A month filter that isn't YYYY-MM, or a range that runs backwards; the route's 400."""


def parse_month(value: Optional[str], name: str) -> Optional[date]:
    """
    Read a month filter.
    Takes the text sent (None or blank: no filter) and its name, for the message.
    Returns the month's first day, or None; raises MonthError for anything but YYYY-MM with a month 01 to 12.
    """
    if value is None or not value.strip():
        return None
    match = MONTH.match(value.strip())
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise MonthError(f"{name} must be YYYY-MM")
    return date(int(match.group(1)), int(match.group(2)), 1)


@dataclass
class SummaryRow:
    """One property in one month: its square feet and amounts by pay type, and what Remarks says."""

    property_id: UUID
    address: str
    community_board: Optional[str] = None
    four_assessable: Decimal = ZERO
    four_non_assessable: Decimal = ZERO
    four_total: Decimal = ZERO
    seven_assessable: Decimal = ZERO
    seven_non_assessable: Decimal = ZERO
    seven_total: Decimal = ZERO
    built_7_billed_4: Decimal = ZERO
    used_approval_date: bool = False
    four_amount: Optional[Decimal] = None
    seven_amount: Optional[Decimal] = None
    grand_total: Optional[Decimal] = None
    remarks: list[str] = field(default_factory=list)


def _month_of(flag: dict[str, Any]) -> tuple[Optional[date], bool]:
    """
    The month a flag counts in: its property's Date Constructed, else the day its IDR was approved in New York.
    Takes the flag row.
    Returns (the month's first day, whether the approval date stood in); (None, True) with neither.
    """
    if flag["date_constructed"] is not None:
        return flag["date_constructed"].replace(day=1), False
    if flag["re_signed_at"] is not None:
        return signed_date(flag["re_signed_at"]).replace(day=1), True
    return None, True


def _money(sf: Decimal, rate: Optional[Decimal]) -> Optional[Decimal]:
    """
    An amount: square feet at a rate.
    Takes the square feet and the rate (None when not set).
    Returns the amount, or None without a rate.
    """
    return None if rate is None else sf * Decimal(rate)


def summary_months(flags: list[dict[str, Any]], rates: dict[str, Optional[Decimal]],
                   first: Optional[date] = None, last: Optional[date] = None) -> dict[date, list[SummaryRow]]:
    """
    Group a project's claimed flags by month and property, measured as the Construction Report measures them.
    Takes the flags (list_assessment_flags), the project's rates (cost_per_sf_4in, cost_per_sf_7in) and the months to keep (first and last, inclusive; None: open).
    Returns {month: rows}, the months in order and each month's rows by address, any case; amounts None without a rate.
    """
    rows: dict[tuple[date, UUID], SummaryRow] = {}
    for flag in flags:
        month, fallback = _month_of(flag)
        if month is None or (first and month < first) or (last and month > last):
            continue
        address = display_address_of(flag["property_kind"], flag["address"], flag["intersection_street_1"],
                                     flag["intersection_street_2"], flag["quadrant"], flag["facing_street"])
        row = rows.setdefault((month, flag["property_id"]), SummaryRow(flag["property_id"], address, flag.get("community_board")))
        row.used_approval_date = row.used_approval_date or fallback
        total, assessable, non_assessable = flag_areas(flag)
        if flag["surveyed_thickness"] == "7in":
            row.seven_total += total
            row.seven_assessable += assessable
            row.seven_non_assessable += non_assessable
        else:  # 4", and 7" assessed as 4"
            row.four_total += total
            row.four_assessable += assessable
            row.four_non_assessable += non_assessable
            if flag["surveyed_thickness"] == "7in_as_4in":
                row.built_7_billed_4 += total

    rate_4, rate_7 = rates.get("cost_per_sf_4in"), rates.get("cost_per_sf_7in")
    months: dict[date, list[SummaryRow]] = {}
    for (month, _), row in sorted(rows.items(), key=lambda item: (item[0][0], item[1].address.lower())):
        row.four_amount, row.seven_amount = _money(row.four_assessable, rate_4), _money(row.seven_assessable, rate_7)
        amounts = [amount for amount in (row.four_amount, row.seven_amount) if amount is not None]
        row.grand_total = sum(amounts, ZERO) if amounts else None
        if row.built_7_billed_4:
            row.remarks.append(f'{row.built_7_billed_4:,.2f} SF built at 7" billed at 4"')
        if rate_4 is None and row.four_total:
            row.remarks.append('4" rate not set')
        if rate_7 is None and row.seven_total:
            row.remarks.append('7" rate not set')
        months.setdefault(month, []).append(row)
    return months


# ---------------------------------------------------------------------------------------------------------------------
# The workbook
# ---------------------------------------------------------------------------------------------------------------------

def _rate_label(size: str, rate: Optional[Decimal]) -> str:
    """
    A tab header's rate cell.
    Takes the pay type ('4"' or '7"') and its rate (None when not set).
    Returns '4" rate: $11.54', or the label with a dash.
    """
    return f"{size} rate: " + (DASH if rate is None else f"${Decimal(rate):,.2f}")


def _header(sheet: Worksheet, project: dict[str, Any], label: str) -> None:
    """
    Write a tab's first two rows: the project, and the month with the two rates.
    Takes the sheet, the project row and the month's label.
    Returns nothing.
    """
    parts = [project["project_name"] or "", f"Project ID: {project['project_id']}"]
    parts += [f"Reg: {project['registration_code']}"] if project.get("registration_code") else []
    parts += [f"Borough: {project['borough']}"] if project.get("borough") else []
    sheet["A1"] = " · ".join(part for part in parts if part)
    sheet["A1"].font = Font(bold=True, size=12)
    sheet["A2"] = label
    sheet["A2"].font = Font(bold=True)
    sheet["C2"] = _rate_label('4"', project.get("cost_per_sf_4in"))
    sheet["G2"] = _rate_label('7"', project.get("cost_per_sf_7in"))
    for index, width in enumerate(COLUMN_WIDTHS, start=1):
        sheet.column_dimensions[sheet.cell(1, index).column_letter].width = width


def _sf(value: Decimal) -> Any:
    """
    A square-feet cell's value: the number, or a dash for none (for scanning the column).
    Takes the square feet.
    Returns what to write.
    """
    return value if value else DASH


def _write_row(sheet: Worksheet, row_number: int, values: list[Any], bold: bool = False) -> None:
    """
    Write one row of a tab, numbers formatted by their column: SF to two decimals, amounts in dollars.
    Takes the sheet, the row's number, its twelve values (None leaves a cell blank) and whether it is the totals row.
    Returns nothing.
    """
    for column, value in enumerate(values, start=1):
        cell = sheet.cell(row_number, column, value)
        if column in MONEY_COLUMNS:
            cell.number_format = MONEY_FORMAT
        elif column in SF_COLUMNS:
            cell.number_format = SF_FORMAT
            if value == DASH:
                cell.alignment = Alignment(horizontal="right")
        if bold:
            cell.font = Font(bold=True)
            cell.fill = TOTALS_FILL


def _month_tab(workbook: Workbook, project: dict[str, Any], month: date, rows: list[SummaryRow]) -> None:
    """
    Add one month's tab: the header, a row per property, the totals row and, when any row went by its IDR's approval date, a note saying how many.
    Takes the workbook, the project row, the month and its rows.
    Returns nothing.
    """
    sheet = workbook.create_sheet(month.strftime("%Y-%m"))
    _header(sheet, project, month.strftime("%B %Y"))
    for column, title in enumerate(COLUMNS, start=1):
        sheet.cell(3, column, title).font = Font(bold=True)
        sheet.cell(3, column).alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A4"
    for offset, row in enumerate(rows):
        _write_row(sheet, 4 + offset, [
            row.address, row.community_board, _sf(row.four_assessable), _sf(row.four_non_assessable), _sf(row.four_total), row.four_amount,
            _sf(row.seven_assessable), _sf(row.seven_non_assessable), _sf(row.seven_total), row.seven_amount,
            row.grand_total, "; ".join(row.remarks) or None,
        ])

    def total(attribute: str) -> Optional[Decimal]:
        values = [getattr(row, attribute) for row in rows]
        known = [value for value in values if value is not None]
        return sum(known, ZERO) if known else None

    totals_row = 4 + len(rows)
    _write_row(sheet, totals_row, [
        "Totals", None, total("four_assessable"), total("four_non_assessable"), total("four_total"), total("four_amount"),
        total("seven_assessable"), total("seven_non_assessable"), total("seven_total"), total("seven_amount"),
        total("grand_total"), None,
    ], bold=True)
    fallback = sum(row.used_approval_date for row in rows)
    if fallback:
        note = sheet.cell(totals_row + 2, 1, f"{fallback} {'row' if fallback == 1 else 'rows'} used IDR approval date "
                                              "(no page-level Date Constructed).")
        note.font = Font(italic=True)


def build_workbook(project: dict[str, Any], months: dict[date, list[SummaryRow]], filtered: bool) -> bytes:
    """
    Build the Assessment Summary workbook: one tab per month with rows, or a single 'No activity' tab saying why there are none.
    Takes the project row, the months (summary_months) and whether a month range was asked for.
    Returns the .xlsx file's bytes.
    """
    workbook = Workbook()
    workbook.remove(workbook.active)
    for month, rows in months.items():
        _month_tab(workbook, project, month, rows)
    if not months:
        sheet = workbook.create_sheet(NO_ACTIVITY)
        _header(sheet, project, NO_ACTIVITY)
        sheet["A4"] = EMPTY_RANGE if filtered else EMPTY_PROJECT
        sheet["A4"].font = Font(italic=True)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def summary_filename(project_id: str, now: Optional[datetime] = None) -> str:
    """
    The workbook's file name: the project and today's date in New York.
    Takes the project id and, for tests, the moment it is (now, UTC, by default).
    Returns '<project_id>_assessment_summary_<YYYY-MM-DD>.xlsx'.
    """
    today = signed_date(now or datetime.now(timezone.utc))
    return f"{project_id}_assessment_summary_{today.isoformat()}.xlsx"
