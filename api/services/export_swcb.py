"""
Stamps an IDR's Sidewalk, Curb, Concrete Base (SWCB) report onto the DDC template's Conc Fr / Conc Bk pages.

Conc Fr is the front: the header block, the Description of Work, the operation line (Curb / Sidewalk / Concrete Base
/ Structural, and the subcontractor), Detailed Activity, the Inspection Matrix and Pay Items. Conc Bk is the back:
work force, equipment, the MPT/safety checklist and remarks. A description longer than Conc Fr's five lines continues
on Conc Bk's Remarks, followed by the comments, and then on Report Cont (see export_common.flow_text).

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import (
    CHECK_MARK, REPORT_CONT, REPORT_CONT_TEXT, EquipmentLayout, HeaderLayout, PayItemsLayout, SafetyLayout, TextArea,
    WorkforceLayout, allocate_copies, flow_text, pay_item_page_count, section, stamp_common_header, stamp_equipment,
    stamp_pay_item_pages, stamp_report_cont, stamp_safety, stamp_workforce, text_value, write_lines,
)
from api.services.xlsx_template import WorkbookTemplate

CONC_FRONT = "Conc Fr"
CONC_BACK = "Conc Bk"

# Same cells as Gen Fr's header, except: the date cell is a single General-formatted cell (so the date goes in as
# m/d/yy text), and each weather box is one merged area whose "AM" / "PM" label sits at its top left, so the value
# shares the cell under the label (AD17 / AK17 are inside those merges here, not value cells).
CONC_FRONT_HEADER = HeaderLayout(
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

# Description of Work: five ruled lines B23:AP23 … B27:AP27 in 10 pt (B22 is a spacer row). They span the same width
# as Gen Fr's 14 pt lines, which hold 60 characters, so 10 pt lines hold about 60 x 14 / 10 = 84. There is no "reverse
# page used" box on Conc Fr, so nothing is ticked when the description continues on Conc Bk.
CONC_FRONT_TEXT = TextArea(rows=range(23, 28), column="B", line_chars=84)

# The operation line (row 29): checkbox rectangles (transparent since the template cleanup) over these cells, an
# 8 x 8 px box centred in each 16 x 17 px cell; a centred 6 pt "X" sits inside the outline.
OPERATION_BOXES = {"curb": "E29", "sidewalk": "K29", "base": "S29", "structural": "Z29"}
OPERATION_MARK_FONT_PT = 6
SUBCONTRACTOR_CELL = "AJ29"  # AJ29:AP29, a short underlined blank

# Detailed Activity: rows 33-35 (row 36 is a blank spare row), From L:Q, To R:W, Remarks X:AP
ACTIVITY_ROWS = {"excavation": 33, "formPrep": 34, "pour": 35}
ACTIVITY_COLUMNS = {"fromStation": "L", "toStation": "R", "remarks": "X"}

# Inspection Matrix rows 40-46. Each column (base / sidewalk / curb) has a Y, N and N/A box (two merged cells each).
# Which columns an item takes mirrors the frontend's MATRIX_ROWS (SWCBInspectionMatrix.jsx) and the template's boxes:
# row 42 has Base and Sidewalk boxes (Curb is merged away), row 43 only Base, row 45 a write-in box per column.
MATRIX_ANSWER_CELLS = {
    "base": {"Y": "X", "N": "Z", "NA": "AB"},
    "sidewalk": {"Y": "AD", "N": "AF", "NA": "AH"},
    "curb": {"Y": "AJ", "N": "AL", "NA": "AN"},
}
MATRIX_TEXT_CELLS = {"base": "X", "sidewalk": "AD", "curb": "AJ"}  # row 45's write-in boxes X:AC, AD:AI, AJ:AO
ALL_COLUMNS = ("base", "sidewalk", "curb")
MATRIX_ROWS = [  # (row, key, columns that take an answer, free text)
    (40, "subgradeCompacted", ALL_COLUMNS, False),
    (41, "compactionTestTaken", ALL_COLUMNS, False),
    (42, "sidewalkFoundationPlaced", ("base", "sidewalk"), False),
    (43, "roadwayStoneBasePlaced", ("base",), False),
    (44, "curingCompoundApplied", ALL_COLUMNS, False),
    (45, "otherCuringMethods", ALL_COLUMNS, True),
    (46, "rebarInstalled", ALL_COLUMNS, False),
]

# Pay Items: rows 49-60 under the header at row 48 (15 pt rows). The Description cell U:AP is ~341 px usable against
# Gen Fr's ~293 px X:AP (same column widths, three more columns), so Gen Fr's 40 / 48 characters a line scale to 46 / 55.
CONC_FRONT_PAY_ITEMS = PayItemsLayout(
    rows=range(49, 61),
    columns={"itemNo": "B", "budgetCode": "F", "payQuantity": "K", "quantityChk": "P", "description": "U"},
    line_chars_10pt=46, line_chars_8pt=55,
)

# ---- Conc Bk ----------------------------------------------------------------

# Work Force (rows 5-17, No. in G:H): the frontend's five roles on rows 5-9; Teamsters, Surveyors and Masons are
# pre-printed (rows 10-12) and filled when added as trades; other trades take blank rows 13-17, label in B.
CONC_BACK_WORKFORCE = WorkforceLayout(
    role_rows={"superintendent": 5, "foremen": 6, "operators": 7, "laborers": 8, "flaggers": 9},
    trade_rows={"teamsters": 10, "surveyors": 11, "masons": 12},
    free_rows=(13, 14, 15, 16, 17),
    label_column="B", count_column="G",
)

# Equipment (rows 5-17, Model / Size N:U + No. V:X, then a second pair Y:AF + AG:AI). The SWCB form's four standard
# types each have a row; its added-equipment options each have their own pre-printed row too (the template writes the
# rollers with a hyphen, the frontend with an en dash). Excavator (only in older SWCB reports) and anything else go on
# blank row 17, label written in; a second unit of a pre-printed type uses that row's second pair.
CONC_BACK_EQUIPMENT_EXTRA_ROWS = {
    "crane": 7, "paving machine": 8, "ac distributor": 9, "sweepers": 10, "trailers": 11,
    "roller – static": 13, "roller - static": 13, "roller – dynamic": 14, "roller - dynamic": 14, "hand tamper": 16,
}
CONC_BACK_EQUIPMENT = EquipmentLayout(
    standard_rows={"frontEndLoader": 5, "backhoe": 6, "truckDump": 12, "compressor": 15},
    extra_rows=CONC_BACK_EQUIPMENT_EXTRA_ROWS,
    row_names=frozenset(CONC_BACK_EQUIPMENT_EXTRA_ROWS),  # every pre-printed row is its own type: no variant names
    free_rows=(17,),
    label_column="I",
    slots=(("N", "V"), ("Y", "AG")),
)

# End of the Day MPT/Safety Check List: rows 40-49, Y in N:O, N in P:Q, Remarks R:AI (no N/A column)
CONC_BACK_SAFETY = SafetyLayout(
    rows={"plasticBarrels": 40, "pedestrianBarricades": 41, "timberCurbs": 42, "timberBreakawayBarricades": 43,
          "generalSafety": 44, "localEmergencyAccess": 45, "fencing": 46, "plates": 47, "arrowBoard": 48,
          "siteCleaned": 49},
    yes_column="N", no_column="P", remarks_column="R",
)

# Remarks: the "Remarks:" label at C19, then fifteen 11 pt ruled lines C20:AH20 … C34:AH34 (about 600 px; 75
# characters a line at the same margin as Gen Bk's comment lines). They take the description's overflow and the
# comments. "Attached Pages for Additional Remarks / Sketches / Calculations" (C52, a transparent checkbox rectangle)
# is ticked when the text continues on Report Cont. Signatures (rows 55-60) stay blank: the inspector and RE sign.
CONC_BACK_TEXT = TextArea(rows=range(20, 35), column="C", line_chars=75)
ATTACHED_PAGES_BOX = "C52"
# "See attached Concrete Truck and Mixing Information" (Z37, also a transparent rectangle): ticked by the dispatcher
# when this SWCB report has a CONC_MIX addendum
CONC_MIX_ATTACHED_BOX = "Z37"


def _answer(value: Any) -> Optional[str]:
    """
    Normalise one Inspection Matrix answer.
    Takes the saved value.
    Returns 'Y', 'N' or 'NA', or None when unanswered (or anything else).
    """
    return value if value in ("Y", "N", "NA") else None


def matrix_answers(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Read the Inspection Matrix as the form can show it: answers only in the columns each item takes, text on the
    text item.
    Takes the report_data.
    Returns {item key: {column: 'Y' | 'N' | 'NA' | None, or text | None for the text item}}.
    """
    matrix = section(data, "inspectionMatrix")
    answers = {}
    for _, key, columns, is_text in MATRIX_ROWS:
        line = matrix.get(key) if isinstance(matrix.get(key), dict) else {}
        if is_text:
            answers[key] = {column: text_value(line.get(column)) for column in ALL_COLUMNS}
        else:
            answers[key] = {column: _answer(line.get(column)) if column in columns else None for column in ALL_COLUMNS}
    return answers


