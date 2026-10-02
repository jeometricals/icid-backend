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
from api.services.export_general import stamp_general
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


def _swcb_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find the IDR's SWCB reports, each printed on a Conc Fr / Conc Bk pair.
    Takes the IDR's reports, in page order.
    Returns the non-addendum SWCB reports, in page order (empty when none).
    """
    return [r for r in reports if r["report_type"] == "SWCB" and not r["is_addendum"]]


def _conc_mix_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find the IDR's Concrete Truck & Mix Info reports, each printed on one or more Conc Mix sheets.
    Takes the IDR's reports, in page order.
    Returns the CONC_MIX reports, addendum or not (the flag is the frontend's to set), in page order.
    """
    return [r for r in reports if r["report_type"] == "CONC_MIX"]


def _extra_sheets(conc_mix: dict[str, Any]) -> int:
    """
    Count the Conc Mix sheets a CONC_MIX report needs past its first (one per 11 trucks).
    Takes the CONC_MIX report row.
    Returns the number of extra sheets.
    """
    return export_conc_mix.sheet_count(conc_mix["report_data"]) - 1


def _page_after_clones(page: Optional[int], conc_mixes: list[dict[str, Any]]) -> Optional[int]:
    """
    Renumber a report's page around the Conc Mix clones: the database gives each report one page, and a CONC_MIX
    report's extra sheets take the numbers right after its own, so every page after it moves down by that many.
    Takes the report's page number (None on a draft) and the IDR's CONC_MIX reports.
    Returns the page number to print: the page plus the extra sheets of every CONC_MIX numbered before it.
    """
    if page is None:
        return None
    return page + sum(_extra_sheets(r) for r in conc_mixes if r["page_number"] is not None and r["page_number"] < page)


def _swcb_sheets(index: int) -> tuple[str, str]:
    """
    Name the Conc Fr / Conc Bk pair an SWCB report prints on.
    Takes its zero-based position among the IDR's SWCB reports.
    Returns the template's own pair for the first, ("Conc Fr 2", "Conc Bk 2"), ... for the clones after it.
    """
    if index == 0:
        return export_swcb.CONC_FRONT, export_swcb.CONC_BACK
    return f"{export_swcb.CONC_FRONT} {index + 1}", f"{export_swcb.CONC_BACK} {index + 1}"


def _allocate_sheets(workbook: WorkbookTemplate, swcbs: list[dict[str, Any]],
                     conc_mixes: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], list[list[str]]]:
    """
    Provide every SWCB and CONC_MIX report its sheets before anything is stamped, cloning the blank forms as needed.
    Conc Mix sheets are numbered across the whole IDR (Conc Mix, Conc Mix 2, ...) whichever report they belong to.
    Takes the workbook and the SWCB and CONC_MIX reports, in page order.
    Returns (each SWCB's (front, back) pair, each CONC_MIX's sheet names), in the reports' order.
    """
    pairs = [_swcb_sheets(index) for index in range(len(swcbs))]
    for front, back in pairs[1:]:
        workbook.clone_sheet(export_swcb.CONC_FRONT, front)
        workbook.clone_sheet(export_swcb.CONC_BACK, back)
    conc_mix_sheets: list[list[str]] = []
    used = 0
    for report in conc_mixes:
        conc_mix_sheets.append(export_conc_mix.allocate_sheets(workbook, report["report_data"], first_index=used))
        used += len(conc_mix_sheets[-1])
    return pairs, conc_mix_sheets


def _print_order(groups: list[tuple[Optional[UUID], list[str]]], conc_mixes: list[dict[str, Any]],
                 conc_mix_sheets: list[list[str]]) -> list[str]:
    """
    Put the pages in print order: each main report's pages, then its CONC_MIX addendums' sheets.
    Takes the main reports' groups in page order ((report id, its pages, including Report Cont when it continues
    there); the id is None for a General composed for the export), the CONC_MIX reports and each one's sheets.
    Returns the pages in order. A CONC_MIX whose parent isn't printed (an AC report, not exported yet), or that has
    none, comes last.
    """
    printed = {report_id for report_id, _ in groups if report_id is not None}
    children: dict[UUID, list[str]] = {}
    orphans: list[str] = []
    for report, sheets in zip(conc_mixes, conc_mix_sheets):
        parent = report.get("parent_report_id")
        if parent in printed:
            children.setdefault(parent, []).extend(sheets)
        else:
            orphans.extend(sheets)
    return [page for report_id, pages in groups for page in pages + children.get(report_id, [])] + orphans


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
    Build an IDR's .xlsx export from the report-forms template: the General's pages, then each SWCB report's (on Conc
    Fr / Conc Bk and clones of them), each report followed by its CONC_MIX addendums' Conc Mix sheets; an SWCB's
    Conc Bk ticks its "See attached" box when it has one. Extra Conc Mix sheets are numbered after their report and
    counted in OF. A draft IDR's pages are each marked "DRAFT - Not for Submission".
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
    swcbs, conc_mixes = _swcb_reports(reports), _conc_mix_reports(reports)
    extra_sheets = sum(_extra_sheets(r) for r in conc_mixes)
    if extra_sheets and idr.get("total_pages") is not None:
        idr = {**idr, "total_pages": idr["total_pages"] + extra_sheets}  # each Conc Mix clone is a page of its own
    general_id = next((r["report_id"] for r in reports if r["report_type"] == "GEN" and not r["is_addendum"]), None)

    workbook = WorkbookTemplate(TEMPLATE_PATH)
    swcb_sheets, conc_mix_sheets = _allocate_sheets(workbook, swcbs, conc_mixes)
    _stamp_contract_info(workbook, project)
    general_pages = stamp_general(workbook, idr, project, inspector, general_data,
                                  _page_after_clones(page_number, conc_mixes))
    groups = [(general_id, general_pages)]
    report_cont_used = REPORT_CONT in general_pages

    # Reports stamp in page order, so the first to need Report Cont keeps it; later ones have their long text cut
    for swcb, (front, back) in zip(swcbs, swcb_sheets):
        swcb_pages = export_swcb.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=_page_after_clones(swcb["page_number"], conc_mixes),
                                        report_data=swcb["report_data"], report_cont_available=not report_cont_used,
                                        front=front, back=back)
        report_cont_used = report_cont_used or REPORT_CONT in swcb_pages
        groups.append((swcb["report_id"], swcb_pages))
        if any(r.get("parent_report_id") == swcb["report_id"] for r in conc_mixes):
            export_swcb.mark_conc_mix_attached(workbook, back)

    for conc_mix, sheets in zip(conc_mixes, conc_mix_sheets):
        export_conc_mix.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                               page_number=_page_after_clones(conc_mix["page_number"], conc_mixes),
                               report_data=conc_mix["report_data"], sheets=sheets)

    # Visible sheets print in tab order, so each page moves right after the one before it (Report Cont from near the
    # front, Conc Mix from between SWR Bk and HC Fr); the first, Gen Fr, is the tab the file opens on
    pages = _print_order(groups, conc_mixes, conc_mix_sheets)
    for previous, page in zip(pages, pages[1:]):
        workbook.move_sheet(page, after=previous)

    for page in pages:
        workbook.fit_to_letter_page(page)
        if idr["status"] != "submitted":
            stamp_draft_marker(workbook, page)
    workbook.show_only(pages)

    return IdrExport(
        filename=f"IDR_{idr_id}_{idr['report_date'].isoformat()}.xlsx",
        content=workbook.to_bytes(),
    )
