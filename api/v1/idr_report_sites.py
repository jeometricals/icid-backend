from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from api.queries.idr_report_sites import (
    add_report_site,
    delete_report_site,
    find_idr_site,
    list_report_sites,
    site_pay_item_keys,
    update_report_site,
)
from api.queries.idr_reports import get_idr_report, list_reports_for_idr
from api.schemas.auth import UserOut
from api.schemas.idr_report import label_for
from api.schemas.idr_report_site import (
    ReportSiteCreate,
    ReportSiteListResponse,
    ReportSiteOut,
    ReportSiteResponse,
    ReportSiteUpdate,
)
from api.services.auth import current_user, demo_idr_fence
from api.v1.idr_flags import attribution_editor, load_idr

# A third router under /v1/idrs, for the sites a report imports (Slice U). A demo user reaches only their own IDRs.
router = APIRouter(prefix="/v1/idrs", tags=["IDR report sites"], dependencies=[Depends(current_user), Depends(demo_idr_fence)])

# The reports a site can be imported into (the UI offers it on SWCB for the pilot); a DSP takes none
SITE_REPORT_TYPES = ("GEN", "SWCB", "AC", "CONC_MIX", "CONC_CYL")
NOT_HERE = "Sites are imported into a General, SWCB, AC, Conc Mix or Conc Cyl report"
NOT_ON_PROJECT = "That site isn't a live site of this IDR's project"
SITE_NOT_FOUND = "Site not found on this report"