def operation_types(data: dict[str, Any]) -> dict[str, bool]:
    """
    Decide which operation boxes to tick: Base, Sidewalk and Curb when any Inspection Matrix item has an answer (or,
    on the text item, text) in that column; Structural when report_data.structural is true.
    Takes the report_data.
    Returns {"base" / "sidewalk" / "curb" / "structural": whether to tick it}.
    """
    answers = matrix_answers(data)
    ticked = {column: any(line[column] is not None for line in answers.values()) for column in ALL_COLUMNS}
    ticked["structural"] = data.get("structural") is True
    return ticked


def _tick(workbook: WorkbookTemplate, sheet: str, cell: str, ticked: bool) -> None:
    """
    Tick (or clear) one of the drawn checkbox rectangles: a small centred "X" in the cell under it.
    Takes the workbook, sheet name, the cell under the rectangle and whether to tick it.
    Returns nothing.
    """
    workbook.set_cell(sheet, cell, CHECK_MARK if ticked else None)
    if ticked:
        workbook.set_font_size(sheet, cell, OPERATION_MARK_FONT_PT)
        workbook.center_cell(sheet, cell)


def mark_conc_mix_attached(workbook: WorkbookTemplate, back: str = CONC_BACK) -> None:
    """
    Tick a Conc Bk's "See attached Concrete Truck and Mixing Information" box.
    Takes the workbook (after render has stamped the page) and the back page (Conc Bk unless given).
    Returns nothing.
    """
    _tick(workbook, back, CONC_MIX_ATTACHED_BOX, True)


