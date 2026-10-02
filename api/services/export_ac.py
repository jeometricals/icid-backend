"""
Stamps an IDR's Asphaltic Concrete (AC) report onto the DDC template's AC Fr / AC Bk pages.

AC Fr is the front: the header block, the paving contractor and temperatures, theoretical max density, the pavement
course table, material usage for top and binder, Pay Items, the A/C requirements and tack coat. AC Bk is the back:
remarks, work force and equipment, the MPT/safety checklist beside the delivery ticket log, and the signature block.
Both pages are stamped in full on their first sheet: pavement courses past the fourth, pay items past the tenth and
remarks past AC Bk's thirteen lines aren't continued yet, and delivery tickets past the tenth are counted in a note.
Both pages always print with an AC report (AC Bk carries the certification and the signature lines, left blank:
the inspector and RE sign).

AC Bk is Conc Bk's back page sixteen rows lower (same column widths, same tables), with its own Y / N columns, no
safety remarks column, and the delivery ticket log beside the safety list.

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import (
    EquipmentLayout, HeaderLayout, PayItemsLayout, SafetyLayout, TextArea, WorkforceLayout, fill_lines,
    mark_truncated, object_rows, paragraphs, section, stamp_checklist, stamp_common_header, stamp_equipment,
    stamp_pay_items, stamp_safety, stamp_workforce, tick_box, typed_value, write_lines,
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

# Requirements (rows 51-57): the form prints the seven labels (B:V) and nothing to answer them in, so the export adds
# its own Y / N / Remarks columns over the blank W:AP, headed on row 50 beside "REQUIREMENTS:": Y in W:X, N in Y:Z
# and Remarks in AA:AP, each merged per row. The cells are the template's 10 pt centred; remarks go left-aligned and
# shrink to fit their box. N/A has no box, so it opens the remarks, as on the safety checklist.
AC_REQUIREMENTS = SafetyLayout(
    rows={"subgradeCompacted": 51, "roadwayCleanDry": 52, "acRollerPerSpec": 53, "densityTestsTaken": 54,
          "spotCheckAcDepth": 55, "tackCoatPerSpec": 56, "tackCoatOnEdges": 57},
    yes_column="W", no_column="Y", remarks_column="AA",
)
REQUIREMENT_BOXES = {"W": "X", "Y": "Z", "AA": "AP"}  # each box's first column -> its last
REQUIREMENT_HEADINGS = {"W": "Y", "Y": "N", "AA": "REMARKS"}
REQUIREMENT_HEADING_ROW = 50

# Tack coat: No. of Gallons (AA58:AD58) and Gallons per S.Y. (AL58:AO58) after their labels on row 58, and the
# application method / type on Q61:AO61. "QUANTITY OF TACK COAT:" (B58) has no field in the report; its blank
# (L58:S58) stays empty.
TACK_COAT_CELLS = {"noOfGallons": "AA58", "gallonsPerSy": "AL58", "applicationMethod": "Q61"}

# ---- AC Bk ------------------------------------------------------------------

# Remarks: "Remarks:" at C3 and its subtitle at C4, then thirteen 11 pt ruled lines C5:AH5 ... C17:AH17, as wide as
# Conc Bk's (75 characters a line). They take the comments; text past the thirteenth line is cut with a note.
AC_BACK_TEXT = TextArea(rows=range(5, 18), column="C", line_chars=75)

# Work Force (rows 21-33, No. in G:H): the frontend's five roles on rows 21-25; Teamsters, Surveyors and Masons are
# pre-printed on 26-28 (an added trade of that name lands there), and 29-33 are blank rows for other added trades
AC_BACK_WORKFORCE = WorkforceLayout(
    role_rows={"superintendent": 21, "foremen": 22, "operators": 23, "laborers": 24, "flaggers": 25},
    trade_rows={"teamsters": 26, "surveyors": 27, "masons": 28},
    free_rows=(29, 30, 31, 32, 33),
    label_column="B", count_column="G",
)

# Equipment (rows 21-33, Model / Size N:U + No. V:X, then a second pair Y:AF + AG:AI). Four of the frontend's standard
# types have a row; Excavator doesn't, so it goes on the blank row 33 with its name written in, as on Conc Bk. Added
# equipment of a pre-printed type (Crane, Paving Machine, AC Distributor, ...) lands on its own row.
AC_BACK_EQUIPMENT_EXTRA_ROWS = {
    "crane": 23, "paving machine": 24, "ac distributor": 25, "sweepers": 26, "trailers": 27,
    "roller – static": 29, "roller - static": 29, "roller – dynamic": 30, "roller - dynamic": 30, "hand tamper": 32,
}
AC_BACK_EQUIPMENT = EquipmentLayout(
    standard_rows={"frontEndLoader": 21, "backhoe": 22, "truckDump": 28, "compressor": 31},
    extra_rows=AC_BACK_EQUIPMENT_EXTRA_ROWS,
    row_names=frozenset(AC_BACK_EQUIPMENT_EXTRA_ROWS),  # every pre-printed row is its own type: no variant names
    free_rows=(33,),
    label_column="I",
    slots=(("N", "V"), ("Y", "AG")),
)

# End of the Day MPT/Safety Check List: rows 36-45, Y in L:M and N in N:O. There is no remarks column (the delivery
# ticket log takes the rest of the row), so safety remarks, and N/A answers, aren't printed on AC Bk.
AC_BACK_SAFETY = SafetyLayout(
    rows={"plasticBarrels": 36, "pedestrianBarricades": 37, "timberCurbs": 38, "timberBreakawayBarricades": 39,
          "generalSafety": 40, "localEmergencyAccess": 41, "fencing": 42, "plates": 43, "arrowBoard": 44,
          "siteCleaned": 45},
    yes_column="L", no_column="N", remarks_column=None,
)

# Delivery ticket log, beside the safety list: rows 36-45, LOCATION P:AC, Ticket No. AD:AF, Temperature AG:AI.
# Tickets past the tenth aren't printed; a note under the comments says how many.
TICKET_ROWS = range(36, 46)
TICKET_COLUMNS = {"location": "P", "ticketNo": "AD", "temperature": "AG"}
MORE_TICKETS_NOTE = "[Note] {count} more delivery ticket{plural} — see ICID"

# "Attached Pages for Additional Remarks and / or Sketches" (C48, a transparent rectangle, like Conc Bk's C52)
ATTACHED_PAGES_BOX = "C48"


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


def _stamp_requirements(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Lay out the requirements' Y / N / Remarks boxes and fill them from the A/C requirements answers.
    Takes the workbook, the front page and the report_data (each requirement is {value: 'Y'|'N'|'NA'|'', remarks}).
    Returns nothing.
    """
    for first, heading in REQUIREMENT_HEADINGS.items():
        rows = [REQUIREMENT_HEADING_ROW, *AC_REQUIREMENTS.rows.values()]
        for row in rows:
            workbook.merge_cells(front, f"{first}{row}:{REQUIREMENT_BOXES[first]}{row}")
        workbook.set_cell(front, f"{first}{REQUIREMENT_HEADING_ROW}", heading)
    requirements = section(data, "acRequirements")
    answers = {key: (section(requirements, key).get("value"), section(requirements, key).get("remarks"))
               for key in AC_REQUIREMENTS.rows}
    stamp_checklist(workbook, front, AC_REQUIREMENTS, answers)
    for row in AC_REQUIREMENTS.rows.values():
        cell = f"{AC_REQUIREMENTS.remarks_column}{row}"
        workbook.align_left(front, cell)
        workbook.shrink_to_fit_cell(front, cell)


