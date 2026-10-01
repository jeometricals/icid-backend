"""
Exports a submitted IDR as an .xlsx file built on the DDC report-forms template.

This module loads the IDR's data and assembles the workbook; each report's pages are stamped by its own module
(export_general for the General). Pages that hold nothing stay hidden, so the file prints only the IDR's pages.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from api.queries.idr_reports import get_general_report, list_non_general_main_reports
from api.queries.idrs import get_idr_by_id
from api.queries.projects import get_project_by_id, get_project_contractor_name
from api.queries.users import get_user_by_id
from api.schemas.idr_report import ADDENDUM_TYPES
from api.services.auto_general import build_auto_general_data
from api.services.export_general import GEN_BACK, REPORT_CONT, stamp_general
from api.services.xlsx_template import WorkbookTemplate

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "report_forms.xlsx"

CONTRACT_INFO = "Contract Info"


class ExportError(Exception):
    """Base class for an IDR that can't be exported."""


class IdrNotFoundError(ExportError):
    """No IDR has that id."""


class IdrNotSubmittedError(ExportError):
    """The IDR is still a draft; only submitted IDRs are exported."""


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
    Build a submitted IDR's .xlsx export from the report-forms template.
    Takes the IDR uuid.
    Returns an IdrExport (file name and bytes); raises IdrNotFoundError, IdrNotSubmittedError or ExportDataError.
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        raise IdrNotFoundError("IDR not found")
    if idr["status"] != "submitted":
        raise IdrNotSubmittedError("Only submitted IDRs can be exported")

    project = get_project_by_id(idr["project_id"])
    if project is None:
        raise ExportDataError("Failed to load the IDR's project")
    project = {**project, "contractor": get_project_contractor_name(idr["project_id"])}
    inspector = _inspector_name(get_user_by_id(idr["reporter_uuid"]))
    general_data, page_number = _general_for_export(idr_id)

    workbook = WorkbookTemplate(TEMPLATE_PATH)
    _stamp_contract_info(workbook, project)
    pages = stamp_general(workbook, idr, project, inspector, general_data, page_number)

    # The template keeps Report Cont near the front; as the General's continuation it prints after Gen Bk
    workbook.move_sheet(REPORT_CONT, after=GEN_BACK)
    for page in pages:
        workbook.fit_to_letter_page(page)
    workbook.show_only(pages)

    return IdrExport(
        filename=f"IDR_{idr_id}_{idr['report_date'].isoformat()}.xlsx",
        content=workbook.to_bytes(),
    )
