"""
Stamps an IDR's Concrete Cylinder Data (CONC_CYL) addendum onto the DDC template's Conc Cyl page: the Data Sheet for
Concrete Test Cylinders (Rev. 10/31/03).

Conc Cyl is a single page: a header (project details, the date and day of the week, the testing laboratory's block),
the delivery and casting details, a table of 18 cylinders, the specific location of placement and the two signature
lines. The inspector fills the table's first three columns (Class, Cylinder #, Slump); the other five, and the
testing laboratory's block, are the lab's to fill by hand and are never written. Neither are Resident Engineer's
Name and CLIENT: the data model has no source for them.

Cell positions come from reading templates/report_forms.xlsx.
"""

import logging
from typing import Any, Optional

from api.services.export_common import (
    SignatureLayout, TextArea, allocate_copies, fill_lines, format_iso_as_mdy, highlight_day, mark_truncated,
    object_rows, redline_paragraphs, section, short_date, stamp_field, text_value, write_lines,
)
from api.services.export_redlines import NO_REDLINES, Redlines
from api.services.xlsx_template import EMU_PER_PIXEL, WorkbookTemplate

logger = logging.getLogger(__name__)

SHEET_NAME = "Conc Cyl"

# Conc Cyl's signature line is row 58 (C58:O58), its date AL58:AO58, the captions under them on row 59; the image
# also takes the blank row above. Its columns are 16 px and rows 57 and 58 are 17 px (12.75 pt), so the box is
# 13 x 16 = 208 px by 34 px.
SIGNATURE_LAYOUT = SignatureLayout(signature_cells="C57:O58", signature_cx_emu=208 * EMU_PER_PIXEL,
                                   signature_cy_emu=34 * EMU_PER_PIXEL, date_cell="AL58", column_px=16)
# The Resident Engineer's line is W58:AI58, its caption W59:AI59. Column X is 11 px, the other twelve 16 px, so the
# box is 203 px wide.
RE_SIGNATURE_LAYOUT = SignatureLayout(signature_cells="W57:AI58", signature_cx_emu=203 * EMU_PER_PIXEL,
                                      signature_cy_emu=34 * EMU_PER_PIXEL,
                                      column_widths_px=(16, 11) + (16,) * 11, caption_cells="W59:AI59")

# The header's project details: each a merged, underlined area right of its label. Contract No. is the project's id.
PROJECT_CELLS = {"F8": "project_id", "P8": "registration_code", "H10": "project_description", "F12": "borough",
                 "F14": "contractor"}
# The template has two of them bold, which a typed value shouldn't be: each takes the style of a plain one beside it
PLAIN_STYLES = {"P8": "F8", "F12": "F14"}

# The date line is laid out for a pen: seven one-column cells after the "Date:" label, with a "/" drawn as a diagonal
# border in two of them. They are merged before the date is written, and the two lose their slash by taking the
# label's style (centred, 10 pt, no border), which the date takes too.
DATE_LABEL = "AF5"
DATE_AREA = "AI5:AO5"
DATE = "AI5"
DATE_SLASHES = ("AK5", "AM5")
DAY_OF_WEEK = ("AI7", "AJ7", "AK7", "AL7", "AM7", "AN7", "AO7")  # the S M T W T F S letters, Sunday first

# The report's own fields, by the field_path an edit names each by, and what each takes: "Sheet No. ___ of ___" is
# whatever the inspector typed, and the two dates are saved as ISO text
FIELD_CELLS = {"sheetNo": "AH20", "sheetOf": "AM20"}
DELIVERY_CASTING_CELLS = {"dateOfDelivery": "I22", "cyPoured": "AD22", "jobLocation": "I24", "dateCast": "AD24"}
DATE_FIELDS = frozenset({"dateOfDelivery", "dateCast"})

# The Cylinders table: rows 27-44, one merged area per column. Columns 4 to 8 (Age Day, Date Tested, Total Load, PSI,
# Page Cyl Reg.) are the lab's.
CYLINDER_ROWS = range(27, 45)
CYLINDER_COLUMNS = {"class": "B", "cylinderNo": "G", "slump": "M"}

