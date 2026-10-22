"""
Exports a submitted IDR as an .xlsx file built on the DDC report-forms template.

This module loads the IDR's data and assembles the workbook; each report's pages are stamped by its own module
(export_general for the General, export_swcb for a Sidewalk, Curb, Concrete Base report, export_ac for an Asphaltic
Concrete report, export_conc_mix for a Concrete Truck & Mix Info report, export_conc_cyl for a Concrete Cylinder
Data report, export_dsp for a Daily Site Patrol). Pages that hold nothing stay hidden, so the file prints only the IDR's pages. A draft
IDR exports too, with "DRAFT - Not for Submission" across the top of every page it prints. From submission on, an
IDR's pages carry the inspector's signature (the copy the IDR kept at submit), wherever a page has a signature line.
An approved IDR's carry the Resident Engineer's signature beside it (the copy kept at final approval), captioned with
the approver's name. The inspector's is dated with the IDR's work date; the RE's caption carries the day the RE approved.
Each report's imported sites bring their sheets (a sidewalk's PIR, a ped ramp corner's inspection; export_site_sheets)
after its attachments.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from api.core.config import EXPORT_BUCKET_NAME, EXPORT_URL_EXPIRY_SECONDS
from api.queries.idr_report_site_pay_items import idr_site_pay_items
from api.queries.idr_reports import get_general_report, list_reports_for_idr
from api.queries.idrs import get_idr_by_id
from api.queries.projects import get_project_by_id, get_project_contractor_name
from api.queries.report_attachments import list_uploaded_attachments_for_reports
from api.queries.users import get_user_by_id
from api.services import (
    export_ac, export_attachments, export_conc_cyl, export_conc_mix, export_dsp, export_site_sheets, export_swcb,
)
from api.services.idr_flags import EXPORT_HEADING, IdrFlagError, export_lines
from api.services.idr_segments import IdrSegmentError, export_segment_lines
from api.services.pay_item_sources import site_export_lines
from api.services import export_general
from api.services.export_common import (
    REPORT_CONT, REPORT_CONT_RE_SIGNATURE, REPORT_CONT_SIGNATURE, SignatureImage, SignatureLayout, allocate_copies,
    pay_item_page_count, prepare_signature, section, stamp_draft_marker, stamp_re_signature, stamp_signature,
    stamp_signature_date,
)
from api.services.export_general import GEN_FRONT, GEN_FRONT_PAY_ITEMS, SEE_INDIVIDUAL_REPORTS, stamp_general
from api.services.export_redlines import Redlines
from api.services.field_edits import field_edits_for
from api.services.xlsx_template import WorkbookTemplate
from api.services.signatures import fetch_signature_file
from api.storage.client import create_signed_url, upload_file

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


class NothingToExportError(ExportError):
    """The IDR has no page to print: no General, no report the export prints and no attachment."""


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


@dataclass
class _General:
    """The General the export prints: its report_data, its page number, the uuid reviewers' edits name it by (None for
    an auto-General, which can't be edited) and whether it is auto-generated."""

    data: dict[str, Any]
    page_number: Optional[int]
    edited_as: Optional[UUID]
    is_auto: bool


def _general_for_export(idr_id: UUID) -> Optional[_General]:
    """
    Find the General to print: the IDR's own main General, as it is; an auto-General as a summary cover, its pay items
    (a "See individual reports" row in their place), MPT / safety checklist, work force and equipment left blank; or
    none, when the IDR has no General (nothing is composed for the export).
    Takes the IDR uuid.
    Returns the _General, or None.
    """
    general = get_general_report(idr_id)
    if general is None:
        return None
    data = general["report_data"] or {}
    if general.get("is_auto_generated"):
        return _General({"description": data.get("description"), "payItems": [{"description": SEE_INDIVIDUAL_REPORTS}]},
                        general["page_number"], None, True)
    return _General(data, general["page_number"], general.get("report_id"), False)


