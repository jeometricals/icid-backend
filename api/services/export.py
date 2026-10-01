"""
Exports a submitted IDR as an .xlsx file built on the DDC report-forms template.

D1 stamps Contract Info and the General's front page (Gen Fr): header and Description of Work.
Pay items, workforce, equipment, safety and comments, and the other report types, come later.
"""

import textwrap
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from api.queries.idr_reports import get_general_report, list_non_general_main_reports
from api.queries.idrs import get_idr_by_id
from api.queries.projects import get_project_by_id, get_project_contractor_name
from api.queries.users import get_user_by_id
from api.schemas.idr_report import ADDENDUM_TYPES
from api.services.auto_general import build_auto_general_data
from api.services.xlsx_template import WorkbookTemplate

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "report_forms.xlsx"

CONTRACT_INFO = "Contract Info"
GEN_FRONT = "Gen Fr"

# Gen Fr's day-of-week letters, Sunday first (S M T W T F S)
DAY_OF_WEEK_CELLS = ["AI5", "AJ5", "AK5", "AL5", "AM5", "AN5", "AO5"]

# The ruled lines of Gen Fr's Description of Work box, and how many characters fit on one at its font size
DESCRIPTION_ROWS = range(22, 35)
DESCRIPTION_LINE_CHARS = 60
DESCRIPTION_OVERFLOW = " … (continued in ICID)"


class ExportError(Exception):
    """Base class for an IDR that can't be exported."""


class IdrNotFoundError(ExportError):
    """No IDR has that id."""


class IdrNotSubmittedError(ExportError):
    """The IDR is still a draft; only submitted IDRs are exported."""


class ExportDataError(ExportError):
    """The IDR's project or reports could not be loaded."""


@dataclass
class IdrExport:
    """A generated export: the file name to offer and the .xlsx bytes."""

    filename: str
    content: bytes


def _time_range(start: Optional[time], end: Optional[time]) -> str:
    """
    Fill the template's "( Start ___ End ___ )" line.
    Takes the start and end times (either may be None).
    Returns the line with HH:MM in place of each known blank.
    """
    def slot(value: Optional[time]) -> str:
        return value.strftime("%H:%M") if value else "________"
    return f"( Start {slot(start)} End {slot(end)} )"


def _temperature(label: str, value: Optional[Decimal]) -> str:
    """
    Fill a Low / High temperature box, whose label shares the cell with the value.
    Takes the label and the temperature (or None).
    Returns e.g. "Low  45" (whole numbers without decimals), or just the label when unknown.
    """
    if value is None:
        return label
    number = Decimal(value)
    shown = str(number.quantize(Decimal(1))) if number == number.to_integral_value() else str(number.normalize())
    return f"{label}  {shown}"


def _inspector_name(user: Optional[dict[str, Any]]) -> Optional[str]:
    """
    Format the inspector's name for the form.
    Takes the users row (or None).
    Returns "First Last", falling back to the email, or None when the user is unknown.
    """
    if user is None:
        return None
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return name or user.get("email")


def description_lines(text: Any, capacity: int = len(DESCRIPTION_ROWS)) -> list[str]:
    """
    Lay a Description of Work out on the form's ruled lines.
    Takes the description (non-text becomes "") and the number of lines available.
    Returns at most capacity lines, each paragraph starting a new line; text that doesn't fit is cut
    and the last line ends with a "continued" note.
    """
    paragraphs = text.splitlines() if isinstance(text, str) else []
    lines = [line for paragraph in paragraphs if paragraph.strip()
             for line in textwrap.wrap(paragraph.strip(), DESCRIPTION_LINE_CHARS)]
    if len(lines) <= capacity:
        return lines
    kept = lines[:capacity]
    room = DESCRIPTION_LINE_CHARS - len(DESCRIPTION_OVERFLOW)
    kept[-1] = kept[-1][:room].rstrip() + DESCRIPTION_OVERFLOW
    return kept


def _general_for_export(idr_id: UUID) -> tuple[dict[str, Any], Optional[int]]:
    """
    Find the General to print: the IDR's own, or one composed the way the auto-General is when it has none.
    Takes the IDR uuid.
    Returns (its report_data, its page number or None when composed); raises ExportDataError if reports can't load.
    """
    general = get_general_report(idr_id)
    if general is not None:
        return general["report_data"] or {}, general["page_number"]
    reports = list_non_general_main_reports(idr_id)
    if reports is None:
        raise ExportDataError("Failed to load IDR reports")
    contributing = [report for report in reports if report["report_type"] not in ADDENDUM_TYPES]
    return build_auto_general_data(contributing), None


