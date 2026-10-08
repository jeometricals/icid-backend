"""
The DDC template's Conc Cyl page: the Data Sheet for Concrete Test Cylinders (Rev. 10/31/03).

Conc Cyl is a single page: a header (project details, date and day of the week, the testing laboratory's block), the
delivery and casting details, a table of 18 cylinders, the specific location of placement and the two signature lines.
This module names the sheet, says where its signatures go and gives each report its sheet; render stamps nothing
yet, so the export prints the blank form (with the draft marker or the signatures the dispatcher adds).

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import SignatureLayout, allocate_copies
from api.services.export_redlines import NO_REDLINES, Redlines
from api.services.xlsx_template import EMU_PER_PIXEL, WorkbookTemplate

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


def allocate_sheets(workbook: WorkbookTemplate, first_index: int = 0) -> list[str]:
    """
    Provide the sheet one CONC_CYL report prints on (Conc Cyl, Conc Cyl 2, ... numbered across the IDR's CONC_CYL
    reports), cloning the blank Conc Cyl for each past the template's own.
    Takes the workbook and the report's position among the IDR's CONC_CYL reports (0 for the first).
    Returns the report's sheet names: always one.
    """
    return allocate_copies(workbook, SHEET_NAME, 1, first_index)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, sheets: Optional[list[str]] = None,
           redlines: Redlines = NO_REDLINES) -> list[str]:
    """
    Stamp a Concrete Test Cylinders report onto its Conc Cyl sheet. Not built yet: it stamps nothing.
    Takes what export_conc_mix.render takes: the workbook, the IDR row, the project row, the contractor's and
    inspector's names, the report's page number, its report_data, the sheets to use and the report's redlines.
    Returns the sheets it used, in order: the ones given, or Conc Cyl alone.
    """
    return sheets if sheets is not None else allocate_sheets(workbook)
