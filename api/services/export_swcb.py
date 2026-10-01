"""
Stamps an IDR's Sidewalk, Curb, Concrete Base (SWCB) report onto the DDC template's Conc Fr / Conc Bk pages.

Conc Fr is the front: the header block, the Description of Work, the operation line (Curb / Sidewalk / Concrete Base
/ Structural, and the subcontractor), Detailed Activity, the Inspection Matrix and Pay Items. Conc Bk is the back:
work force, equipment, remarks and the MPT/safety checklist (not stamped yet). export.py doesn't call this module until
it is complete.

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import (
    CHECK_MARK, HeaderLayout, PayItemsLayout, fill_lines, mark_truncated, paragraphs, stamp_common_header,
    stamp_pay_items, text_value, write_lines,
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
# as Gen Fr's 14 pt lines, which hold 60 characters, so 10 pt lines hold about 60 x 14 / 10 = 84.
# TODO(D3.2d): continue a longer description onto Conc Bk's Remarks and Report Cont, as the General does; for now it
# is cut on the fifth line with the "continued in ICID" note.
DESCRIPTION_ROWS = range(23, 28)
DESCRIPTION_LINE_CHARS = 84

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


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    """
    Read one object-valued section of report_data, tolerating a missing or malformed one.
    Takes the report_data and the section's key.
    Returns the section, or {} when it isn't an object.
    """
    value = data.get(key)
    return value if isinstance(value, dict) else {}


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
    matrix = _section(data, "inspectionMatrix")
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


def _stamp_description(workbook: WorkbookTemplate, data: dict[str, Any]) -> None:
    """
    Write the Description of Work on Conc Fr's five ruled lines, left-aligned, cut with a note if it doesn't fit.
    Takes the workbook and the report_data.
    Returns nothing.
    """
    queue = paragraphs(data.get("description"))
    lines = fill_lines(queue, len(DESCRIPTION_ROWS), DESCRIPTION_LINE_CHARS)
    if queue:
        lines = mark_truncated(lines, DESCRIPTION_LINE_CHARS)
    write_lines(workbook, CONC_FRONT, DESCRIPTION_ROWS, lines)


def _stamp_operation(workbook: WorkbookTemplate, data: dict[str, Any]) -> None:
    """
    Tick the operation boxes and write the subcontractor.
    Takes the workbook and the report_data.
    Returns nothing.
    """
    for operation, ticked in operation_types(data).items():
        cell = OPERATION_BOXES[operation]
        workbook.set_cell(CONC_FRONT, cell, CHECK_MARK if ticked else None)
        if ticked:
            workbook.set_font_size(CONC_FRONT, cell, OPERATION_MARK_FONT_PT)
            workbook.center_cell(CONC_FRONT, cell)
    workbook.set_cell(CONC_FRONT, SUBCONTRACTOR_CELL, text_value(data.get("subcontractor")))
    workbook.shrink_to_fit_cell(CONC_FRONT, SUBCONTRACTOR_CELL)


def _stamp_activity(workbook: WorkbookTemplate, data: dict[str, Any]) -> None:
    """
    Write the Detailed Activity rows (Excavation, Form / Prep, Pour): from and to station, and remarks.
    Takes the workbook and the report_data.
    Returns nothing.
    """
    activity = _section(data, "activity")
    for key, row in ACTIVITY_ROWS.items():
        entry = activity.get(key) if isinstance(activity.get(key), dict) else {}
        for field, column in ACTIVITY_COLUMNS.items():
            workbook.set_cell(CONC_FRONT, f"{column}{row}", text_value(entry.get(field)))
        workbook.shrink_to_fit_cell(CONC_FRONT, f"{ACTIVITY_COLUMNS['remarks']}{row}")


def _stamp_matrix(workbook: WorkbookTemplate, data: dict[str, Any]) -> None:
    """
    Write the Inspection Matrix: an X in the Y, N or N/A box of each answered column, and the write-in text.
    Takes the workbook and the report_data.
    Returns nothing.
    """
    answers = matrix_answers(data)
    for row, key, columns, is_text in MATRIX_ROWS:
        for column in columns:
            value = answers[key][column]
            if is_text:
                cell = f"{MATRIX_TEXT_CELLS[column]}{row}"
                workbook.set_cell(CONC_FRONT, cell, value)
                workbook.shrink_to_fit_cell(CONC_FRONT, cell)
                continue
            for option, letter in MATRIX_ANSWER_CELLS[column].items():
                workbook.set_cell(CONC_FRONT, f"{letter}{row}", CHECK_MARK if value == option else None)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None) -> None:
    """
    Stamp an SWCB report onto Conc Fr / Conc Bk and set the workbook to show and print just those two pages.
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, the report's page number
    (None leaves Sheet No. blank) and its report_data (None stamps the header only).
    Returns nothing; the calculation chain is dropped when the workbook is serialized (WorkbookTemplate.to_bytes).
    """
    stamp_common_header(workbook, CONC_FRONT, CONC_FRONT_HEADER, idr, project, contractor, inspector, page_number)
    data = report_data if isinstance(report_data, dict) else {}
    _stamp_description(workbook, data)
    _stamp_operation(workbook, data)
    _stamp_activity(workbook, data)
    _stamp_matrix(workbook, data)
    stamp_pay_items(workbook, CONC_FRONT, CONC_FRONT_PAY_ITEMS, data.get("payItems"))
    for sheet in (CONC_FRONT, CONC_BACK):
        workbook.fit_to_letter_page(sheet)
    workbook.show_only([CONC_FRONT, CONC_BACK])
