"""
Stamps an IDR's Concrete Truck & Mix Info (CONC_MIX) addendum onto the DDC template's Conc Mix page.

Conc Mix is a single page: a compact header (project details, date, page number and inspector; no day of the week,
times, temperatures or weather), Location of Use, Mixer Type, the Trucks table, Concrete Specifications, Material
Usage and Remarks. Only the header is stamped so far.

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import short_date
from api.services.xlsx_template import WorkbookTemplate

CONC_MIX = "Conc Mix"

# The header. HeaderLayout doesn't fit (it requires the day, times, temperatures and weather this form lacks), so it
# is stamped here, as Report Cont's is. The template has formulas reading Contract Info in the project cells, replaced
# by the values themselves; F14 is the top left of the two-row F14:V15 merge. The date, page and "of" cells (AD8,
# AD10, AJ10) are unmerged, General-formatted blanks on an underline, so the date goes in as m/d/yy text. The
# inspector's line H17:V17 is three merges (H17:M17, N17:Q17, R17:V17); the name goes in the first.
CONC_MIX_PROJECT_CELLS = {"G8": "project_id", "P8": "registration_code", "I10": "project_description",
                          "F12": "borough", "F14": "contractor"}
CONC_MIX_DATE = "AD8"
CONC_MIX_SHEET_NO = "AD10"
CONC_MIX_SHEET_OF = "AJ10"
CONC_MIX_INSPECTOR = "H17"
CONC_MIX_IR_NO = "AJ17"  # "ATTACHMENT TO I.R. NO.": left blank, the I.R. number is assigned later


def _stamp_header(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                  contractor: Optional[str], inspector: Optional[str], page_number: Optional[int]) -> None:
    """
    Write Conc Mix's header: project details (as values, not Contract Info formulas), date, page number and inspector.
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, and the report's page
    number (None leaves PAGE / OF blank). Returns nothing; ATTACHMENT TO I.R. NO. stays blank.
    """
    for cell, field in CONC_MIX_PROJECT_CELLS.items():
        workbook.set_cell(CONC_MIX, cell, contractor if field == "contractor" else project.get(field))
    workbook.set_cell(CONC_MIX, CONC_MIX_DATE, short_date(idr["report_date"]))
    has_page = page_number is not None
    workbook.set_cell(CONC_MIX, CONC_MIX_SHEET_NO, page_number if has_page else None)
    workbook.set_cell(CONC_MIX, CONC_MIX_SHEET_OF, idr.get("total_pages") if has_page else None)
    workbook.set_cell(CONC_MIX, CONC_MIX_INSPECTOR, inspector)
    workbook.set_cell(CONC_MIX, CONC_MIX_IR_NO, None)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None) -> list[str]:
    """
    Stamp a CONC_MIX report onto the Conc Mix page (the header so far; report_data isn't read yet).
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, the report's page number
    (None leaves PAGE / OF blank) and its report_data.
    Returns the sheets it used: Conc Mix, set to print on one Letter page; the caller decides which sheets show.
    """
    _stamp_header(workbook, idr, project, contractor, inspector, page_number)
    workbook.fit_to_letter_page(CONC_MIX)
    return [CONC_MIX]
