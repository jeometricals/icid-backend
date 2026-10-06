"""
Exports a submitted IDR as an .xlsx file built on the DDC report-forms template.

This module loads the IDR's data and assembles the workbook; each report's pages are stamped by its own module
(export_general for the General, export_swcb for a Sidewalk, Curb, Concrete Base report, export_conc_mix for a
Concrete Truck & Mix Info report). Pages that hold nothing stay hidden, so the file prints only the IDR's pages. A draft
IDR exports too, with "DRAFT - Not for Submission" across the top of every page it prints. From submission on, an
IDR's pages carry the inspector's signature (the copy the IDR kept at submit) and the date it was signed, wherever a
page has a signature line. An approved IDR's carry the Resident Engineer's signature beside it (the copy kept at
final approval), captioned with the approver's name and the date.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from api.core.config import EXPORT_BUCKET_NAME, EXPORT_URL_EXPIRY_SECONDS, SIGNATURE_BUCKET_NAME
from api.queries.idr_reports import get_general_report, list_non_general_main_reports, list_reports_for_idr
from api.queries.idrs import get_idr_by_id
from api.queries.projects import get_project_by_id, get_project_contractor_name
from api.queries.report_attachments import list_uploaded_attachments_for_reports
from api.queries.users import get_user_by_id
from api.schemas.idr_report import ADDENDUM_TYPES
from api.services import export_ac, export_attachments, export_conc_mix, export_swcb
from api.services.auto_general import build_auto_general_data
from api.services import export_general
from api.services.export_common import (
    REPORT_CONT, REPORT_CONT_RE_SIGNATURE, REPORT_CONT_SIGNATURE, SignatureImage, SignatureLayout, allocate_copies,
    pay_item_page_count, prepare_signature, section, stamp_draft_marker, stamp_re_signature, stamp_signature,
)
from api.services.export_general import GEN_FRONT, GEN_FRONT_PAY_ITEMS, stamp_general
from api.services.xlsx_template import WorkbookTemplate
from api.storage.client import create_signed_url, download_file, upload_file

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "report_forms.xlsx"

CONTRACT_INFO = "Contract Info"

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STORAGE_UNAVAILABLE = "Storage is temporarily unavailable, so the export couldn't be saved. Try again."


class ExportError(Exception):
    """Base class for an IDR that can't be exported."""


class IdrNotFoundError(ExportError):
    """No IDR has that id."""


class ExportDataError(ExportError):
    """The IDR's project or reports could not be loaded."""


class ExportStorageError(ExportError):
    """Storage refused or failed to store the export or to sign its download URL."""


@dataclass
class IdrExport:
    """A generated export: the file name to offer and the .xlsx bytes."""

    filename: str
    content: bytes


@dataclass
class PublishedExport:
    """A stored export: a short-lived URL that downloads it, and the file name it downloads as."""

    download_url: str
    filename: str


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


def _ac_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find the IDR's Asphaltic Concrete reports, each printed on AC Fr sheets and an AC Bk.
    Takes the IDR's reports, in page order.
    Returns the non-addendum AC reports, in page order (empty when none).
    """
    return [r for r in reports if r["report_type"] == "AC" and not r["is_addendum"]]


def _conc_mix_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find the IDR's Concrete Truck & Mix Info reports, each printed on one or more Conc Mix sheets.
    Takes the IDR's reports, in page order.
    Returns the CONC_MIX reports, addendum or not (the flag is the frontend's to set), in page order.
    """
    return [r for r in reports if r["report_type"] == "CONC_MIX"]


def _page_after_clones(page: Optional[int], extras: list[tuple[Optional[int], int]]) -> Optional[int]:
    """
    Renumber a report's page around the extra sheets reports print on (pay-item overflow fronts, Conc Mix clones): the
    database gives each report one page, and a report's extra sheets take the numbers right after its own, so every
    page after it moves down by that many.
    Takes the report's page number (None on a draft) and each report's (page number, extra sheet count).
    Returns the page number to print: the page plus the extra sheets of every report numbered before it.
    """
    if page is None:
        return None
    return page + sum(extra for start, extra in extras if start is not None and start < page)


