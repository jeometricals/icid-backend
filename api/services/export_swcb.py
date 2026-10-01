"""
Stamps an IDR's Sidewalk, Curb, Concrete Base (SWCB) report onto the DDC template's Conc Fr / Conc Bk pages.

Conc Fr is the front: the header block, then the operation line, Detailed Activity, the Inspection Matrix and Pay
Items. Conc Bk is the back: work force, equipment, remarks and the MPT/safety checklist. So far only the header is
stamped; the body follows in the next slices, and export.py doesn't call this module until it is complete.

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import HeaderLayout, stamp_common_header
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


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None) -> None:
    """
    Stamp an SWCB report onto Conc Fr / Conc Bk and set the workbook to show and print just those two pages.
    Takes the workbook, the IDR row, the project row, the contractor's and inspector's names, and the report's page
    number (None leaves Sheet No. blank).
    Returns nothing; the calculation chain is dropped when the workbook is serialized (WorkbookTemplate.to_bytes).
    """
    stamp_common_header(workbook, CONC_FRONT, CONC_FRONT_HEADER, idr, project, contractor, inspector, page_number)
    for sheet in (CONC_FRONT, CONC_BACK):
        workbook.fit_to_letter_page(sheet)
    workbook.show_only([CONC_FRONT, CONC_BACK])