def report_of(idr_id: UUID, report_id: UUID) -> dict[str, Any]:
    """
    Fetch the report a sites route acts on, only if it is the IDR's.
    Takes the IDR's and the report's uuids.
    Returns the report dict; raises 404 when the IDR has no such report.
    """
    report = get_idr_report(idr_id, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")
    return report


def report_name(report: dict[str, Any]) -> str:
    """
    Name a report for a message: its type's words and its page.
    Takes a report dict (report_type, page_number, is_auto_generated).
    Returns the name, as "Sidewalk, Curb, Concrete Base (page 2)".
    """
    name = label_for(report["report_type"]) + (" (auto-generated)" if report.get("is_auto_generated") else "")
    return f"{name} (page {report['page_number']})" if report.get("page_number") else name


def _hand_entered_conflict(idr_id: UUID, property_id: UUID) -> str | None:
    """
    Say why a site can't be imported when one of the IDR's reports holds by hand a pay item the site's flags or curb segments would fill (so approving would count it twice).
    Takes the IDR's and the property's uuids.
    Returns the message naming the first such item and its report, or None when there is none; raises 500 when they can't be read.
    """
    keys = site_pay_item_keys(property_id)
    reports = list_reports_for_idr(idr_id)
    if keys is None or reports is None:
        raise HTTPException(status_code=500, detail="Failed to read the site's pay items")
    filled = {(row["item_no"].strip(), (row["budget_code"] or "").strip()) for row in keys}
    for report in reports:
        if report.get("is_auto_generated"):
            continue
        items = (report.get("report_data") or {}).get("payItems")
        for item in items if isinstance(items, list) else []:
            key = (str(item.get("itemNo") or "").strip(), str(item.get("budgetCode") or "").strip()) if isinstance(item, dict) else None
            if key in filled:
                return (f"Pay item {key[0]} ({key[1]}) is entered by hand on {report_name(report)}, and this site's PIR "
                        "flags or curb segments fill it: remove it from that report first, or it would be counted twice")
    return None


def _site_response(row: dict[str, Any], message: str) -> ReportSiteResponse:
    """
    Wrap one site in the response envelope.
    Takes the site dict and the message.
    Returns the ReportSiteResponse.
    """
    return ReportSiteResponse(status="success", message=message, data=ReportSiteOut.model_validate(row))


@router.get("/{idr_id}/reports/{report_id}/sites", response_model=ReportSiteListResponse)
def list_sites(idr_id: UUID, report_id: UUID) -> ReportSiteListResponse:
    """
    List the sites a report imported, oldest first, each with its property and work. For anyone who can read the IDR.
    Takes the IDR's and the report's uuids as path parameters.
    Returns a ReportSiteListResponse; raises 404 for an unknown IDR or a report that isn't its, and 500 if they can't be read.
    """
    load_idr(idr_id)
    report_of(idr_id, report_id)
    rows = list_report_sites(report_id)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch the report's sites")
    return ReportSiteListResponse(status="success", message="Sites retrieved",
                                  data=[ReportSiteOut.model_validate(row) for row in rows])


@router.post("/{idr_id}/reports/{report_id}/sites", response_model=ReportSiteResponse, status_code=201,
             responses={409: {"description": "The site is already in a report of this IDR, or a hand-entered pay item clashes"}})
def import_site(
    report_id: UUID, body: ReportSiteCreate, idr: dict = Depends(attribution_editor), user: UserOut = Depends(current_user)
) -> ReportSiteResponse | JSONResponse:
    """
    Import a site into a report, with the work done there: its live flags and curb segments become the IDR's, claimed when it is approved. Once per IDR. For the IDR's inspector while it is a draft, the reviewer who accepted it while in review, or an admin; never a demo user, and not once it is approved.
    Takes the report's uuid as a path parameter (the IDR's through attribution_editor), the property and work in the body, the IDR (loaded and checked by attribution_editor) and the user.
    Returns 201 with a ReportSiteResponse; raises 400 for a report that takes no sites (a DSP) or a site that isn't a live one of the project, 403 or 409 as attribution_editor does, 404 for a report that isn't the IDR's, 409 with existing_report_id when the IDR already imported the site, 409 when a report of the IDR holds by hand a pay item the site's flags or segments fill, and 422 for 'other' work without its text.
    """
    report = report_of(idr["idr_id"], report_id)
    if report["report_type"] not in SITE_REPORT_TYPES:
        raise HTTPException(status_code=400, detail=NOT_HERE)
    existing = find_idr_site(idr["idr_id"], body.property_id)
    if existing is not None:
        return JSONResponse(status_code=409, content={
            "detail": f"Already in {report_name(existing)} on this IDR", "existing_report_id": str(existing["report_id"])})
    conflict = _hand_entered_conflict(idr["idr_id"], body.property_id)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    row = add_report_site(report_id, idr["idr_id"], body.property_id, body.work_type, body.work_type_other, user.uuid)
    if row is None:
        existing = find_idr_site(idr["idr_id"], body.property_id)
        if existing is not None:  # imported by someone else meanwhile
            return JSONResponse(status_code=409, content={
                "detail": f"Already in {report_name(existing)} on this IDR", "existing_report_id": str(existing["report_id"])})
        raise HTTPException(status_code=400, detail=NOT_ON_PROJECT)
    return _site_response(row, "Site imported")


@router.patch("/{idr_id}/reports/{report_id}/sites/{site_id}", response_model=ReportSiteResponse)
def update_site(
    report_id: UUID, site_id: UUID, body: ReportSiteUpdate, idr: dict = Depends(attribution_editor),
    user: UserOut = Depends(current_user),
) -> ReportSiteResponse:
    """
    Change the work done at an imported site (its type, and its text for 'other'); a real change records who and when (updated_by, updated_at). Who may: as for importing.
    Takes the report's and the site's uuids as path parameters, the work in the body, the IDR (loaded and checked by attribution_editor) and the user.
    Returns a ReportSiteResponse; raises 403 or 409 as attribution_editor does, 404 for a report or site that isn't the IDR's and 422 for 'other' work without its text.
    """
    report_of(idr["idr_id"], report_id)
    row = update_report_site(report_id, site_id, body.work_type, body.work_type_other, user.uuid)
    if row is None:
        raise HTTPException(status_code=404, detail=SITE_NOT_FOUND)
    return _site_response(row, "Site updated")


@router.delete("/{idr_id}/reports/{report_id}/sites/{site_id}", status_code=204, response_class=Response)
def remove_site(report_id: UUID, site_id: UUID, idr: dict = Depends(attribution_editor)) -> Response:
    """
    Remove a site from a report: its flags and curb segments leave the IDR. Who may: as for importing.
    Takes the report's and the site's uuids as path parameters and the IDR (loaded and checked by attribution_editor).
    Returns 204; raises 403 or 409 as attribution_editor does, 404 for a report or site that isn't the IDR's and 500 if it can't be removed.
    """
    report_of(idr["idr_id"], report_id)
    rows = delete_report_site(report_id, site_id)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to remove the site")
    if not rows:
        raise HTTPException(status_code=404, detail=SITE_NOT_FOUND)
    return Response(status_code=204)
