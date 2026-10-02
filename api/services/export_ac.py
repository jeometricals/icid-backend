"""
Stamps an IDR's Asphaltic Concrete (AC) report onto the DDC template's AC Fr / AC Bk pages.

AC Fr is the front: the header block, the paving contractor and temperatures, theoretical max density, the pavement
course table, material usage for top and binder, Pay Items, the A/C requirements and tack coat. AC Bk is the back:
remarks, work force and equipment, the MPT/safety checklist beside the delivery ticket log, and the signature block.
Only the header is stamped so far; both pages always print with an AC report (AC Bk carries the certification and
the signature lines).

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import HeaderLayout, stamp_common_header
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


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, fronts: Optional[list[str]] = None,
           back: str = AC_BACK) -> list[str]:
    """
    Stamp an AC report onto its AC Fr / AC Bk pages (the header so far; report_data isn't read yet).
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, the report's page number
    (None leaves Sheet No. blank), its report_data, its front pages (AC Fr unless given) and its back page (AC Bk
    unless given).
    Returns the sheets it used, in print order: the fronts, then the back; each is set to print on one Letter page,
    and the caller decides which sheets the workbook shows.
    """
    fronts = fronts or [AC_FRONT]
    stamp_common_header(workbook, fronts[0], AC_FRONT_HEADER, idr, project, contractor, inspector, page_number)
    pages = fronts + [back]
    for sheet in pages:
        workbook.fit_to_letter_page(sheet)
    return pages