def _stamp_contract_info(workbook: WorkbookTemplate, project: dict[str, Any], contractor: Optional[str]) -> None:
    """
    Write the project details every form's header pulls from.
    Takes the workbook, the project row and the contractor's name (or None).
    Returns nothing; the Resident Engineer (C7) stays blank, as the data model has no source for it yet.
    """
    values = {
        "C2": project["project_id"],
        "C3": project.get("registration_code"),
        "C4": project.get("project_description"),
        "C5": project.get("borough"),
        "C6": contractor,
        "C7": None,
    }
    for coordinate, value in values.items():
        workbook.set_cell(CONTRACT_INFO, coordinate, value)


def _stamp_general_front(workbook: WorkbookTemplate, idr: dict[str, Any], inspector: Optional[str],
                         general_data: dict[str, Any], page_number: Optional[int]) -> None:
    """
    Write the General's front page: header fields, the day of week and the Description of Work.
    Takes the workbook, the IDR row, the inspector's name, the General's report_data and its page number.
    Returns nothing; the I.R. No. stays blank (no source yet), and Sheet No. is left blank for a composed General.
    """
    report_date: date = idr["report_date"]
    workbook.set_cell(GEN_FRONT, "AI4", report_date)
    # The form circles the day by hand; the export shades and outlines its letter instead
    day_cell = DAY_OF_WEEK_CELLS[(report_date.weekday() + 1) % 7]
    workbook.set_style(GEN_FRONT, day_cell, workbook.highlighted_style(workbook.cell_style(GEN_FRONT, day_cell)))
    workbook.set_cell(GEN_FRONT, "AH6", None)
    has_page = page_number is not None
    workbook.set_cell(GEN_FRONT, "AH8", page_number if has_page else None)
    workbook.set_cell(GEN_FRONT, "AM8", idr.get("total_pages") if has_page else None)
    workbook.set_cell(GEN_FRONT, "AG10", _time_range(idr.get("work_start_time"), idr.get("work_end_time")))
    workbook.set_cell(GEN_FRONT, "AG12", _time_range(idr.get("inspector_start_time"), idr.get("inspector_end_time")))
    workbook.set_cell(GEN_FRONT, "AD13", _temperature("Low", idr.get("temp_low")))
    workbook.set_cell(GEN_FRONT, "AK13", _temperature("High", idr.get("temp_high")))
    workbook.set_cell(GEN_FRONT, "AD17", idr.get("weather_am"))
    workbook.set_cell(GEN_FRONT, "AK17", idr.get("weather_pm"))
    workbook.set_cell(GEN_FRONT, "H17", inspector)

    lines = description_lines(general_data.get("description"))
    for index, row in enumerate(DESCRIPTION_ROWS):
        workbook.set_cell(GEN_FRONT, f"B{row}", lines[index] if index < len(lines) else None)


def generate_idr_export(idr_id: UUID) -> IdrExport:
    """
    Build a submitted IDR's .xlsx export from the report-forms template.
    Takes the IDR uuid.
    Returns an IdrExport (file name and bytes); raises IdrNotFoundError, IdrNotSubmittedError or ExportDataError.
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        raise IdrNotFoundError("IDR not found")
    if idr["status"] != "submitted":
        raise IdrNotSubmittedError("Only submitted IDRs can be exported")

    project = get_project_by_id(idr["project_id"])
    if project is None:
        raise ExportDataError("Failed to load the IDR's project")
    contractor = get_project_contractor_name(idr["project_id"])
    inspector = _inspector_name(get_user_by_id(idr["reporter_uuid"]))
    general_data, page_number = _general_for_export(idr_id)

    workbook = WorkbookTemplate(TEMPLATE_PATH)
    _stamp_contract_info(workbook, project, contractor)
    _stamp_general_front(workbook, idr, inspector, general_data, page_number)
    workbook.fit_to_letter_page(GEN_FRONT)
    workbook.show_only([GEN_FRONT])

    return IdrExport(
        filename=f"IDR_{idr_id}_{idr['report_date'].isoformat()}.xlsx",
        content=workbook.to_bytes(),
    )