@dataclass
class _Sheets:
    """The sheets each report prints on, allocated before anything is stamped."""

    general_fronts: list[str]
    swcbs: list[tuple[list[str], str]]  # each SWCB's (front pages, back page)
    conc_mixes: list[list[str]]
    acs: list[tuple[list[str], str]]    # each AC report's (AC Fr sheets, AC Bk)


def _allocate_sheets(workbook: WorkbookTemplate, general_data: dict[str, Any], swcbs: list[dict[str, Any]],
                     conc_mixes: list[dict[str, Any]], acs: list[dict[str, Any]]) -> _Sheets:
    """
    Provide every report its sheets before anything is stamped, cloning the blank forms as needed. Copies are numbered
    across the whole IDR per form: Gen Fr 2, ... for the General's pay-item overflow; Conc Fr 2, ... for later SWCB
    reports and pay-item overflow alike, in page order; Conc Bk 2, ... one per later SWCB; Conc Mix 2, ... for later
    CONC_MIX reports and truck overflow; AC Fr 2, ... for later AC reports and their pavement-course and pay-item
    overflow, in page order; AC Bk 2, ... one per later AC report.
    Takes the workbook, the General's report_data and the SWCB, CONC_MIX and AC reports, in page order.
    Returns the sheet names for each report.
    """
    general_fronts = allocate_copies(workbook, GEN_FRONT,
                                     pay_item_page_count(general_data.get("payItems"), GEN_FRONT_PAY_ITEMS))
    swcb_sheets, fronts_used = [], 0
    for index, report in enumerate(swcbs):
        count = pay_item_page_count(section(report, "report_data").get("payItems"), export_swcb.CONC_FRONT_PAY_ITEMS)
        fronts = allocate_copies(workbook, export_swcb.CONC_FRONT, count, fronts_used)
        swcb_sheets.append((fronts, allocate_copies(workbook, export_swcb.CONC_BACK, 1, index)[0]))
        fronts_used += count
    conc_mix_sheets, mix_used = [], 0
    for report in conc_mixes:
        conc_mix_sheets.append(export_conc_mix.allocate_sheets(workbook, report["report_data"], first_index=mix_used))
        mix_used += len(conc_mix_sheets[-1])
    ac_sheets, ac_fronts_used = [], 0
    for index, report in enumerate(acs):
        count = export_ac.front_count(report["report_data"])
        fronts = allocate_copies(workbook, export_ac.AC_FRONT, count, ac_fronts_used)
        ac_sheets.append((fronts, allocate_copies(workbook, export_ac.AC_BACK, 1, index)[0]))
        ac_fronts_used += count
    return _Sheets(general_fronts, swcb_sheets, conc_mix_sheets, ac_sheets)


def _segments(groups: list[tuple[Optional[UUID], list[str]]], conc_mixes: list[dict[str, Any]],
              conc_mix_sheets: list[list[str]]) -> list[tuple[Optional[UUID], list[str]]]:
    """
    Put the printed reports in print order: each main report, then its CONC_MIX addendums.
    Takes the main reports' groups in page order ((report id, its pages, including Report Cont when it continues
    there); the id is None for a General composed for the export), the CONC_MIX reports and each one's sheets.
    Returns (report id, its pages) for each, in order. A CONC_MIX whose parent isn't printed (a report type not
    exported yet), or that has none, comes last.
    """
    printed = {report_id for report_id, _ in groups if report_id is not None}
    children: dict[UUID, list[tuple[UUID, list[str]]]] = {}
    orphans: list[tuple[Optional[UUID], list[str]]] = []
    for report, sheets in zip(conc_mixes, conc_mix_sheets):
        parent = report.get("parent_report_id")
        segment = (report.get("report_id"), sheets)
        if parent in printed:
            children.setdefault(parent, []).append(segment)
        else:
            orphans.append(segment)
    ordered = [segment for group in groups for segment in [group] + children.get(group[0], [])]
    return ordered + orphans