def _stamp_tack_coat(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Write the tack coat's gallons, gallons per S.Y. and application method / type.
    Takes the workbook, the front page and the report_data.
    Returns nothing.
    """
    tack_coat = section(data, "tackCoat")
    for field, cell in TACK_COAT_CELLS.items():
        workbook.set_cell(front, cell, typed_value(tack_coat.get(field)))


def _stamp_delivery_tickets(workbook: WorkbookTemplate, back: str, data: dict[str, Any]) -> int:
    """
    Fill the delivery ticket log's ten rows, one ticket a row, blanking the rest.
    Takes the workbook, the back page and the report_data.
    Returns how many tickets didn't fit.
    """
    tickets = object_rows(data, "deliveryTickets")
    for index, row in enumerate(TICKET_ROWS):
        ticket = tickets[index] if index < len(tickets) else {}
        for field, column in TICKET_COLUMNS.items():
            workbook.set_cell(back, f"{column}{row}", typed_value(ticket.get(field)))
    return max(0, len(tickets) - len(TICKET_ROWS))


def _stamp_remarks(workbook: WorkbookTemplate, back: str, comments: Any, more_tickets: int) -> None:
    """
    Write the comments on AC Bk's remarks lines, followed by a note when delivery tickets didn't all fit.
    Takes the workbook, the back page, the comments and how many tickets didn't fit.
    Returns nothing; text past the thirteenth line is cut with the "continued in ICID" note.
    """
    note = MORE_TICKETS_NOTE.format(count=more_tickets, plural="" if more_tickets == 1 else "s")
    queue = paragraphs(comments) + ([note] if more_tickets else [])
    lines = fill_lines(queue, len(AC_BACK_TEXT.rows), AC_BACK_TEXT.line_chars)
    if queue:
        lines = mark_truncated(lines, AC_BACK_TEXT.line_chars)
    write_lines(workbook, back, AC_BACK_TEXT.rows, lines, AC_BACK_TEXT.column)


def mark_attachments(workbook: WorkbookTemplate, back: str = AC_BACK) -> None:
    """
    Tick AC Bk's "Attached Pages for Additional Remarks and / or Sketches" box.
    Takes the workbook (after render has stamped the page) and the back page (AC Bk unless given).
    Returns nothing.
    """
    tick_box(workbook, back, ATTACHED_PAGES_BOX, True)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, fronts: Optional[list[str]] = None,
           back: str = AC_BACK) -> list[str]:
    """
    Stamp an AC report onto its AC Fr / AC Bk pages. The "Attached Pages" box is the caller's to tick (see
    mark_attachments), as it knows the report's attachments.
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
    _stamp_requirements(workbook, fronts[0], data)
    _stamp_tack_coat(workbook, fronts[0], data)

    more_tickets = _stamp_delivery_tickets(workbook, back, data)
    _stamp_remarks(workbook, back, data.get("comments"), more_tickets)
    stamp_workforce(workbook, back, AC_BACK_WORKFORCE, data)
    stamp_equipment(workbook, back, AC_BACK_EQUIPMENT, data)
    stamp_safety(workbook, back, AC_BACK_SAFETY, data)
    pages = fronts + [back]
    for sheet in pages:
        workbook.fit_to_letter_page(sheet)
    return pages
