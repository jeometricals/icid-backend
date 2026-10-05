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
    SignatureLayout,
    CHECK_MARK, REPORT_CONT, REPORT_CONT_TEXT, EquipmentLayout, HeaderLayout, PayItemsLayout, SafetyLayout,
    TextArea, WorkforceLayout, allocate_copies, flow_text, pay_item_page_count, stamp_common_header, stamp_equipment,
    stamp_pay_item_pages, stamp_report_cont, stamp_safety, stamp_workforce, write_lines,
)
from api.services.xlsx_template import EMU_PER_PIXEL, WorkbookTemplate

GEN_FRONT = "Gen Fr"
GEN_BACK = "Gen Bk"

# Gen Bk's signature line is row 59 (C59:M59), its date AE59:AH59; the image also takes the blank row above
SIGNATURE_LAYOUT = SignatureLayout(signature_cells="C58:M59", signature_cx_emu=209 * EMU_PER_PIXEL,
                                   signature_cy_emu=34 * EMU_PER_PIXEL, date_cell="AE59")

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

GEN_FRONT_TEXT = TextArea(rows=range(22, 35), column="B", line_chars=60)  # B22:AP22 … B34:AP34, 14 pt lines
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

# Comment lines B3:AI3 … B6:AI6, 14 pt; rows 7-25 below them are the sketch grid
GEN_BACK_TEXT = TextArea(rows=range(3, 7), column="B", line_chars=60)
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


# ---- The General ------------------------------------------------------------

def stamp_general(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], inspector: Optional[str],
                  general_data: dict[str, Any], page_number: Optional[int],
                  fronts: Optional[list[str]] = None) -> list[str]:
    """
    Stamp the General onto Gen Fr (and copies of it for pay items past its table), Gen Bk and, for long text,
    Report Cont.
    Takes the workbook, IDR row, project details (with "contractor"), inspector name, the General's report_data,
    its page number (None for a composed General) and its front pages (Gen Fr, Gen Fr 2, ...; None clones them here).
    Returns the sheets to print, in order: the fronts and Gen Bk always (Gen Bk carries the certification and the
    signature lines even when nothing else is on it), then Report Cont when the text continues onto it.
    """
    pay_items = general_data.get("payItems")
    fronts = fronts or allocate_copies(workbook, GEN_FRONT, pay_item_page_count(pay_items, GEN_FRONT_PAY_ITEMS))
    _stamp_front_header(workbook, idr, project, inspector, page_number)
    stamp_pay_item_pages(workbook, fronts, GEN_FRONT_HEADER, GEN_FRONT_PAY_ITEMS, GEN_FRONT_TEXT, idr, project,
                         project.get("contractor"), inspector, page_number, pay_items)

    # The General is stamped first, so Report Cont is always free for it
    flow = flow_text(general_data.get("description"), general_data.get("comments"),
                     GEN_FRONT_TEXT, GEN_BACK_TEXT, REPORT_CONT_TEXT)
    write_lines(workbook, GEN_FRONT, GEN_FRONT_TEXT.rows, flow.front, GEN_FRONT_TEXT.column)
    workbook.set_cell(GEN_FRONT, REVERSE_PAGE_BOX, CHECK_MARK if flow.past_front else None)
    write_lines(workbook, GEN_BACK, GEN_BACK_TEXT.rows, flow.back, GEN_BACK_TEXT.column)
    workbook.set_cell(GEN_BACK, CONTINUED_BOX, CHECK_MARK if flow.past_back else None)

    stamp_workforce(workbook, GEN_BACK, GEN_BACK_WORKFORCE, general_data)
    stamp_equipment(workbook, GEN_BACK, GEN_BACK_EQUIPMENT, general_data)
    stamp_safety(workbook, GEN_BACK, GEN_BACK_SAFETY, general_data)

    sheets = fronts + [GEN_BACK]
    if flow.report_cont:
        stamp_report_cont(workbook, idr, project, inspector, flow.report_cont)
        sheets.append(REPORT_CONT)
    return sheets
