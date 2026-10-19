"""
Stamps an IDR's Daily Site Patrol (DSP) report onto the DDC template's DSP Fr page (Daily Site Patrol, Rev. 10/31/03).

DSP Fr is a single page: a header (project details, the date and day of the week, I.R. No., sheet number and time of
patrol), the Maintenance of Site checklist (6 items) and the Maintenance and Protection of Traffic checklist (16), each
answered YES / NO / N/A with details, whether a field order was issued, and the Resident Engineer's line. The form has
no inspector's signature line, so only the Resident Engineer's signature prints, on an approved IDR. The day of the
week, the sheet number and the I.R. No. come from the IDR, never from report_data. DSP Bk is not printed, and nothing
on the page is redlined: it prints the values as they stand.

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.schemas.dsp_report import MOS_ITEMS, MPT_ITEMS
from api.services.export_common import (
    CHECK_MARK, SignatureLayout, allocate_copies, fill_lines, highlight_day, ir_number, mark_truncated, object_rows,
    paragraphs, short_date, text_value, tick_box,
)
from api.services.export_redlines import NO_REDLINES, Redlines
from api.services.xlsx_template import EMU_PER_PIXEL, WorkbookTemplate

SHEET_NAME = "DSP Fr"

# The Resident Engineer's line is Q87:Z87, its caption ("Resident Engineer") Q88:Z88 and the page's one Date line
# AN87:AQ87. Columns Q to U are 16 px and V to Z 13 px, rows 86 and 87 17 px, so with the blank row above the box is
# 5 x 16 + 5 x 13 = 145 px by 34 px. The Date cell belongs to this layout: the page has no inspector's line.
RE_SIGNATURE_LAYOUT = SignatureLayout(signature_cells="Q86:Z87", signature_cx_emu=145 * EMU_PER_PIXEL,
                                      signature_cy_emu=34 * EMU_PER_PIXEL, date_cell="AN87",
                                      column_widths_px=(16,) * 5 + (13,) * 5, caption_cells="Q88:Z88")

# The header's project details, each a merged area right of its label: formulas reading Contract Info in the
# template, replaced by the values themselves. Contract No. is the project's id.
PROJECT_CELLS = {"G8": "project_id", "P8": "registration_code", "I10": "project_description", "F12": "borough",
                 "F14": "contractor"}

# The date line is laid out for a pen: AK8:AL8 (merged), then five one-column cells, with a "/" drawn as a diagonal
# border in AM8 and AO8. The merge is widened to AK8:AQ8 and the two lose their slash by taking AK8's style (centred,
# 10 pt, underlined), which the date keeps.
DATE = "AK8"
DATE_MERGE = "AK8:AL8"
DATE_AREA = "AK8:AQ8"
DATE_SLASHES = ("AM8", "AO8")
DAY_OF_WEEK = ("AK10", "AL10", "AM10", "AN10", "AO10", "AP10", "AQ10")  # the S M T W T F S letters, Sunday first
IR_NO = "AJ12"         # AJ12:AQ12
SHEET_NO = "AJ13"      # AJ13:AL14, then "of" and AO13:AQ14
SHEET_OF = "AO13"
TIME_OF_PATROL = "AK15"  # AK15:AQ16

# The checklists: each question is a 3-row merge (B:U), its YES, NO and N/A boxes the bordered cells W, Z and AC of the
# middle row (13 x 10 px), and its details the 3-row merge AE:AR (217 px) beside it.
MOS_ROWS = range(19, 37, 3)  # rows 19, 22, ... 34
MPT_ROWS = range(38, 86, 3)  # rows 38, 41, ... 83
ANSWER_COLUMNS = {"yes": "W", "no": "Z", "na": "AC"}
DETAILS_COLUMN = "AE"
# The details print at 7 pt on two lines (the merge is 22.5 pt high). Conc Mix's 8 pt lines hold 0.16 characters a
# pixel, so a 7 pt line across 217 px holds about 39; 38 keeps a margin. Longer text is cut with "continued in ICID".
DETAILS_FONT_PT = 7
DETAILS_LINES = 2
DETAILS_LINE_CHARS = 38

# Field Order Issued?: two 10 x 10 px boxes drawn in AI87 (Y) and AK87 (N). They are white-filled rectangles, which
# would hide the X, so their fill is cleared (the template file is unchanged). The form has no place for the field
# order's number, so the label (AA87:AH87) carries it: "Field Order Issued? (No. FO-12)", shrunk to fit.
FIELD_ORDER_BOXES = {True: "AI87", False: "AK87"}
FIELD_ORDER_BOX_SHAPES = ("Rectangle 1", "Rectangle 2")  # the Y box's, then the N box's
FIELD_ORDER_LABEL = "AA87"
FIELD_ORDER_LABEL_TEXT = "Field Order Issued?"
FIELD_ORDER_MARK_FONT_PT = 7  # as Conc Mix marks its 10 x 10 px boxes


def allocate_sheets(workbook: WorkbookTemplate, first_index: int = 0) -> list[str]:
    """
    Provide the sheet one DSP report prints on (DSP Fr, DSP Fr 2, ... numbered across the IDR's DSP reports), cloning
    the blank DSP Fr for each past the template's own.
    Takes the workbook and the report's position among the IDR's DSP reports (0 for the first).
    Returns the report's sheet names: always one.
    """
    return allocate_copies(workbook, SHEET_NAME, 1, first_index)


def checklist_rows(data: dict[str, Any], key: str, items: tuple[str, ...]) -> list[dict[str, Any]]:
    """
    Read one checklist in the form's order, tolerating a missing list or rows out of place.
    Takes the report_data, the checklist's key ("mosChecklist" or "mptChecklist") and its items in order.
    Returns one row per item ({} for an item the list doesn't hold); the first row for an item wins.
    """
    rows: dict[str, dict[str, Any]] = {}
    for row in object_rows(data, key):
        rows.setdefault(row.get("item"), row)
    return [rows.get(item, {}) for item in items]


def details_text(details: Any) -> Optional[str]:
    """
    Lay a row's details out on its two lines.
    Takes the details as saved.
    Returns the lines joined by a line break, cut with "continued in ICID" when they don't fit; None when blank.
    """
    queue = paragraphs(details)
    lines = fill_lines(queue, DETAILS_LINES, DETAILS_LINE_CHARS)
    if queue:
        lines = mark_truncated(lines, DETAILS_LINE_CHARS)
    return "\n".join(lines) or None


def _stamp_header(workbook: WorkbookTemplate, sheet: str, idr: dict[str, Any], project: dict[str, Any],
                  contractor: Optional[str], page_number: Optional[int], data: dict[str, Any]) -> None:
    """
    Write the page's header: project details, the IDR's work date and its day of the week, I.R. No., Sheet No. and
    "of", and the time of patrol.
    Takes the workbook, the sheet, the IDR row (its total_pages fills "of"), the project row, the contractor's name,
    the page's number (None leaves Sheet No. and "of" blank) and the report_data.
    Returns nothing; I.R. No. stays blank for an IDR nobody has accepted yet.
    """
    for cell, field in PROJECT_CELLS.items():
        workbook.set_cell(sheet, cell, contractor if field == "contractor" else project.get(field))
        workbook.shrink_to_fit_cell(sheet, cell)
    workbook.unmerge_cells(sheet, DATE_MERGE)
    workbook.merge_cells(sheet, DATE_AREA)
    style = workbook.cell_style(sheet, DATE)
    for cell in DATE_SLASHES:
        workbook.set_style(sheet, cell, style)
    workbook.set_cell(sheet, DATE, short_date(idr["report_date"]))
    highlight_day(workbook, sheet, DAY_OF_WEEK, idr["report_date"])
    workbook.set_cell(sheet, IR_NO, ir_number(idr))
    has_page = page_number is not None
    workbook.set_cell(sheet, SHEET_NO, page_number if has_page else None)
    workbook.set_cell(sheet, SHEET_OF, idr.get("total_pages") if has_page else None)
    workbook.set_cell(sheet, TIME_OF_PATROL, text_value(data.get("timeOfPatrol")))
    workbook.center_cell(sheet, TIME_OF_PATROL)


def _stamp_checklist(workbook: WorkbookTemplate, sheet: str, rows: list[dict[str, Any]], first_rows: range) -> None:
    """
    Mark one checklist: an X in the box of each row's answer (none while unanswered), and its details.
    Takes the workbook, the sheet, the checklist's rows in the form's order (see checklist_rows) and the form rows
    each question starts on.
    Returns nothing.
    """
    for row, entry in zip(first_rows, rows):
        answer = entry.get("answer")
        for option, column in ANSWER_COLUMNS.items():
            tick_box(workbook, sheet, f"{column}{row + 1}", answer == option)
        cell = f"{DETAILS_COLUMN}{row}"
        text = details_text(entry.get("details"))
        workbook.set_cell(sheet, cell, text)
        if text:
            workbook.set_font_size(sheet, cell, DETAILS_FONT_PT)


def _stamp_field_order(workbook: WorkbookTemplate, sheet: str, data: dict[str, Any]) -> None:
    """
    Tick Field Order Issued?'s Y or N box (neither when it isn't answered), and add the field order's number to the
    label when one was issued.
    Takes the workbook, the sheet and the report_data.
    Returns nothing.
    """
    issued = data.get("fieldOrderIssued")
    for shape in FIELD_ORDER_BOX_SHAPES:
        workbook.clear_shape_fill(sheet, shape)
    for answer, cell in FIELD_ORDER_BOXES.items():
        ticked = issued is answer
        workbook.set_cell(sheet, cell, CHECK_MARK if ticked else None)
        if ticked:
            workbook.set_font_size(sheet, cell, FIELD_ORDER_MARK_FONT_PT)
            workbook.center_cell(sheet, cell)
    number = text_value(data.get("fieldOrderNumber")) if issued is True else None
    if number:
        workbook.set_cell(sheet, FIELD_ORDER_LABEL, f"{FIELD_ORDER_LABEL_TEXT} (No. {number})")
        workbook.shrink_to_fit_cell(sheet, FIELD_ORDER_LABEL)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, sheets: Optional[list[str]] = None,
           redlines: Redlines = NO_REDLINES) -> list[str]:
    """
    Stamp a DSP report onto its DSP Fr sheet: the header, both checklists and Field Order Issued?. Reviewers' edits
    are not printed as redlines (the page shows the values as they stand), so redlines is taken and ignored.
    Takes what export_conc_mix.render takes: the workbook, the IDR row (its total_pages fills "of"), the project row,
    the contractor's and inspector's names (the form has no place for the inspector), the report's page number (None
    leaves Sheet No. blank), its report_data (None stamps the header and an empty form), the sheets to use, from
    allocate_sheets (None allocates DSP Fr here, for an IDR's only DSP report) and the report's redlines.
    Returns the sheets it used, in order, each set to print on one Letter page; the caller decides which show.
    """
    data = report_data if isinstance(report_data, dict) else {}
    sheets = sheets if sheets is not None else allocate_sheets(workbook)
    for sheet in sheets:
        _stamp_header(workbook, sheet, idr, project, contractor, page_number, data)
        _stamp_checklist(workbook, sheet, checklist_rows(data, "mosChecklist", MOS_ITEMS), MOS_ROWS)
        _stamp_checklist(workbook, sheet, checklist_rows(data, "mptChecklist", MPT_ITEMS), MPT_ROWS)
        _stamp_field_order(workbook, sheet, data)
        workbook.fit_to_letter_page(sheet)
    return sheets