def _flag_lines(idr: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Lay out an IDR's flag rollup, then its curb segments' (idr_segments.export_segment_lines), as pay items under one
    "PIR Flags" heading row (see idr_flags.export_lines): live before approval, as frozen at approval after. They carry
    no id, so no redline touches them.
    Takes the IDR row.
    Returns the rows (empty when there are none); raises ExportDataError when the IDR's flags or segments can't be read.
    """
    try:
        flag_lines, segment_lines = export_lines(idr), export_segment_lines(idr)
    except (IdrFlagError, IdrSegmentError) as exc:
        raise ExportDataError(str(exc)) from exc
    if not flag_lines and not segment_lines:
        return []
    return (flag_lines or [{"description": EXPORT_HEADING}]) + segment_lines


def _with_lines(report_data: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Add rows to a report's pay items, after its own.
    Takes the report_data and the rows.
    Returns a copy of the report_data with the rows added (the same dict when there are none).
    """
    if not lines:
        return report_data
    own = report_data.get("payItems")
    return {**report_data, "payItems": (own if isinstance(own, list) else []) + lines}


def _with_flag_lines_on_a_report(reports: list[dict[str, Any]], lines: list[dict[str, Any]],
                                 idr_id: UUID) -> list[dict[str, Any]]:
    """
    Print an IDR's "PIR Flags" lines when it has no General: after the pay items (and "Imported Sites" lines) of its
    first SWCB in page order, else of its first AC report; with neither they don't print, and that is logged.
    Takes the IDR's reports in page order, the lines (_flag_lines) and the IDR's uuid.
    Returns the reports, the one taking the lines as a copy with them added.
    """
    if not lines:
        return reports
    for report_type in ("SWCB", "AC"):
        index = next((i for i, r in enumerate(reports)
                      if r["report_type"] == report_type and not r.get("is_addendum")), None)
        if index is not None:
            report = reports[index]
            report = {**report, "report_data": _with_lines(report.get("report_data") or {}, lines)}
            return reports[:index] + [report] + reports[index + 1:]
    skipped = [line for line in lines if "payQuantity" in line]
    logger.warning("IDR %s has PIR Flags lines but no General, SWCB or AC report to print them on: %d pay item "
                   "row(s) not printed (%s)", idr_id, len(skipped),
                   ", ".join(f"{line.get('itemNo')} {line.get('payQuantity')} {line.get('unit')}" for line in skipped))
    return reports


def _with_site_lines(reports: list[dict[str, Any]], idr_id: UUID) -> list[dict[str, Any]]:
    """
    Add the pay items entered on each SWCB's imported sites to its pay items, after its own, under an "Imported Sites"
    heading row (pay_item_sources.site_export_lines), summed per pay item. They carry no id, so no redline touches them.
    Takes the IDR's reports and its uuid.
    Returns the reports, an SWCB with site pay items getting a copy of its report_data with the lines added; raises
    ExportDataError when the site pay items can't be read.
    """
    if not any(r["report_type"] == "SWCB" for r in reports):
        return reports
    site_items = idr_site_pay_items(idr_id)
    if site_items is None:
        raise ExportDataError("Failed to load the IDR's site pay items")
    out = []
    for report in reports:
        lines = site_export_lines(site_items, report["report_id"]) if report["report_type"] == "SWCB" else []
        if lines:
            data = report.get("report_data") or {}
            own = data.get("payItems")
            report = {**report, "report_data": {**data, "payItems": (own if isinstance(own, list) else []) + lines}}
        out.append(report)
    return out


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


def _conc_cyl_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find the IDR's Concrete Cylinder Data reports, each printed on a Conc Cyl sheet of its own.
    Takes the IDR's reports, in page order.
    Returns the CONC_CYL reports, addendum or not (the flag is the frontend's to set), in page order.
    """
    return [r for r in reports if r["report_type"] == "CONC_CYL"]


def _dsp_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find the IDR's Daily Site Patrol reports, each printed on a DSP Fr sheet of its own.
    Takes the IDR's reports, in page order.
    Returns the DSP reports, addendum or not, in page order.
    """
    return [r for r in reports if r["report_type"] == "DSP"]


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
    conc_cyls: list[list[str]]          # each CONC_CYL report's one Conc Cyl sheet
    dsps: list[list[str]]               # each DSP report's one DSP Fr sheet


def _load_redlines(idr_id: UUID) -> Redlines:
    """
    Load the edits reviewers made on the IDR, for the redlines its pages print.
    Takes the IDR uuid.
    Returns the IDR's Redlines (holding the header's; each report takes its own with for_report); raises
    ExportDataError if the edits can't load.
    """
    edits = field_edits_for(idr_id)
    if edits is None:
        raise ExportDataError("Failed to load the IDR's edits")
    return Redlines(edits)


def _allocate_sheets(workbook: WorkbookTemplate, general_data: Optional[dict[str, Any]],
                     swcbs: list[dict[str, Any]], conc_mixes: list[dict[str, Any]], acs: list[dict[str, Any]],
                     conc_cyls: list[dict[str, Any]], dsps: list[dict[str, Any]], general_redlines: Redlines,
                     redlines: Redlines) -> _Sheets:
    """
    Provide every report its sheets before anything is stamped, cloning the blank forms as needed. Copies are numbered
    across the whole IDR per form: Gen Fr 2, ... for the General's pay-item overflow; Conc Fr 2, ... for later SWCB
    reports and pay-item overflow alike, in page order; Conc Bk 2, ... one per later SWCB; Conc Mix 2, ... for later
    CONC_MIX reports and truck overflow; AC Fr 2, ... for later AC reports and their pavement-course and pay-item
    overflow, in page order; AC Bk 2, ... one per later AC report; Conc Cyl 2, ... one per later CONC_CYL report; DSP
    Fr 2, ... one per later DSP report. A pay item a reviewer revised takes a row per revision, so redlines can add
    overflow pages.
    Takes the workbook, the General's report_data (None when no General prints), the SWCB, CONC_MIX, AC, CONC_CYL and
    DSP reports, in page order, the General's redlines and the IDR's (each report's are taken from them).
    Returns the sheet names for each report (no General fronts without a General).
    """
    general_fronts = [] if general_data is None else allocate_copies(workbook, GEN_FRONT, pay_item_page_count(
        general_data.get("payItems"), GEN_FRONT_PAY_ITEMS, general_redlines))
    swcb_sheets, fronts_used = [], 0
    for index, report in enumerate(swcbs):
        count = pay_item_page_count(section(report, "report_data").get("payItems"), export_swcb.CONC_FRONT_PAY_ITEMS,
                                    redlines.for_report(report.get("report_id")))
        fronts = allocate_copies(workbook, export_swcb.CONC_FRONT, count, fronts_used)
        swcb_sheets.append((fronts, allocate_copies(workbook, export_swcb.CONC_BACK, 1, index)[0]))
        fronts_used += count
    conc_mix_sheets, mix_used = [], 0
    for report in conc_mixes:
        conc_mix_sheets.append(export_conc_mix.allocate_sheets(workbook, report["report_data"], first_index=mix_used))
        mix_used += len(conc_mix_sheets[-1])
    ac_sheets, ac_fronts_used = [], 0
    for index, report in enumerate(acs):
        count = export_ac.front_count(report["report_data"], redlines.for_report(report.get("report_id")))
        fronts = allocate_copies(workbook, export_ac.AC_FRONT, count, ac_fronts_used)
        ac_sheets.append((fronts, allocate_copies(workbook, export_ac.AC_BACK, 1, index)[0]))
        ac_fronts_used += count
    conc_cyl_sheets = [export_conc_cyl.allocate_sheets(workbook, first_index=index) for index in range(len(conc_cyls))]
    dsp_sheets = [export_dsp.allocate_sheets(workbook, first_index=index) for index in range(len(dsps))]
    return _Sheets(general_fronts, swcb_sheets, conc_mix_sheets, ac_sheets, conc_cyl_sheets, dsp_sheets)


def _segments(groups: list[tuple[Optional[UUID], list[str]]], addendums: list[dict[str, Any]],
              addendum_sheets: list[list[str]]) -> list[tuple[Optional[UUID], list[str]]]:
    """
    Put the printed reports in print order: each main report, then its CONC_MIX, CONC_CYL and DSP addendums.
    Takes the main reports' groups in page order ((report id, its pages, including Report Cont when it continues
    there); the id is None for a General composed for the export), the CONC_MIX and CONC_CYL reports and the DSP
    reports that have a parent, and each one's sheets.
    Returns (report id, its pages) for each, in order. An addendum whose parent isn't printed (a report type not
    exported yet), or that has none, comes last.
    """
    printed = {report_id for report_id, _ in groups if report_id is not None}
    children: dict[UUID, list[tuple[UUID, list[str]]]] = {}
    orphans: list[tuple[Optional[UUID], list[str]]] = []
    for report, sheets in zip(addendums, addendum_sheets):
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


# Every page with an "Inspector's Signature" line, by the template sheet it is (or is a copy of). DSP Fr has none.
SIGNATURE_LAYOUTS: dict[str, SignatureLayout] = {
    export_general.GEN_BACK: export_general.SIGNATURE_LAYOUT,
    export_swcb.CONC_BACK: export_swcb.SIGNATURE_LAYOUT,
    export_ac.AC_BACK: export_ac.SIGNATURE_LAYOUT,
    export_conc_mix.CONC_MIX: export_conc_mix.SIGNATURE_LAYOUT,
    export_conc_cyl.SHEET_NAME: export_conc_cyl.SIGNATURE_LAYOUT,
    REPORT_CONT: REPORT_CONT_SIGNATURE,
    export_attachments.ATTACHMENTS: export_attachments.SIGNATURE_LAYOUT,
}

# The same pages each have a "Reviewed by" line beside it, for the Resident Engineer's signature; so has DSP Fr, alone,
# its layout naming the page's Date cell
RE_SIGNATURE_LAYOUTS: dict[str, SignatureLayout] = {
    export_general.GEN_BACK: export_general.RE_SIGNATURE_LAYOUT,
    export_swcb.CONC_BACK: export_swcb.RE_SIGNATURE_LAYOUT,
    export_ac.AC_BACK: export_ac.RE_SIGNATURE_LAYOUT,
    export_conc_mix.CONC_MIX: export_conc_mix.RE_SIGNATURE_LAYOUT,
    export_conc_cyl.SHEET_NAME: export_conc_cyl.RE_SIGNATURE_LAYOUT,
    export_dsp.SHEET_NAME: export_dsp.RE_SIGNATURE_LAYOUT,
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
        return prepare_signature(fetch_signature_file(path))
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
    Build an IDR's .xlsx export from the report-forms template: the General's pages (when it has one), then each
    SWCB report's (on Conc Fr / Conc Bk and clones of them) and each AC report's (AC Fr / AC Bk and clones), in page order, each followed by
    its CONC_MIX addendums' Conc Mix sheets and its CONC_CYL addendums' Conc Cyl sheets; each Daily Site Patrol on a
    DSP Fr sheet, among the main reports in page order (after its parent when it has one); an SWCB's Conc Bk ticks its "See attached" box when it has one, an AC Bk
    its "Attached Pages" box when its report has attachments or continues on Report Cont. Pay items past a front
    page's table continue on copies of it, right after it. A report's extra sheets are numbered after it and counted
    in OF. Each report's attachments follow its last page, one unnumbered page each, then its imported sites' sheets
    the same way (each live page of a sidewalk's PIR; a ped ramp corner's approved DOT inspection form, else ICID's
    inspection report of the corner, once per export; see export_site_sheets). A draft IDR's pages are each
    marked "DRAFT - Not for Submission"; from submission on they carry the inspector's signature wherever a page has
    a signature line, and an approved IDR's carry the Resident Engineer's beside it (the inspector's dated with the work date, the RE's with the approval date).
    What reviewers edited prints with its redlines, at any status: the replaced value struck, the reviewer's in blue
    with their initials (see export_redlines); a revised pay item takes a row per revision, which can add pages.
    The IDR's flag rollup prints with the General's pay items, under a "PIR Flags" heading row; an IDR with no
    General prints no General pages (Slice X.3) and puts that rollup on its first SWCB's, else its first AC's, pay
    items.
    An auto-General prints as a summary cover (see _general_for_export).
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
    general = _general_for_export(idr_id)
    flag_lines = _flag_lines(idr)
    reports = _with_site_lines(_load_reports(idr_id), idr_id)
    if general is not None:
        general.data = _with_lines(general.data, flag_lines)
    else:
        reports = _with_flag_lines_on_a_report(reports, flag_lines, idr_id)
    swcbs, conc_mixes, acs = _swcb_reports(reports), _conc_mix_reports(reports), _ac_reports(reports)
    conc_cyls, dsps = _conc_cyl_reports(reports), _dsp_reports(reports)
    general_id = next((r["report_id"] for r in reports if r["report_type"] == "GEN" and not r["is_addendum"]), None)
    redlines = _load_redlines(idr_id)
    # The header's only, unless it is the inspector's own
    general_redlines = redlines.for_report(general.edited_as if general is not None else None)

    workbook = WorkbookTemplate(TEMPLATE_PATH)
    sheets = _allocate_sheets(workbook, general.data if general is not None else None, swcbs, conc_mixes, acs,
                              conc_cyls, dsps, general_redlines, redlines)
    # Each report's sheets past its first are pages of their own: they take the numbers after it and count in OF
    extras = [(general.page_number, len(sheets.general_fronts) - 1)] if general is not None else []
    extras += [(r["page_number"], len(fronts) - 1) for r, (fronts, _) in zip(swcbs, sheets.swcbs)]
    extras += [(r["page_number"], len(names) - 1) for r, names in zip(conc_mixes, sheets.conc_mixes)]
    extras += [(r["page_number"], len(fronts) - 1) for r, (fronts, _) in zip(acs, sheets.acs)]
    extra_pages = sum(extra for start, extra in extras if start is not None)
    if extra_pages and idr.get("total_pages") is not None:
        idr = {**idr, "total_pages": idr["total_pages"] + extra_pages}

    _stamp_contract_info(workbook, project)
    groups: list[tuple[Optional[UUID], list[str]]] = []
    report_cont_used = False
    if general is not None:
        general_pages = stamp_general(workbook, idr, project, inspector, general.data,
                                      _page_after_clones(general.page_number, extras), fronts=sheets.general_fronts,
                                      redlines=general_redlines, summary_only=general.is_auto)
        groups.append((general_id, general_pages))
        report_cont_used = REPORT_CONT in general_pages

    # A DSP prints among the main reports, in page order; one with a parent follows it, as an addendum does
    dsp_pages: dict[int, list[str]] = {}
    for dsp, names in zip(dsps, sheets.dsps):
        dsp_pages[id(dsp)] = export_dsp.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                               page_number=_page_after_clones(dsp["page_number"], extras),
                                               report_data=dsp["report_data"], sheets=names)
    dsp_addendums = [dsp for dsp in dsps if dsp.get("parent_report_id") is not None]

    # The printed main reports stamp in page order, so their groups print in that order and the first to need
    # Report Cont keeps it; later ones have their long text cut
    swcb_sheets = {id(swcb): pair for swcb, pair in zip(swcbs, sheets.swcbs)}
    ac_sheets = {id(ac): pair for ac, pair in zip(acs, sheets.acs)}
    ac_backs: list[tuple[dict[str, Any], str, list[str]]] = []  # each AC report, its AC Bk and its pages
    main_dsps = {id(dsp) for dsp in dsps if dsp.get("parent_report_id") is None}
    for report in (r for r in reports if id(r) in swcb_sheets or id(r) in ac_sheets or id(r) in main_dsps):
        page = _page_after_clones(report["page_number"], extras)
        if id(report) in main_dsps:
            groups.append((report["report_id"], dsp_pages[id(report)]))
            continue
        if id(report) in ac_sheets:
            fronts, back = ac_sheets[id(report)]
            ac_pages = export_ac.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=page, report_data=report["report_data"],
                                        report_cont_available=not report_cont_used, fronts=fronts, back=back,
                                        redlines=redlines.for_report(report["report_id"]))
            report_cont_used = report_cont_used or REPORT_CONT in ac_pages
            groups.append((report["report_id"], ac_pages))
            ac_backs.append((report, back, ac_pages))
            continue
        fronts, back = swcb_sheets[id(report)]
        swcb_pages = export_swcb.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                                        page_number=page, report_data=report["report_data"],
                                        report_cont_available=not report_cont_used, fronts=fronts, back=back,
                                        redlines=redlines.for_report(report["report_id"]))
        report_cont_used = report_cont_used or REPORT_CONT in swcb_pages
        groups.append((report["report_id"], swcb_pages))
        if any(r.get("parent_report_id") == report["report_id"] for r in conc_mixes):
            export_swcb.mark_conc_mix_attached(workbook, back)

    for conc_mix, names in zip(conc_mixes, sheets.conc_mixes):
        export_conc_mix.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                               page_number=_page_after_clones(conc_mix["page_number"], extras),
                               report_data=conc_mix["report_data"], sheets=names,
                               redlines=redlines.for_report(conc_mix.get("report_id")))

    for conc_cyl, names in zip(conc_cyls, sheets.conc_cyls):
        export_conc_cyl.render(workbook, idr, project, project.get("contractor"), inspector=inspector,
                               page_number=_page_after_clones(conc_cyl["page_number"], extras),
                               report_data=conc_cyl["report_data"], sheets=names,
                               redlines=redlines.for_report(conc_cyl.get("report_id")))

    # Attachments, then the imported sites' sheets, print after their report's last page; a report that isn't printed
    # (SWR, HC, ...) has them at the end, followed by the page counting photos past the cap
    segments = _segments(groups, conc_mixes + conc_cyls + dsp_addendums,
                         sheets.conc_mixes + sheets.conc_cyls + [dsp_pages[id(dsp)] for dsp in dsp_addendums])
    printed = [report_id for report_id, _ in segments if report_id is not None]
    unprinted = [r["report_id"] for r in reports if r.get("report_id") is not None and r["report_id"] not in printed]
    attachments = _load_attachments(reports)
    site_pages = export_site_sheets.site_pages(idr_id, printed + unprinted)
    # Each AC Bk's "Attached Pages" box: ticked when its own AC report has attachment or site pages or continues on
    # Report Cont
    for report, back, ac_pages in ac_backs:
        if attachments.get(report["report_id"]) or site_pages.get(report["report_id"]) or REPORT_CONT in ac_pages:
            export_ac.mark_attachments(workbook, back)
    attachment_pages, closing = export_attachments.render(
        workbook, idr, project, inspector,
        [(report_id, attachments.get(report_id, [])) for report_id in printed + unprinted
         if report_id in attachments or report_id in site_pages], site_pages)
    pages = [page for report_id, pages in segments for page in pages + attachment_pages.get(report_id, [])]
    pages += [page for report_id in unprinted for page in attachment_pages.get(report_id, [])] + closing
    if not pages:
        raise NothingToExportError("This IDR has nothing to print yet: add a report first.")

    # Visible sheets print in tab order, so each page moves right after the one before it (Report Cont from near the
    # front, Conc Mix from between SWR Bk and HC Fr, attachment pages from the end); the first (Gen Fr, when a General
    # prints) is the tab the file opens on
    for previous, page in zip(pages, pages[1:]):
        workbook.move_sheet(page, after=previous)

    signature = _load_signature(idr)  # None for a draft: an IDR is signed from submission on
    re_signature = _load_re_signature(idr)  # None unless the IDR is approved
    re_name = None
    if re_signature is not None and idr.get("re_reviewer_uuid"):
        re_name = _inspector_name(get_user_by_id(idr["re_reviewer_uuid"]))
    # The page's one Date cell goes with the latest signature printed: the RE's on an approved IDR, else the
    # inspector's. A draft has neither, and a signature that couldn't be printed brings no date. A page with only the
    # RE's line (DSP Fr) never prints the inspector's, so its Date is the RE's day or blank.
    re_signed_at = idr.get("re_signed_at") if re_signature is not None else None
    if re_signed_at:
        signed_at = re_signed_at
    elif signature is not None:
        signed_at = idr.get("inspector_signed_at")
    else:
        signed_at = None
    for page in pages:
        workbook.fit_to_letter_page(page)
        if idr["status"] == "draft":
            stamp_draft_marker(workbook, page)
        layout = _signature_layout(page)
        re_layout = _signature_layout(page, RE_SIGNATURE_LAYOUTS)
        if layout is not None:
            stamp_signature(workbook, page, layout, signature)
        if re_layout is not None:
            stamp_re_signature(workbook, page, re_layout, re_signature, re_name)
        if layout is not None:
            stamp_signature_date(workbook, page, layout, signed_at)
        elif re_layout is not None and re_layout.date_cell is not None:
            stamp_signature_date(workbook, page, re_layout, re_signed_at)
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
