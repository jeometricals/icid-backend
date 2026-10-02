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


def _extra_conc_mix_sheets(conc_mix: Optional[dict[str, Any]]) -> int:
    """
    Count the Conc Mix clones a CONC_MIX report needs for its trucks (sheets past the first).
    Takes the CONC_MIX report row, or None.
    Returns the number of extra sheets, 0 without a report.
    """
    return export_conc_mix.sheet_count(conc_mix["report_data"]) - 1 if conc_mix is not None else 0


def _page_after_clones(page: Optional[int], conc_mix: Optional[dict[str, Any]], extra_sheets: int) -> Optional[int]:
    """
    Renumber a report's page around the Conc Mix clones: the database gives each report one page, and the clones take
    the numbers right after the CONC_MIX report's, so every report numbered after it moves down by the clone count.
    Takes the report's page number (None on a draft), the CONC_MIX report row (or None) and the clone count.
    Returns the page number to print.
    """
    if page is None or conc_mix is None or conc_mix["page_number"] is None or page <= conc_mix["page_number"]:
        return page
    return page + extra_sheets


def _conc_mix_anchor(conc_mix: dict[str, Any], reports: list[dict[str, Any]], swcb: Optional[dict[str, Any]],
                     pages: list[str], report_cont_owner: Optional[str]) -> str:
    """
    Find the sheet the Conc Mix pages print right after: the last page of the report they're an addendum to.
    Takes the CONC_MIX report row, the IDR's reports, the exported SWCB report (or None), the pages so far in print
    order, and the back page whose report continues on Report Cont (or None).
    Returns Gen Bk for a General parent, Conc Bk for the exported SWCB, or Report Cont instead when that parent
    continues there. Any other parent (an AC report, which isn't exported yet), or none, puts them at the end.
    """
    parent_id = conc_mix.get("parent_report_id")
    parent = next((r for r in reports if parent_id is not None and r.get("report_id") == parent_id), None)
    if parent is not None and parent["report_type"] == "GEN" and not parent["is_addendum"]:
        back = GEN_BACK
    elif parent is not None and swcb is not None and parent["report_id"] == swcb.get("report_id"):
        back = export_swcb.CONC_BACK
    else:
        return pages[-1]
    return REPORT_CONT if report_cont_owner == back else back


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
    Conc Mix page and its clones (whose box on Conc Bk is then ticked). The clones are numbered after the CONC_MIX
    report and counted in OF. A draft IDR's pages are each marked "DRAFT - Not for Submission".
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
    extra_sheets = _extra_conc_mix_sheets(conc_mix)
    if extra_sheets and idr.get("total_pages") is not None:
        idr = {**idr, "total_pages": idr["total_pages"] + extra_sheets}  # each Conc Mix clone is a page of its own
    page_number = _page_after_clones(page_number, conc_mix, extra_sheets)

    workbook = WorkbookTemplate(TEMPLATE_PATH)
    _stamp_contract_info(workbook, project)
    pages = stamp_general(workbook, idr, project, inspector, general_data, page_number)
    report_cont_owner = GEN_BACK if REPORT_CONT in pages else None

    if swcb is not None:
        # The General comes first, so it keeps Report Cont if it needed it; the SWCB's long text is then cut instead
        swcb_pages = export_swcb.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=_page_after_clones(swcb["page_number"], conc_mix, extra_sheets),
                                        report_data=swcb["report_data"],
                                        report_cont_available=report_cont_owner is None)
        if REPORT_CONT in swcb_pages:
            report_cont_owner = export_swcb.CONC_BACK
        pages += [page for page in swcb_pages if page not in pages]

    # The template keeps Report Cont near the front; it prints right after the back page of the report it continues
    # (visible sheets print in tab order, and the first page listed, Gen Fr, is the tab the file opens on)
    workbook.move_sheet(REPORT_CONT, after=report_cont_owner or GEN_BACK)

    if conc_mix is not None:
        # Conc Mix sits between SWR Bk and HC Fr in the template; it moves to follow its parent report, and render
        # places any clones right after it
        anchor = _conc_mix_anchor(conc_mix, reports, swcb, pages, report_cont_owner)
        workbook.move_sheet(export_conc_mix.CONC_MIX, after=anchor)
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
