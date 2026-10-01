"""
Stamps an IDR's General onto the DDC template's General pages.

Gen Fr is the front: header, Description of Work and Pay Items. Gen Bk is the back: comments,
the MPT/safety checklist, work force and equipment. Report Cont takes whatever text doesn't fit.

Text flows in a cascade. The description fills Gen Fr's ruled lines; what doesn't fit continues on
Gen Bk's comment lines (Gen Fr's "Reverse page used" box ticked), followed by the comments. What
doesn't fit there continues on Report Cont (Gen Bk's "Continued on next page" box ticked), and what
doesn't fit on Report Cont is cut with a note pointing to ICID.

Cell positions come from reading templates/report_forms.xlsx; see the constants below.
"""

from typing import Any, Optional

from api.services.export_common import (
    CHECK_MARK, EquipmentLayout, HeaderLayout, PayItemsLayout, SafetyLayout, WorkforceLayout, fill_lines,
    highlight_day, mark_truncated, paragraphs, short_date, stamp_common_header, stamp_equipment,
    stamp_pay_items, stamp_safety, stamp_workforce, write_lines,
)
from api.services.xlsx_template import WorkbookTemplate

GEN_FRONT = "Gen Fr"
GEN_BACK = "Gen Bk"
REPORT_CONT = "Report Cont"

# ---- Gen Fr -----------------------------------------------------------------

GEN_FRONT_HEADER = HeaderLayout(
    project_cells={"G8": "project_id", "P8": "registration_code", "I10": "project_description",
                   "F12": "borough", "F14": "contractor"},
    date="AI4", date_as_text=False,  # a merged cell formatted m/d/yy
    day_of_week=("AI5", "AJ5", "AK5", "AL5", "AM5", "AN5", "AO5"),
    ir_no="AH6", sheet_no="AH8", sheet_of="AM8",
    work_time="AG10", inspector_time="AG12",
    temp_low="AD13", temp_high="AK13",
    weather_am="AD17", weather_pm="AK17", weather_labels=None,  # value boxes under separate "AM" / "PM" labels
    inspector="H17",
)

DESCRIPTION_ROWS = range(22, 35)  # B22:AP22 … B34:AP34, size-14 ruled lines
DESCRIPTION_LINE_CHARS = 60
REVERSE_PAGE_BOX = "AC36"  # "Reverse page used for additional remarks."

# Pay Items: rows 39-50 under the header at row 38 (row 51 is the form's footer). The Description cell (X:AP,
# merged, centred Arial 10) is about 293 px wide inside its padding: measured Arial fits ~46 characters a line at
# 10 pt and ~55 at 8 pt; the budgets keep a safety margin (a 43-character item was seen clipping in a viewer).
GEN_FRONT_PAY_ITEMS = PayItemsLayout(
    rows=range(39, 51),
    columns={"itemNo": "B", "budgetCode": "G", "payQuantity": "N", "quantityChk": "S", "description": "X"},
    line_chars_10pt=40, line_chars_8pt=48,
)

# ---- Gen Bk -----------------------------------------------------------------

COMMENT_ROWS = range(3, 7)  # B3:AI3 … B6:AI6, size-14 ruled lines; rows 7-25 are the sketch grid
COMMENT_LINE_CHARS = 60
CONTINUED_BOX = "Z27"  # "Continued on next page (if any)."

GEN_BACK_SAFETY = SafetyLayout(
    rows={"plasticBarrels": 30, "pedestrianBarricades": 31, "timberCurbs": 32, "timberBreakawayBarricades": 33,
          "generalSafety": 34, "localEmergencyAccess": 35, "fencing": 36, "plates": 37, "arrowBoard": 38,
          "siteCleaned": 39},
    yes_column="N", no_column="P", remarks_column="R",
)

GEN_BACK_WORKFORCE = WorkforceLayout(
    role_rows={"superintendent": 43, "foremen": 44, "operators": 45, "laborers": 46, "flaggers": 47},
    trade_rows={"carpenters": 48, "timbermen": 49, "masons": 50, "teamsters": 51},  # pre-printed on the form
    free_rows=(52, 53),
    label_column="B", count_column="G",
)

GEN_BACK_EQUIPMENT = EquipmentLayout(
    standard_rows={"frontEndLoader": 43, "backhoe": 44, "truckDump": 46, "compressor": 48, "excavator": 49},
    # Added equipment the form has a pre-printed row for (by the frontend's labels, lower-cased)
    extra_rows={"crane": 45, "roller": 47, "roller – static": 47, "roller – dynamic": 47, "trailers": 50,
                "pavement cutter": 51, "tampers": 52, "hand tamper": 52},
    row_names=frozenset({"crane", "roller", "trailers", "pavement cutter", "tampers"}),  # as printed on those rows
    free_rows=(53,),
    label_column="I",
    slots=(("N", "V"), ("Y", "AG")),  # each row has two Model / Size + No. pairs
)

