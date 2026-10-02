"""
Stamps an IDR's Asphaltic Concrete (AC) report onto the DDC template's AC Fr / AC Bk pages.

AC Fr is the front: the header block, the paving contractor and temperatures, theoretical max density, the pavement
course table, material usage for top and binder, Pay Items, the A/C requirements and tack coat. AC Bk is the back:
remarks, work force and equipment, the MPT/safety checklist beside the delivery ticket log, and the signature block.
So far AC Fr is stamped down to its Pay Items (header, paving contractor, temperatures, theoretical max density,
the first four pavement courses, material usage and the first ten pay items); both pages always print with an AC
report (AC Bk carries the certification and the signature lines).

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import (
    HeaderLayout, PayItemsLayout, object_rows, section, stamp_common_header, stamp_pay_items, typed_value,
)
from api.services.xlsx_template import WorkbookTemplate

AC_FRONT = "AC Fr"
AC_BACK = "AC Bk"

# The header: the same cells as Conc Fr's, cell for cell. The date cell is General-formatted (the date goes in as
# m/d/yy text), and each weather box is one merged area whose "AM" / "PM" label sits at its top left.
AC_FRONT_HEADER = HeaderLayout(
    project_cells={"G8": "project_id", "P8": "registration_code", "I10": "project_description",
                   "F12": "borough", "F14": "contractor"},
    date="AI4", date_as_text=True,
    day_of_week=("AI5", "AJ5", "AK5", "AL5", "AM5", "AN5", "AO5"),
    ir_no="AH6", sheet_no="AH8", sheet_of="AM8",
    work_time="AG10", inspector_time="AG12",
    temp_low="AD13", temp_high="AK13",
    weather_am="AD15", weather_pm="AK15", weather_labels=("AM", "PM"),
    inspector="H17",
)


# Paving contractor (rows 20-22). The contractor's name has no value cell or line of its own on row 20, so it is
# centred across L20:X20, the span of the Subcontractor box below it (L21:X21, merged and underlined). Rice No.
# goes in L22:S22, above "(Contractor to Supply)".
PAVING_NAME_CELLS = [f"{column}20" for column in ("L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X")]
PAVING_CELLS = {"subcontractor": "L21", "riceNo": "L22"}

# Temperature: the value boxes under SURFACE / AMBIENT and START / FINISH, each a merged area on row 23
TEMPERATURE_CELLS = {"surfaceStart": "AA23", "surfaceFinish": "AE23", "ambientStart": "AI23", "ambientFinish": "AM23"}

# Theoretical max density (row 25) has no value cells: each value is centred across the blank run after its label,
# Y25:AD25 after "TOP:" (W25, running into X25) and AH25:AP25 after "BINDER:" (AE25, running into AG25)
MAX_DENSITY_CELLS = {
    "top": ["Y25", "Z25", "AA25", "AB25", "AC25", "AD25"],
    "binder": ["AH25", "AI25", "AJ25", "AK25", "AL25", "AM25", "AN25", "AO25", "AP25"],
}

# Pavement course table: header rows 26-27 (From / To under Station on 27), then four data rows, one merged area per
# column, 8 pt centred. Courses past the fourth aren't printed yet (G6 continues them on copies of AC Fr).
PAVEMENT_ROWS = range(28, 32)
PAVEMENT_COLUMNS = {"itemNo": "B", "mixType": "G", "stationFrom": "K", "stationTo": "N", "lane": "Q", "length": "T",
                    "width": "W", "course": "AA", "designDepth": "AE", "area": "AI", "weight": "AM"}

# Material usage, TOP on the left half and BINDER on the right: each value sits right of its "=" (H, S / AB, AM)
MATERIAL_CELLS = {
    "materialUsageTop": {"noOfTickets": "H34", "firstTicketNo": "H35", "lastTicketNo": "H36",
                         "qtyReceived": "S34", "qtyUsed": "S35", "qtyWasted": "S36"},
    "materialUsageBinder": {"noOfTickets": "AB34", "firstTicketNo": "AB35", "lastTicketNo": "AB36",
                            "qtyReceived": "AM34", "qtyUsed": "AM35", "qtyWasted": "AM36"},
}

# Pay Items: rows 39-48 under the header at row 38 (15 pt rows), in Conc Fr's columns. AC Fr's column widths are
# Conc Fr's, so the Description cell U:AP keeps Conc Fr's budget of 46 / 55 characters a line at 10 / 8 pt. Items past
# the tenth aren't printed yet (G6 continues them on copies of AC Fr).
AC_FRONT_PAY_ITEMS = PayItemsLayout(
    rows=range(39, 49),
    columns={"itemNo": "B", "budgetCode": "F", "payQuantity": "K", "quantityChk": "P", "description": "U"},
    line_chars_10pt=46, line_chars_8pt=55,
)


def _centre_across(workbook: WorkbookTemplate, sheet: str, cells: list[str], value: Any) -> None:
    """
    Write a value centred across a run of cells that has no merged box of its own.
    Takes the workbook, the sheet, the run's cells (left to right) and the value (None leaves the run blank).
    Returns nothing.
    """
    workbook.set_cell(sheet, cells[0], value)
    if value is not None:
        workbook.center_across(sheet, cells)


def _stamp_site_conditions(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Write the paving contractor, Rice No., the surface and ambient temperatures and the theoretical max density.
    Takes the workbook, the front page and the report_data; numbers stay numbers, text is trimmed, blanks stay empty.
    Returns nothing.
    """
    paving = section(data, "pavingContractor")
    _centre_across(workbook, front, PAVING_NAME_CELLS, typed_value(paving.get("pavingContractorName")))
    for field, cell in PAVING_CELLS.items():
        workbook.set_cell(front, cell, typed_value(paving.get(field)))
    temperature = section(data, "temperature")
    for field, cell in TEMPERATURE_CELLS.items():
        workbook.set_cell(front, cell, typed_value(temperature.get(field)))
    density = section(data, "maxDensity")
    for field, cells in MAX_DENSITY_CELLS.items():
        _centre_across(workbook, front, cells, typed_value(density.get(field)))