# Specific Location of Placement: three ruled 10 pt lines. The first shares row 46 with its label (B46:Q46), so it
# starts at R46 and runs to AO (379 px); the other two run C47:AO48 (619 px). Conc Fr's 10 pt lines hold 84
# characters across 651 px, so the first line holds 48 and the others 79.
PLACEMENT_FIRST = TextArea(rows=range(46, 47), column="R", line_chars=48)
PLACEMENT_REST = TextArea(rows=range(47, 49), column="C", line_chars=79)
# The template has the second line bold; it takes the first line's style (the third's has a top border, which
# would rule a line under the label)
PLACEMENT_BOLD_LINE = "C47"
PLACEMENT_PLAIN_LINE = "R46"


def allocate_sheets(workbook: WorkbookTemplate, first_index: int = 0) -> list[str]:
    """
    Provide the sheet one CONC_CYL report prints on (Conc Cyl, Conc Cyl 2, ... numbered across the IDR's CONC_CYL
    reports), cloning the blank Conc Cyl for each past the template's own.
    Takes the workbook and the report's position among the IDR's CONC_CYL reports (0 for the first).
    Returns the report's sheet names: always one.
    """
    return allocate_copies(workbook, SHEET_NAME, 1, first_index)


def cylinder_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Read the Cylinders table from report_data, tolerating a missing list or malformed rows.
    Takes the report_data.
    Returns the cylinders (each a dict), in the inspector's order, each with "_path": the field_path an edit names it
    by ("cylinders[<its id>]"), or None for one without an id (a draft's, which nobody can have edited).
    """
    return [{**row, "_path": f"cylinders[{row['id']}]" if text_value(row.get("id")) else None}
            for row in object_rows(data, "cylinders")]


def _stamp_value(workbook: WorkbookTemplate, sheet: str, cell: str, value: Any, edits: list[dict[str, Any]],
                 is_date: bool = False) -> None:
    """
    Write one of the report's values into its cell, shrunk to fit its line; a value a reviewer edited prints as its
    chain. Takes the workbook, the sheet, the cell, the value, its edits (oldest first) and whether it is a date
    saved as ISO text (printed m/d/yy).
    Returns nothing.
    """
    stamp_field(workbook, sheet, cell, value, edits, show=format_iso_as_mdy if is_date else None)
    workbook.shrink_to_fit_cell(sheet, cell)


def _stamp_header(workbook: WorkbookTemplate, sheet: str, idr: dict[str, Any], project: dict[str, Any],
                  contractor: Optional[str]) -> None:
    """
    Write the page's header: project details, the IDR's work date and its day of the week.
    Takes the workbook, the sheet, the IDR row, the project row and the contractor's name.
    Returns nothing; Resident Engineer's Name, CLIENT and the testing laboratory's block stay blank.
    """
    for cell, plain in PLAIN_STYLES.items():
        workbook.set_style(sheet, cell, workbook.cell_style(sheet, plain))
    for cell, field in PROJECT_CELLS.items():
        workbook.set_cell(sheet, cell, contractor if field == "contractor" else project.get(field))
        workbook.shrink_to_fit_cell(sheet, cell)
    workbook.ensure_merged(sheet, DATE_AREA)
    style = workbook.cell_style(sheet, DATE_LABEL)
    for cell in DATE_SLASHES:
        workbook.set_style(sheet, cell, style)
    workbook.set_cell(sheet, DATE, short_date(idr["report_date"]), style=style)
    highlight_day(workbook, sheet, DAY_OF_WEEK, idr["report_date"])


def _stamp_details(workbook: WorkbookTemplate, sheet: str, data: dict[str, Any], redlines: Redlines) -> None:
    """
    Write Sheet No. and "of", and the delivery and casting details (date of delivery, C.Y. poured, job location,
    date cast).
    Takes the workbook, the sheet, the report_data and the report's redlines.
    Returns nothing.
    """
    for field, cell in FIELD_CELLS.items():
        _stamp_value(workbook, sheet, cell, data.get(field), redlines.field(field))
    details = section(data, "deliveryCasting")
    for field, cell in DELIVERY_CASTING_CELLS.items():
        _stamp_value(workbook, sheet, cell, details.get(field), redlines.field(f"deliveryCasting.{field}"),
                     is_date=field in DATE_FIELDS)


def _stamp_cylinders(workbook: WorkbookTemplate, sheet: str, cylinders: list[dict[str, Any]],
                     redlines: Redlines) -> None:
    """
    Fill the Cylinders table's first three columns, one cylinder per row; the lab's five columns are left alone.
    Takes the workbook, the sheet, the cylinders (see cylinder_rows; those past the table's 18 rows are dropped, with
    a warning, and rows past the last cylinder are emptied) and the report's redlines.
    Returns nothing.
    """
    if len(cylinders) > len(CYLINDER_ROWS):
        logger.warning("Conc Cyl holds %d cylinders; %d not printed", len(CYLINDER_ROWS),
                       len(cylinders) - len(CYLINDER_ROWS))
    for index, row in enumerate(CYLINDER_ROWS):
        cylinder = cylinders[index] if index < len(cylinders) else {}
        for field, column in CYLINDER_COLUMNS.items():
            edits = redlines.field(f"{cylinder['_path']}.{field}") if cylinder.get("_path") else []
            _stamp_value(workbook, sheet, f"{column}{row}", cylinder.get(field), edits)


def placement_lines(text: list[str]) -> tuple[list[str], list[str]]:
    """
    Lay the specific location of placement out on its three lines.
    Takes the text's paragraphs.
    Returns (the first line's text, the other lines' text); text that doesn't fit is cut with "continued in ICID".
    """
    queue = list(text)
    first = fill_lines(queue, len(PLACEMENT_FIRST.rows), PLACEMENT_FIRST.line_chars)
    rest = fill_lines(queue, len(PLACEMENT_REST.rows), PLACEMENT_REST.line_chars)
    if queue:
        rest = mark_truncated(rest, PLACEMENT_REST.line_chars)
    return first, rest


def _stamp_placement(workbook: WorkbookTemplate, sheet: str, data: dict[str, Any], redlines: Redlines) -> None:
    """
    Write the specific location of placement on its three lines; text a reviewer edited prints with its redlines.
    Takes the workbook, the sheet, the report_data and the report's redlines.
    Returns nothing.
    """
    text = redline_paragraphs(data.get("placementLocation"), redlines.field("placementLocation"))
    first, rest = placement_lines(text)
    workbook.set_style(sheet, PLACEMENT_BOLD_LINE, workbook.cell_style(sheet, PLACEMENT_PLAIN_LINE))
    write_lines(workbook, sheet, PLACEMENT_FIRST.rows, first, PLACEMENT_FIRST.column)
    write_lines(workbook, sheet, PLACEMENT_REST.rows, rest, PLACEMENT_REST.column)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, sheets: Optional[list[str]] = None,
           redlines: Redlines = NO_REDLINES) -> list[str]:
    """
    Stamp a CONC_CYL report onto its Conc Cyl sheet: the header, the delivery and casting details, the cylinders and
    the specific location of placement. What reviewers edited prints with its redlines; pass them as redlines (none
    unless given).
    Takes what export_conc_mix.render takes: the workbook, the IDR row, the project row, the contractor's and
    inspector's names and the report's page number (neither printed: the form has no place for them), its
    report_data (None stamps the header and an empty body), the sheets to use, from allocate_sheets (None allocates
    Conc Cyl here, for an IDR's only CONC_CYL report) and the report's redlines.
    Returns the sheets it used, in order, each set to print on one Letter page; the caller decides which show.
    """
    data = report_data if isinstance(report_data, dict) else {}
    sheets = sheets if sheets is not None else allocate_sheets(workbook)
    for sheet in sheets:
        _stamp_header(workbook, sheet, idr, project, contractor)
        _stamp_details(workbook, sheet, data, redlines)
        _stamp_cylinders(workbook, sheet, cylinder_rows(data), redlines)
        _stamp_placement(workbook, sheet, data, redlines)
        workbook.fit_to_letter_page(sheet)
    return sheets
