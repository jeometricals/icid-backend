"""
Stamps an IDR's Asphaltic Concrete (AC) report onto the DDC template's AC Fr / AC Bk pages.

AC Fr is the front: the header block, the paving contractor and temperatures, theoretical max density, the pavement
course table, material usage for top and binder, Pay Items, the A/C requirements and tack coat. AC Bk is the back:
remarks, work force and equipment, the MPT/safety checklist beside the delivery ticket log, and the signature block.
Courses past the fourth and pay items past the tenth continue on copies of AC Fr (AC Fr 2, AC Fr 3, ...): front
sheet N carries courses 4N-3 to 4N and its own slice of the pay items, so every "continued" note points to the
sheet that really follows. A sheet with courses repeats the whole front (header, site conditions, material usage,
requirements, tack coat); one past the last course carries just the header and pay items. AC Bk's remarks (the
comments, a note on delivery tickets that didn't fit, and the safety remarks the page has no column for) continue
on Report Cont when it's free. Both pages always print with an AC report (AC Bk carries the certification and the
signature lines, left blank: the inspector and RE sign).

AC Bk is Conc Bk's back page sixteen rows lower (same column widths, same tables), with its own Y / N columns, no
safety remarks column, and the delivery ticket log beside the safety list.

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import (
    SignatureLayout,
    REPORT_CONT, REPORT_CONT_TEXT, EquipmentLayout, HeaderLayout, PayItemsLayout, SafetyLayout, TextArea,
    WorkforceLayout, allocate_copies, checklist_answer, fill_lines, mark_truncated, object_rows,
    pay_item_page_count, pay_item_slices, redline_paragraphs, section, stamp_checklist, stamp_common_header, stamp_equipment,
    stamp_pay_items, stamp_report_cont, stamp_safety, stamp_workforce, text_value, tick_box, typed_value, write_lines,
)
from api.services.export_redlines import NO_REDLINES, Redlines
from api.services.xlsx_template import EMU_PER_PIXEL, WorkbookTemplate

AC_FRONT = "AC Fr"
AC_BACK = "AC Bk"

# AC Bk's signature line is row 55 (C55:M55), its date AE55:AH55; the image also takes the blank row above
SIGNATURE_LAYOUT = SignatureLayout(signature_cells="C54:M55", signature_cx_emu=209 * EMU_PER_PIXEL,
                                   signature_cy_emu=34 * EMU_PER_PIXEL, date_cell="AE55")
# The Resident Engineer's line is S55:AC55, its caption S56:AC56
RE_SIGNATURE_LAYOUT = SignatureLayout(signature_cells="S54:AC55", signature_cx_emu=209 * EMU_PER_PIXEL,
                                      signature_cy_emu=34 * EMU_PER_PIXEL, caption_cells="S56:AC56")

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
    # AI4:AO4 is the date line (a "/" drawn in AK4 and AM4) and AH6 a single cell in a 6 pt row: merged as on Gen Fr,
    # where AI4:AO4 and AH6:AO7 are one area each, so "9/30/26" and the I.R. number aren't clipped
    merge_areas=("AI4:AO4", "AH6:AO7"), date_slashes=("AK4", "AM4"),
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
# column, 8 pt centred. Courses past the fourth continue on the next AC Fr sheet.
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
# the tenth continue on the next AC Fr sheet (the last row then says so).
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

# Continuation notes on AC Fr's copies, in the narrow gap rows: "Continued from previous page" on row 24 (above
# THEORETICAL MAX DENSITY) on every sheet after the first, and on row 32 (the strip under the pavement table) a
# pointer onward when courses continue. Sheet names don't print, so the notes say "previous" / "next" page.
CONTINUED_FROM_CELL = "B24"
CONTINUED_FROM_NOTE = "Continued from previous page"
COURSES_CONTINUED_CELL = "B32"
COURSES_CONTINUED_NOTE = "Pavement courses continued on next page"
NOTE_FONT_PT = 7

# ---- AC Bk ------------------------------------------------------------------

# Remarks: "Remarks:" at C3 and its subtitle at C4, then thirteen 11 pt ruled lines C5:AH5 ... C17:AH17, as wide as
# Conc Bk's (75 characters a line). They take the comments, a note on delivery tickets that didn't fit and the safety
# remarks; what doesn't fit continues on Report Cont when it's free, and is otherwise cut with a note.
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
# ticket log takes the rest of the row), so safety remarks and N/A answers go to the end of the remarks instead,
# under SAFETY_REMARKS_HEADING, each named by its row's label.
AC_BACK_SAFETY = SafetyLayout(
    rows={"plasticBarrels": 36, "pedestrianBarricades": 37, "timberCurbs": 38, "timberBreakawayBarricades": 39,
          "generalSafety": 40, "localEmergencyAccess": 41, "fencing": 42, "plates": 43, "arrowBoard": 44,
          "siteCleaned": 45},
    yes_column="L", no_column="N", remarks_column=None,
)
SAFETY_LABELS = {
    "plasticBarrels": "Plastic Barrels", "pedestrianBarricades": "Pedestrian Barricades", "timberCurbs": "Timber Curbs",
    "timberBreakawayBarricades": "Timber/Breakaway Barricades", "generalSafety": "General Safety Conditions",
    "localEmergencyAccess": "Local and Emergency Access", "fencing": "Fencing", "plates": "Plates",
    "arrowBoard": "Arrow Board", "siteCleaned": "Site Cleaned and Secured",
}
SAFETY_REMARKS_HEADING = "Safety check list remarks:"

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


def front_count(report_data: Any, redlines: Redlines = NO_REDLINES) -> int:
    """
    Count the AC Fr sheets a report prints on: enough for its pavement courses (four a sheet) and for its pay items
    (ten rows on the last sheet, nine and a "continued" row on the others; a revised item takes a row per revision),
    and always at least one.
    Takes the report_data (anything that isn't an object counts as empty) and the report's redlines (none unless
    given).
    Returns the number of sheets.
    """
    data = report_data if isinstance(report_data, dict) else {}
    course_sheets = -(-len(object_rows(data, "pavementCourses")) // len(PAVEMENT_ROWS))
    return max(1, course_sheets, pay_item_page_count(data.get("payItems"), AC_FRONT_PAY_ITEMS, redlines))


def _stamp_pavement_courses(workbook: WorkbookTemplate, front: str, courses: list[dict[str, Any]]) -> None:
    """
    Fill the pavement course table's four rows, one course a row, blanking the rest.
    Takes the workbook, the front page and this sheet's courses (at most four).
    Returns nothing.
    """
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


def safety_remarks(data: dict[str, Any]) -> list[str]:
    """
    List the safety checklist's remarks and N/A answers, which AC Bk has no column for, to print with the remarks.
    Takes the report_data.
    Returns a heading and one "<item>: <remarks>" line per item ("N/A" or "N/A — <remarks>" for N/A), or [] if none.
    """
    checks, remarks = section(data, "safetyChecks"), section(data, "safetyRemarks")
    lines = []
    for key, label in SAFETY_LABELS.items():
        answer, remark = checklist_answer(checks.get(key)), text_value(remarks.get(key))
        if answer == "NA":
            remark = f"N/A — {remark}" if remark else "N/A"
        if remark:
            lines.append(f"{label}: {remark}")
    return [SAFETY_REMARKS_HEADING] + lines if lines else []


def _stamp_remarks(workbook: WorkbookTemplate, back: str, idr: dict[str, Any], project: dict[str, Any],
                   inspector: Optional[str], queue: list[str], report_cont_available: bool) -> bool:
    """
    Write the remarks on AC Bk's lines, continuing on Report Cont when they don't fit and it's free.
    Takes the workbook, the back page, the IDR row, the project row, the inspector's name, the remarks' paragraphs
    and whether Report Cont is free.
    Returns whether the remarks continued on Report Cont; text that still doesn't fit is cut with the
    "continued in ICID" note.
    """
    queue = list(queue)
    lines = fill_lines(queue, len(AC_BACK_TEXT.rows), AC_BACK_TEXT.line_chars)
    continued: list[str] = []
    if queue and report_cont_available:
        continued = fill_lines(queue, len(REPORT_CONT_TEXT.rows), REPORT_CONT_TEXT.line_chars)
        if queue:
            continued = mark_truncated(continued, REPORT_CONT_TEXT.line_chars)
    elif queue:
        lines = mark_truncated(lines, AC_BACK_TEXT.line_chars)
    write_lines(workbook, back, AC_BACK_TEXT.rows, lines, AC_BACK_TEXT.column)
    if continued:
        stamp_report_cont(workbook, idr, project, inspector, continued)
    return bool(continued)


def _write_note(workbook: WorkbookTemplate, sheet: str, cell: str, note: str) -> None:
    """
    Write a small continuation note, left-aligned, in one of AC Fr's narrow gap rows.
    Takes the workbook, the sheet, the cell and the note.
    Returns nothing.
    """
    workbook.set_cell(sheet, cell, note)
    workbook.set_font_size(sheet, cell, NOTE_FONT_PT)
    workbook.align_left(sheet, cell)


def mark_attachments(workbook: WorkbookTemplate, back: str = AC_BACK) -> None:
    """
    Tick AC Bk's "Attached Pages for Additional Remarks and / or Sketches" box.
    Takes the workbook (after render has stamped the page) and the back page (AC Bk unless given).
    Returns nothing.
    """
    tick_box(workbook, back, ATTACHED_PAGES_BOX, True)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, report_cont_available: bool = True,
           fronts: Optional[list[str]] = None, back: str = AC_BACK,
           redlines: Redlines = NO_REDLINES) -> list[str]:
    """
    Stamp an AC report onto its AC Fr sheets, AC Bk and, when its remarks run long, Report Cont. The "Attached Pages"
    box is the caller's to tick (see mark_attachments), as it knows the report's attachments. What reviewers edited
    (the header, the comments, pay items, work force, equipment and the safety answers) prints with its redlines;
    pass them as redlines (none unless given). Safety remarks, printed with the remarks here, show as they stand.
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, the report's page number
    (its later fronts take the numbers after it; None leaves Sheet No. blank), its report_data, whether Report Cont
    is free (False when another report in the export already continues onto it; the remarks are then cut with a
    note), its front pages (AC Fr and its copies, front_count of them; None clones them here) and its back page (AC Bk
    unless given).
    Returns the sheets it used, in print order: the fronts, the back, and Report Cont when used; each is set to print
    on one Letter page, and the caller decides which sheets the workbook shows. Raises ValueError if the fronts given
    don't match front_count.
    """
    data = report_data if isinstance(report_data, dict) else {}
    needed = front_count(data, redlines)
    fronts = fronts or allocate_copies(workbook, AC_FRONT, needed)
    if len(fronts) != needed:
        raise ValueError(f"this AC report needs {needed} AC Fr sheets, got {len(fronts)}")
    courses = object_rows(data, "pavementCourses")
    course_sheets = max(1, -(-len(courses) // len(PAVEMENT_ROWS)))  # the first sheet prints the table even when empty
    pay_slices = pay_item_slices(data.get("payItems"), len(AC_FRONT_PAY_ITEMS.rows), redlines)
    per_sheet = len(PAVEMENT_ROWS)
    for index, front in enumerate(fronts):
        page = page_number + index if page_number is not None else None
        stamp_common_header(workbook, front, AC_FRONT_HEADER, idr, project, contractor, inspector, page, redlines)
        if index < course_sheets:
            _stamp_site_conditions(workbook, front, data)
            _stamp_pavement_courses(workbook, front, courses[index * per_sheet:(index + 1) * per_sheet])
            _stamp_material_usage(workbook, front, data)
            _stamp_requirements(workbook, front, data)
            _stamp_tack_coat(workbook, front, data)
        if index < len(pay_slices):
            stamp_pay_items(workbook, front, AC_FRONT_PAY_ITEMS, pay_slices[index],
                            continued=index < len(pay_slices) - 1, redlines=redlines)
        if index:
            _write_note(workbook, front, CONTINUED_FROM_CELL, CONTINUED_FROM_NOTE)
        if index < course_sheets - 1:
            _write_note(workbook, front, COURSES_CONTINUED_CELL, COURSES_CONTINUED_NOTE)

    more_tickets = _stamp_delivery_tickets(workbook, back, data)
    note = MORE_TICKETS_NOTE.format(count=more_tickets, plural="" if more_tickets == 1 else "s")
    comments = redline_paragraphs(data.get("comments"), redlines.field("comments"))
    queue = comments + ([note] if more_tickets else []) + safety_remarks(data)
    continued = _stamp_remarks(workbook, back, idr, project, inspector, queue, report_cont_available)
    stamp_workforce(workbook, back, AC_BACK_WORKFORCE, data, redlines)
    stamp_equipment(workbook, back, AC_BACK_EQUIPMENT, data, redlines)
    stamp_safety(workbook, back, AC_BACK_SAFETY, data, redlines)
    pages = fronts + [back] + ([REPORT_CONT] if continued else [])
    for sheet in pages:
        workbook.fit_to_letter_page(sheet)
    return pages
