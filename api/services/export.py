"""
Exports a submitted IDR as an .xlsx file built on the DDC report-forms template.

This module loads the IDR's data and assembles the workbook; each report's pages are stamped by its own module
(export_general for the General, export_swcb for a Sidewalk, Curb, Concrete Base report). Pages that hold nothing stay
hidden, so the file prints only the IDR's pages. A draft IDR exports too, with "DRAFT - Not for Submission" across the
top of every page it prints.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from api.queries.idr_reports import get_general_report, list_non_general_main_reports, list_reports_for_idr
from api.queries.idrs import get_idr_by_id
from api.queries.projects import get_project_by_id, get_project_contractor_name
from api.queries.users import get_user_by_id
from api.schemas.idr_report import ADDENDUM_TYPES
from api.services import export_swcb
from api.services.auto_general import build_auto_general_data
from api.services.export_common import REPORT_CONT, stamp_draft_marker
from api.services.export_general import GEN_BACK, stamp_general
from api.services.xlsx_template import WorkbookTemplate

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "report_forms.xlsx"

CONTRACT_INFO = "Contract Info"


class ExportError(Exception):
    """Base class for an IDR that can't be exported."""


class IdrNotFoundError(ExportError):
    """No IDR has that id."""


class ExportDataError(ExportError):
    """The IDR's project or reports could not be loaded."""


@dataclass
class IdrExport:
    """A generated export: the file name to offer and the .xlsx bytes."""

    filename: str
    content: bytes


def _inspector_name(user: Optional[dict[str, Any]]) -> Optional[str]:
    """
    Format the inspector's name for the form.
    Takes the users row (or None).
    Returns "First Last", falling back to the email, or None when the user is unknown.
    """
    if user is None:
        return None
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return name or user.get("email")


def _general_for_export(idr_id: UUID) -> tuple[dict[str, Any], Optional[int]]:
    """
    Find the General to print: the IDR's own, or one composed the way the auto-General is when it has none.
    Takes the IDR uuid.
    Returns (its report_data, its page number or None when composed); raises ExportDataError if reports can't load.
    """
    general = get_general_report(idr_id)
    if general is not None:
        return general["report_data"] or {}, general["page_number"]
    reports = list_non_general_main_reports(idr_id)
    if reports is None:
        raise ExportDataError("Failed to load IDR reports")
    contributing = [report for report in reports if report["report_type"] not in ADDENDUM_TYPES]
    return build_auto_general_data(contributing), None


def _swcb_report(idr_id: UUID) -> Optional[dict[str, Any]]:
    """
    Find the IDR's SWCB report to print on Conc Fr / Conc Bk.
    Takes the IDR uuid.
    Returns the first non-addendum SWCB report in page order, or None; raises ExportDataError if reports can't load.
    The template has one Conc Fr / Conc Bk pair, so a second SWCB report in the same IDR isn't exported yet.
    """
    reports = list_reports_for_idr(idr_id)
    if reports is None:
        raise ExportDataError("Failed to load IDR reports")
    return next((r for r in reports if r["report_type"] == "SWCB" and not r["is_addendum"]), None)


def _stamp_contract_info(workbook: WorkbookTemplate, project: dict[str, Any]) -> None:
    """
    Write the project details the forms' header formulas read (kept even though visible pages get the values directly).
    Takes the workbook and the project details (with "contractor").
    Returns nothing; the Resident Engineer (C7) stays blank, as the data model has no source for it yet.
    """
    values = {
        "C2": project["project_id"],
        "C3": project.get("registration_code"),
        "C4": project.get("project_description"),
        "C5": project.get("borough"),
        "C6": project.get("contractor"),
        "C7": None,
    }
    for coordinate, value in values.items():
        workbook.set_cell(CONTRACT_INFO, coordinate, value)


def generate_idr_export(idr_id: UUID) -> IdrExport:
    """
    Build an IDR's .xlsx export from the report-forms template: the General's pages, then the SWCB report's. A draft
    IDR's pages are each marked "DRAFT - Not for Submission".
    Takes the IDR uuid.
    Returns an IdrExport (file name and bytes); raises IdrNotFoundError or ExportDataError.
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        raise IdrNotFoundError("IDR not found")

    project = get_project_by_id(idr["project_id"])
    if project is None:
        raise ExportDataError("Failed to load the IDR's project")
    project = {**project, "contractor": get_project_contractor_name(idr["project_id"])}
    inspector = _inspector_name(get_user_by_id(idr["reporter_uuid"]))
    general_data, page_number = _general_for_export(idr_id)
    swcb = _swcb_report(idr_id)

    workbook = WorkbookTemplate(TEMPLATE_PATH)
    _stamp_contract_info(workbook, project)
    pages = stamp_general(workbook, idr, project, inspector, general_data, page_number)
    report_cont_owner = GEN_BACK if REPORT_CONT in pages else None

    if swcb is not None:
        # The General comes first, so it keeps Report Cont if it needed it; the SWCB's long text is then cut instead
        swcb_pages = export_swcb.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=swcb["page_number"], report_data=swcb["report_data"],
                                        report_cont_available=report_cont_owner is None)
        if REPORT_CONT in swcb_pages:
            report_cont_owner = export_swcb.CONC_BACK
        pages += [page for page in swcb_pages if page not in pages]

    # The template keeps Report Cont near the front; it prints right after the back page of the report it continues
    # (visible sheets print in tab order, and the first page listed, Gen Fr, is the tab the file opens on)
    workbook.move_sheet(REPORT_CONT, after=report_cont_owner or GEN_BACK)
    for page in pages:
        workbook.fit_to_letter_page(page)
        if idr["status"] != "submitted":
            stamp_draft_marker(workbook, page)
    workbook.show_only(pages)

    return IdrExport(
        filename=f"IDR_{idr_id}_{idr['report_date'].isoformat()}.xlsx",
        content=workbook.to_bytes(),
    )