def _stamp_pavement_courses(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Fill the pavement course table's four rows, one course a row, blanking the rest.
    Takes the workbook, the front page and the report_data (courses past the fourth are left out).
    Returns nothing.
    """
    courses = object_rows(data, "pavementCourses")
    for index, row in enumerate(PAVEMENT_ROWS):
        course = courses[index] if index < len(courses) else {}
        for field, column in PAVEMENT_COLUMNS.items():
            workbook.set_cell(front, f"{column}{row}", typed_value(course.get(field)))


def _stamp_material_usage(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Write material usage for the top and binder courses: tickets and quantities received, used and wasted.
    Takes the workbook, the front page and the report_data.
    Returns nothing.
    """
    for key, cells in MATERIAL_CELLS.items():
        usage = section(data, key)
        for field, cell in cells.items():
            workbook.set_cell(front, cell, typed_value(usage.get(field)))


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, fronts: Optional[list[str]] = None,
           back: str = AC_BACK) -> list[str]:
    """
    Stamp an AC report onto its AC Fr / AC Bk pages (so far AC Fr down to its Pay Items).
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, the report's page number
    (None leaves Sheet No. blank), its report_data, its front pages (AC Fr unless given) and its back page (AC Bk
    unless given).
    Returns the sheets it used, in print order: the fronts, then the back; each is set to print on one Letter page,
    and the caller decides which sheets the workbook shows.
    """
    fronts = fronts or [AC_FRONT]
    data = report_data if isinstance(report_data, dict) else {}
    stamp_common_header(workbook, fronts[0], AC_FRONT_HEADER, idr, project, contractor, inspector, page_number)
    _stamp_site_conditions(workbook, fronts[0], data)
    _stamp_pavement_courses(workbook, fronts[0], data)
    _stamp_material_usage(workbook, fronts[0], data)
    stamp_pay_items(workbook, fronts[0], AC_FRONT_PAY_ITEMS, data.get("payItems"))
    pages = fronts + [back]
    for sheet in pages:
        workbook.fit_to_letter_page(sheet)
    return pages