def _stamp_operation(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Tick the operation boxes and write the subcontractor.
    Takes the workbook, the front page and the report_data.
    Returns nothing.
    """
    for operation, ticked in operation_types(data).items():
        _tick(workbook, front, OPERATION_BOXES[operation], ticked)
    workbook.set_cell(front, SUBCONTRACTOR_CELL, text_value(data.get("subcontractor")))
    workbook.shrink_to_fit_cell(front, SUBCONTRACTOR_CELL)


def _stamp_activity(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Write the Detailed Activity rows (Excavation, Form / Prep, Pour): from and to station, and remarks.
    Takes the workbook, the front page and the report_data.
    Returns nothing.
    """
    activity = section(data, "activity")
    for key, row in ACTIVITY_ROWS.items():
        entry = activity.get(key) if isinstance(activity.get(key), dict) else {}
        for field, column in ACTIVITY_COLUMNS.items():
            workbook.set_cell(front, f"{column}{row}", text_value(entry.get(field)))
        workbook.shrink_to_fit_cell(front, f"{ACTIVITY_COLUMNS['remarks']}{row}")


def _stamp_matrix(workbook: WorkbookTemplate, front: str, data: dict[str, Any]) -> None:
    """
    Write the Inspection Matrix: an X in the Y, N or N/A box of each answered column, and the write-in text.
    Takes the workbook, the front page and the report_data.
    Returns nothing.
    """
    answers = matrix_answers(data)
    for row, key, columns, is_text in MATRIX_ROWS:
        for column in columns:
            value = answers[key][column]
            if is_text:
                cell = f"{MATRIX_TEXT_CELLS[column]}{row}"
                workbook.set_cell(front, cell, value)
                workbook.shrink_to_fit_cell(front, cell)
                continue
            for option, letter in MATRIX_ANSWER_CELLS[column].items():
                workbook.set_cell(front, f"{letter}{row}", CHECK_MARK if value == option else None)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, report_cont_available: bool = True,
           fronts: Optional[list[str]] = None, back: str = CONC_BACK) -> list[str]:
    """
    Stamp an SWCB report onto a Conc Fr / Conc Bk pair, and onto Report Cont when its text runs past the Remarks.
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, the report's page number
    (None leaves Sheet No. blank), its report_data (None stamps the header only), and whether Report Cont is free
    (False when another report in the export already continues onto it; the Remarks are then cut with a note), and
    its front pages (Conc Fr and, for pay items past the table, copies of it; None clones them here) and its back
    page (Conc Bk unless given). Overflow fronts get only a header and pay items.
    Returns the sheets it used, in print order: the fronts, the back, and Report Cont when used; each is set to print on
    one Letter page, and the caller decides which sheets the workbook shows.
    """
    data = report_data if isinstance(report_data, dict) else {}
    fronts = fronts or allocate_copies(workbook, CONC_FRONT, pay_item_page_count(data.get("payItems"),
                                                                                 CONC_FRONT_PAY_ITEMS))
    front = fronts[0]
    stamp_common_header(workbook, front, CONC_FRONT_HEADER, idr, project, contractor, inspector, page_number)

    flow = flow_text(data.get("description"), data.get("comments"), CONC_FRONT_TEXT, CONC_BACK_TEXT,
                     REPORT_CONT_TEXT if report_cont_available else None)
    write_lines(workbook, front, CONC_FRONT_TEXT.rows, flow.front, CONC_FRONT_TEXT.column)
    write_lines(workbook, back, CONC_BACK_TEXT.rows, flow.back, CONC_BACK_TEXT.column)
    _tick(workbook, back, ATTACHED_PAGES_BOX, bool(flow.report_cont))

    _stamp_operation(workbook, front, data)
    _stamp_activity(workbook, front, data)
    _stamp_matrix(workbook, front, data)
    stamp_pay_item_pages(workbook, fronts, CONC_FRONT_HEADER, CONC_FRONT_PAY_ITEMS, CONC_FRONT_TEXT, idr, project,
                         contractor, inspector, page_number, data.get("payItems"))
    stamp_workforce(workbook, back, CONC_BACK_WORKFORCE, data)
    stamp_equipment(workbook, back, CONC_BACK_EQUIPMENT, data)
    stamp_safety(workbook, back, CONC_BACK_SAFETY, data)

    pages = fronts + [back]
    if flow.report_cont:
        stamp_report_cont(workbook, idr, project, inspector, flow.report_cont)
        pages.append(REPORT_CONT)
    for sheet in pages:
        workbook.fit_to_letter_page(sheet)
    return pages
