"""
Exports a submitted IDR as an .xlsx file built on the DDC report-forms template.

This module loads the IDR's data and assembles the workbook; each report's pages are stamped by its own module
(export_general for the General, export_swcb for a Sidewalk, Curb, Concrete Base report, export_conc_mix for a
Concrete Truck & Mix Info report). Pages that hold nothing stay hidden, so the file prints only the IDR's pages. A draft
IDR exports too, with "DRAFT - Not for Submission" across the top of every page it prints.
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
from api.services import export_conc_mix, export_swcb
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


def _load_reports(idr_id: UUID) -> list[dict[str, Any]]:
    """
    Load every report in the IDR, addendums included, in page order (creation order on a draft).
    Takes the IDR uuid.
    Returns the report rows; raises ExportDataError if they can't load.
    """
    reports = list_reports_for_idr(idr_id)
    if reports is None:
        raise ExportDataError("Failed to load IDR reports")
    return reports


def _swcb_report(reports: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    Find the IDR's SWCB report to print on Conc Fr / Conc Bk.
    Takes the IDR's reports, in page order.
    Returns the first non-addendum SWCB report, or None.
    The template has one Conc Fr / Conc Bk pair, so a second SWCB report in the same IDR isn't exported yet.
    """
    return next((r for r in reports if r["report_type"] == "SWCB" and not r["is_addendum"]), None)


def _conc_mix_report(reports: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    Find the IDR's Concrete Truck & Mix Info report to print on Conc Mix.
    Takes the IDR's reports, in page order.
    Returns the first CONC_MIX report, addendum or not (the flag is the frontend's to set), or None.
    The template has one Conc Mix page, so a second CONC_MIX report in the same IDR isn't exported yet.
    """
    return next((r for r in reports if r["report_type"] == "CONC_MIX"), None)


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
    Build an IDR's .xlsx export from the report-forms template: the General's pages, then the SWCB report's, then the
    Conc Mix page (whose box on Conc Bk is then ticked). A draft IDR's pages are each marked "DRAFT - Not for
    Submission".
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
    reports = _load_reports(idr_id)
    swcb, conc_mix = _swcb_report(reports), _conc_mix_report(reports)

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

    if conc_mix is not None:
        # Conc Mix sits between SWR Bk and HC Fr in the template; it prints last, after the page printed last so far
        workbook.move_sheet(export_conc_mix.CONC_MIX, after=pages[-1])
        pages += export_conc_mix.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=conc_mix["page_number"], report_data=conc_mix["report_data"])
        if swcb is not None:
            export_swcb.mark_conc_mix_attached(workbook)

    for page in pages:
        workbook.fit_to_letter_page(page)
        if idr["status"] != "submitted":
            stamp_draft_marker(workbook, page)
    workbook.show_only(pages)

    return IdrExport(
        filename=f"IDR_{idr_id}_{idr['report_date'].isoformat()}.xlsx",
        content=workbook.to_bytes(),
    )