def _load_attachments(reports: list[dict[str, Any]]) -> dict[UUID, list[dict[str, Any]]]:
    """
    Load the uploaded attachments of every report in the IDR, in one query (pending uploads are left out).
    Takes the IDR's reports.
    Returns {report id: its attachments, oldest upload first}; raises ExportDataError if they can't load.
    """
    report_ids = [r["report_id"] for r in reports if r.get("report_id") is not None]
    if not report_ids:
        return {}
    rows = list_uploaded_attachments_for_reports(report_ids)
    if rows is None:
        raise ExportDataError("Failed to load report attachments")
    by_report: dict[UUID, list[dict[str, Any]]] = {}
    for row in rows:
        by_report.setdefault(row["report_id"], []).append(row)
    return by_report


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


# Every page with an "Inspector's Signature" line, by the template sheet it is (or is a copy of)
SIGNATURE_LAYOUTS: dict[str, SignatureLayout] = {
    export_general.GEN_BACK: export_general.SIGNATURE_LAYOUT,
    export_swcb.CONC_BACK: export_swcb.SIGNATURE_LAYOUT,
    export_ac.AC_BACK: export_ac.SIGNATURE_LAYOUT,
    export_conc_mix.CONC_MIX: export_conc_mix.SIGNATURE_LAYOUT,
    REPORT_CONT: REPORT_CONT_SIGNATURE,
    export_attachments.ATTACHMENTS: export_attachments.SIGNATURE_LAYOUT,
}

# The same pages each have a "Reviewed by" line beside it, for the Resident Engineer's signature
RE_SIGNATURE_LAYOUTS: dict[str, SignatureLayout] = {
    export_general.GEN_BACK: export_general.RE_SIGNATURE_LAYOUT,
    export_swcb.CONC_BACK: export_swcb.RE_SIGNATURE_LAYOUT,
    export_ac.AC_BACK: export_ac.RE_SIGNATURE_LAYOUT,
    export_conc_mix.CONC_MIX: export_conc_mix.RE_SIGNATURE_LAYOUT,
    REPORT_CONT: REPORT_CONT_RE_SIGNATURE,
    export_attachments.ATTACHMENTS: export_attachments.RE_SIGNATURE_LAYOUT,
}


def _signature_layout(page: str, layouts: dict[str, SignatureLayout] = SIGNATURE_LAYOUTS) -> Optional[SignatureLayout]:
    """
    Find where a printed page takes a signature.
    Takes the sheet name, and the layouts to look in (the inspector's unless RE_SIGNATURE_LAYOUTS is given); a copy
    ("Conc Bk 2", "Attachments 7") signs where its original does.
    Returns the SignatureLayout, or None for a page without a signature line (the front pages).
    """
    return layouts.get(re.sub(r" \d+$", "", page))


def _fetch_signature(path: str, idr_id: UUID) -> Optional[SignatureImage]:
    """
    Fetch one of an IDR's signature copies from Storage and ready it for the workbook.
    Takes the copy's object path and the IDR's uuid (for the log).
    Returns the SignatureImage, or None when the file can't be fetched or read (logged; the export goes on with a
    blank signature line).
    """
    try:
        return prepare_signature(download_file(path, SIGNATURE_BUCKET_NAME))
    except Exception as exc:  # noqa: BLE001 - a missing signature mustn't stop the export
        logger.error("Signature %s unavailable for the export of IDR %s: %s", path, idr_id, exc)
        return None


def _load_signature(idr: dict[str, Any]) -> Optional[SignatureImage]:
    """
    Fetch the signature an IDR was submitted with, once for the whole export.
    Takes the IDR row.
    Returns the SignatureImage; None for a draft (a returned one included), for an IDR submitted without one, and
    when the file can't be fetched or read.
    """
    path = idr.get("inspector_signature_path")
    if idr["status"] == "draft" or not path:
        return None
    return _fetch_signature(path, idr["idr_id"])


def _load_re_signature(idr: dict[str, Any]) -> Optional[SignatureImage]:
    """
    Fetch the Resident Engineer's signature an IDR was approved with, once for the whole export.
    Takes the IDR row.
    Returns the SignatureImage; None unless the IDR is approved and carries one (so a path left on an IDR that is no
    longer approved is never printed), and when the file can't be fetched or read.
    """
    path = idr.get("re_signature_path")
    if idr["status"] != "approved" or not path:
        return None
    return _fetch_signature(path, idr["idr_id"])