# ---- Report Cont ------------------------------------------------------------

REPORT_CONT_ROWS = range(21, 46)  # B21 … B45, size-10 ruled lines spanning B:AI
REPORT_CONT_LINE_CHARS = 85
REPORT_CONT_DAY_CELLS = ("I11", "J11", "K11", "L11", "M11", "N11", "O11")  # S M T W T F S
REPORT_CONT_PROJECT_CELLS = {"G14": "project_id", "P14": "registration_code", "I15": "project_description",
                             "F17": "borough"}


# ---- Gen Fr ------------------------------------------------------------------

def _stamp_front_header(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                        inspector: Optional[str], page_number: Optional[int]) -> None:
    """
    Write Gen Fr's header block (see stamp_common_header).
    Takes the workbook, IDR row, project details (with "contractor"), inspector name and the General's page number.
    Returns nothing; Sheet No. is blank for a composed General, which isn't one of the IDR's numbered pages.
    """
    stamp_common_header(workbook, GEN_FRONT, GEN_FRONT_HEADER, idr, project, project.get("contractor"),
                        inspector, page_number)


# ---- Report Cont ------------------------------------------------------------

def _stamp_report_cont_header(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                              inspector: Optional[str]) -> None:
    """
    Write Report Cont's header: date, day, project details (as values) and inspector, clearing its blank-line
    placeholders.
    Takes the workbook, IDR row, project details and inspector name.
    Returns nothing.
    """
    workbook.set_cell(REPORT_CONT, "I10", short_date(idr["report_date"]))
    highlight_day(workbook, REPORT_CONT, REPORT_CONT_DAY_CELLS, idr["report_date"])
    workbook.set_cell(REPORT_CONT, "U10", None)  # I.R. No. "__________"
    workbook.set_cell(REPORT_CONT, "AA10", "Sheet No.:")  # was "Sheet No.: ____ of ____"
    for cell, field in REPORT_CONT_PROJECT_CELLS.items():
        workbook.set_cell(REPORT_CONT, cell, project.get(field))
    workbook.set_cell(REPORT_CONT, "H19", inspector)


# ---- The General ------------------------------------------------------------

def stamp_general(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], inspector: Optional[str],
                  general_data: dict[str, Any], page_number: Optional[int]) -> list[str]:
    """
    Stamp the General onto Gen Fr, Gen Bk and (for long text) Report Cont.
    Takes the workbook, IDR row, project details (with "contractor"), inspector name, the General's report_data
    and its page number (None for a composed General).
    Returns the sheets that now hold content, in print order: Gen Fr always, Gen Bk and Report Cont when used.
    """
    _stamp_front_header(workbook, idr, project, inspector, page_number)
    stamp_pay_items(workbook, GEN_FRONT, GEN_FRONT_PAY_ITEMS, general_data.get("payItems"))

    description = paragraphs(general_data.get("description"))
    write_lines(workbook, GEN_FRONT, DESCRIPTION_ROWS, fill_lines(description, len(DESCRIPTION_ROWS), DESCRIPTION_LINE_CHARS))

    # What continues past Gen Fr: the rest of the description, then the comments
    comments = paragraphs(general_data.get("comments"))
    continuation = []
    if description:
        continuation += ["Description of work (continued):"] + description
        if comments:
            continuation.append("Comments:")
    continuation += comments
    workbook.set_cell(GEN_FRONT, REVERSE_PAGE_BOX, CHECK_MARK if description else None)

    back_lines = fill_lines(continuation, len(COMMENT_ROWS), COMMENT_LINE_CHARS)
    write_lines(workbook, GEN_BACK, COMMENT_ROWS, back_lines)
    workbook.set_cell(GEN_BACK, CONTINUED_BOX, CHECK_MARK if continuation else None)

    back_used = bool(back_lines)
    back_used = stamp_workforce(workbook, GEN_BACK, GEN_BACK_WORKFORCE, general_data) or back_used
    back_used = stamp_equipment(workbook, GEN_BACK, GEN_BACK_EQUIPMENT, general_data) or back_used
    back_used = stamp_safety(workbook, GEN_BACK, GEN_BACK_SAFETY, general_data) or back_used

    sheets = [GEN_FRONT] + ([GEN_BACK] if back_used else [])
    if continuation:
        cont_lines = fill_lines(continuation, len(REPORT_CONT_ROWS), REPORT_CONT_LINE_CHARS)
        if continuation:
            cont_lines = mark_truncated(cont_lines, REPORT_CONT_LINE_CHARS)
        _stamp_report_cont_header(workbook, idr, project, inspector)
        write_lines(workbook, REPORT_CONT, REPORT_CONT_ROWS, cont_lines)
        sheets.append(REPORT_CONT)
    return sheets