def generate_idr_export(idr_id: UUID) -> IdrExport:
    """
    Build an IDR's .xlsx export from the report-forms template: the General's pages, then each SWCB report's (on Conc
    Fr / Conc Bk and clones of them) and each AC report's (AC Fr / AC Bk and clones), in page order, each followed by
    its CONC_MIX addendums' Conc Mix sheets; an SWCB's Conc Bk ticks its "See attached" box when it has one, an AC Bk
    its "Attached Pages" box when its report has attachments or continues on Report Cont. Pay items past a front
    page's table continue on copies of it, right after it. A report's extra sheets are numbered after it and counted
    in OF. Each report's attachments follow its last page, one unnumbered page each. A draft IDR's pages are each
    marked "DRAFT - Not for Submission"; from submission on they carry the inspector's signature and the date it was
    signed wherever a page has a signature line, and an approved IDR's carry the Resident Engineer's beside it.
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
    swcbs, conc_mixes, acs = _swcb_reports(reports), _conc_mix_reports(reports), _ac_reports(reports)
    general_id = next((r["report_id"] for r in reports if r["report_type"] == "GEN" and not r["is_addendum"]), None)

    workbook = WorkbookTemplate(TEMPLATE_PATH)
    sheets = _allocate_sheets(workbook, general_data, swcbs, conc_mixes, acs)
    # Each report's sheets past its first are pages of their own: they take the numbers after it and count in OF
    extras = [(page_number, len(sheets.general_fronts) - 1)]
    extras += [(r["page_number"], len(fronts) - 1) for r, (fronts, _) in zip(swcbs, sheets.swcbs)]
    extras += [(r["page_number"], len(names) - 1) for r, names in zip(conc_mixes, sheets.conc_mixes)]
    extras += [(r["page_number"], len(fronts) - 1) for r, (fronts, _) in zip(acs, sheets.acs)]
    extra_pages = sum(extra for start, extra in extras if start is not None)
    if extra_pages and idr.get("total_pages") is not None:
        idr = {**idr, "total_pages": idr["total_pages"] + extra_pages}

    _stamp_contract_info(workbook, project)
    general_pages = stamp_general(workbook, idr, project, inspector, general_data,
                                  _page_after_clones(page_number, extras), fronts=sheets.general_fronts)
    groups = [(general_id, general_pages)]
    report_cont_used = REPORT_CONT in general_pages

    # The printed main reports stamp in page order, so their groups print in that order and the first to need
    # Report Cont keeps it; later ones have their long text cut
    swcb_sheets = {id(swcb): pair for swcb, pair in zip(swcbs, sheets.swcbs)}
    ac_sheets = {id(ac): pair for ac, pair in zip(acs, sheets.acs)}
    ac_backs: list[tuple[dict[str, Any], str, list[str]]] = []  # each AC report, its AC Bk and its pages
    for report in (r for r in reports if id(r) in swcb_sheets or id(r) in ac_sheets):
        page = _page_after_clones(report["page_number"], extras)
        if id(report) in ac_sheets:
            fronts, back = ac_sheets[id(report)]
            ac_pages = export_ac.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=page, report_data=report["report_data"],
                                        report_cont_available=not report_cont_used, fronts=fronts, back=back)
            report_cont_used = report_cont_used or REPORT_CONT in ac_pages
            groups.append((report["report_id"], ac_pages))
            ac_backs.append((report, back, ac_pages))
            continue
        fronts, back = swcb_sheets[id(report)]
        swcb_pages = export_swcb.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=page, report_data=report["report_data"],
                                        report_cont_available=not report_cont_used, fronts=fronts, back=back)
        report_cont_used = report_cont_used or REPORT_CONT in swcb_pages
        groups.append((report["report_id"], swcb_pages))
        if any(r.get("parent_report_id") == report["report_id"] for r in conc_mixes):
            export_swcb.mark_conc_mix_attached(workbook, back)

    for conc_mix, names in zip(conc_mixes, sheets.conc_mixes):
        export_conc_mix.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                               page_number=_page_after_clones(conc_mix["page_number"], extras),
                               report_data=conc_mix["report_data"], sheets=names)

    # Attachments print after their report's last page; a report that isn't printed (SWR, CONC_CYL, ...) has its
    # attachments at the end, followed by the page counting photos past the cap
    segments = _segments(groups, conc_mixes, sheets.conc_mixes)
    printed = [report_id for report_id, _ in segments if report_id is not None]
    unprinted = [r["report_id"] for r in reports if r.get("report_id") is not None and r["report_id"] not in printed]
    attachments = _load_attachments(reports)
    # Each AC Bk's "Attached Pages" box: ticked when its own AC report has attachment pages or continues on Report Cont
    for report, back, ac_pages in ac_backs:
        if attachments.get(report["report_id"]) or REPORT_CONT in ac_pages:
            export_ac.mark_attachments(workbook, back)
    attachment_pages, closing = export_attachments.render(
        workbook, idr, project, inspector,
        [(report_id, attachments[report_id]) for report_id in printed + unprinted if report_id in attachments])
    pages = [page for report_id, pages in segments for page in pages + attachment_pages.get(report_id, [])]
    pages += [page for report_id in unprinted for page in attachment_pages.get(report_id, [])] + closing

    # Visible sheets print in tab order, so each page moves right after the one before it (Report Cont from near the
    # front, Conc Mix from between SWR Bk and HC Fr, attachment pages from the end); the first, Gen Fr, is the tab the
    # file opens on
    for previous, page in zip(pages, pages[1:]):
        workbook.move_sheet(page, after=previous)

    signature = _load_signature(idr)  # None for a draft: an IDR is signed from submission on
    re_signature = _load_re_signature(idr)  # None unless the IDR is approved
    re_name = None
    if re_signature is not None and idr.get("re_reviewer_uuid"):
        re_name = _inspector_name(get_user_by_id(idr["re_reviewer_uuid"]))
    for page in pages:
        workbook.fit_to_letter_page(page)
        if idr["status"] == "draft":
            stamp_draft_marker(workbook, page)
        layout = _signature_layout(page)
        if layout is not None:
            stamp_signature(workbook, page, layout, signature, idr.get("inspector_signed_at"))
            stamp_re_signature(workbook, page, _signature_layout(page, RE_SIGNATURE_LAYOUTS), re_signature, re_name,
                               idr.get("re_signed_at"))
    workbook.show_only(pages)

    return IdrExport(
        filename=f"IDR_{idr_id}_{idr['report_date'].isoformat()}.xlsx",
        content=workbook.to_bytes(),
    )


def export_storage_path(idr_id: UUID, filename: str, at: datetime) -> str:
    """
    Build the object path an export is stored at; the timestamp makes every export a new object.
    Takes the IDR uuid, the export's file name and when it was made (UTC).
    Returns "{idr_id}/{YYYYMMDD_HHMMSS}_{filename}".
    """
    return f"{idr_id}/{at:%Y%m%d_%H%M%S}_{filename}"


def publish_idr_export(idr_id: UUID, now: Optional[datetime] = None) -> PublishedExport:
    """
    Build an IDR's export, store it in the exports bucket and sign a download URL for it (EXPORT_URL_EXPIRY_SECONDS),
    so the browser fetches the file from Storage rather than through this function. Stored exports are kept: there
    is no cleanup yet.
    Takes the IDR uuid and the time to stamp the object path with (now, UTC, unless given).
    Returns a PublishedExport; raises IdrNotFoundError, ExportDataError or ExportStorageError.
    """
    export = generate_idr_export(idr_id)
    path = export_storage_path(idr_id, export.filename, now or datetime.now(timezone.utc))
    try:
        upload_file(EXPORT_BUCKET_NAME, path, export.content, XLSX_MEDIA_TYPE)
        url = create_signed_url(path, EXPORT_URL_EXPIRY_SECONDS, export.filename, bucket=EXPORT_BUCKET_NAME)
    except Exception as exc:  # noqa: BLE001 - any Storage failure is reported the same way
        logger.error("Storage could not store or sign export %s: %s", path, exc)
        raise ExportStorageError("Storage is temporarily unavailable, so the export couldn't be saved. Try again.") \
            from exc
    return PublishedExport(download_url=url, filename=export.filename)
