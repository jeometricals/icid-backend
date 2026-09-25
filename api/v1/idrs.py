from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse, Response

from api.queries.idr_reports import (
    create_idr_report,
    delete_idr_report,
    get_general_report_id,
    get_idr_report,
    list_reports_for_idr,
    save_report_data,
)
from api.queries.idrs import (
    create_idr,
    get_idr_by_id,
    get_idr_id_for_day,
    list_idrs,
    touch_idr,
    update_idr_header,
)
from api.queries.projects import get_project_by_id, is_user_on_project
from api.schemas.idr import (
    Idr,
    IdrConflict,
    IdrCreate,
    IdrHeaderUpdate,
    IdrListItem,
    IdrListResponse,
    IdrResponse,
    IdrWithReports,
    IdrWithReportsResponse,
)
from api.schemas.idr_report import (
    IdrReport,
    IdrReportConflict,
    IdrReportCreate,
    IdrReportResponse,
    ReportData,
)

router = APIRouter(prefix="/v1/idrs", tags=["IDRs"])


@router.post(
    "/",
    response_model=IdrResponse,
    status_code=201,
    responses={409: {"model": IdrConflict, "description": "An IDR already exists for this day"}},
)
def create_draft_idr(body: IdrCreate) -> Union[IdrResponse, JSONResponse]:
    """
    Create a new draft IDR for a reporter on a project for the given date.
    Takes an IdrCreate body with the project id, reporter uuid and report date.
    Returns an IdrResponse, raising 404 for an unknown project, 403 for an unassigned reporter, and 409 (with existing_idr_id) if that day's IDR exists.
    """
    if get_project_by_id(body.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not is_user_on_project(body.reporter_uuid, body.project_id):
        raise HTTPException(status_code=403, detail="Reporter is not assigned to this project")

    rows = create_idr(body.project_id, body.reporter_uuid, body.report_date)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to create IDR")

    if not rows:
        existing_idr_id = get_idr_id_for_day(body.project_id, body.reporter_uuid, body.report_date)

        if existing_idr_id is None:
            raise HTTPException(status_code=500, detail="Failed to create IDR")

        conflict = IdrConflict(
            detail="IDR already exists for this project and date",
            existing_idr_id=existing_idr_id,
        )
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return IdrResponse(
        status="success",
        message="Draft IDR created",
        data=Idr.model_validate(rows[0]),
    )


@router.get("/{idr_id}", response_model=IdrWithReportsResponse)
def get_idr(idr_id: UUID) -> IdrWithReportsResponse:
    """
    Return an IDR with its header fields and every report inside it, report_data as stored.
    Takes the IDR uuid as a path parameter.
    Returns an IdrWithReportsResponse (reports is [] when none), or raises 404 if the IDR does not exist.
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    reports = list_reports_for_idr(idr_id)

    if reports is None:
        raise HTTPException(status_code=500, detail="Failed to load IDR reports")

    return IdrWithReportsResponse(
        status="success",
        message="IDR detail",
        data=IdrWithReports.model_validate({**idr, "reports": reports}),
    )


@router.post(
    "/{idr_id}/reports",
    response_model=IdrReportResponse,
    status_code=201,
    responses={409: {"model": IdrReportConflict, "description": "IDR is not a draft, or already has a General"}},
)
def add_report(idr_id: UUID, body: IdrReportCreate) -> Union[IdrReportResponse, JSONResponse]:
    """
    Add an empty report of the given type to a draft IDR, optionally as an addendum to one of its main reports.
    Takes the IDR uuid as a path parameter and an IdrReportCreate body.
    Returns an IdrReportResponse; raises 404 (no IDR), 409 (not draft, or second General with existing_report_id) and 400 (bad parent).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    if body.parent_report_id is not None:
        if not body.is_addendum:
            raise HTTPException(status_code=400, detail="Only addendums can have a parent report")

        parent = get_idr_report(idr_id, body.parent_report_id)

        if parent is None:
            raise HTTPException(status_code=400, detail="Parent report not found in this IDR")

        if parent["is_addendum"]:
            raise HTTPException(status_code=400, detail="An addendum's parent must be a main report, not another addendum")

    rows = create_idr_report(idr_id, body.report_type.value, body.is_addendum, body.parent_report_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to add report")

    if not rows:
        existing_report_id = get_general_report_id(idr_id)

        if existing_report_id is None:
            raise HTTPException(status_code=500, detail="Failed to add report")

        conflict = IdrReportConflict(
            detail="IDR already has a General report",
            existing_report_id=existing_report_id,
        )
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    touch_idr(idr_id)

    return IdrReportResponse(
        status="success",
        message="Report added",
        data=IdrReport.model_validate(rows[0]),
    )


@router.put("/{idr_id}/reports/{report_id}", response_model=IdrReportResponse)
def save_report(
    idr_id: UUID, report_id: UUID, body: Annotated[ReportData, Body()]
) -> IdrReportResponse:
    """
    Replace a report's data with the body as-is, bumping updated_at on the report and its IDR.
    Takes the IDR and report uuids as path parameters and any JSON object as the body.
    Returns an IdrReportResponse; raises 404 (no IDR), 409 (IDR not draft) and 404 (report not in this IDR).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    rows = save_report_data(idr_id, report_id, body)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to save report")

    if not rows:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")

    return IdrReportResponse(
        status="success",
        message="Report saved",
        data=IdrReport.model_validate(rows[0]),
    )


@router.put("/{idr_id}/header", response_model=IdrResponse)
def save_header(idr_id: UUID, body: IdrHeaderUpdate) -> IdrResponse:
    """
    Update any subset of a draft IDR's header fields (null clears one) and bump its updated_at.
    Takes the IDR uuid as a path parameter and an IdrHeaderUpdate body.
    Returns an IdrResponse; raises 404 (no IDR), 409 (not draft) and 400 (temp_low above temp_high after the update).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    fields = body.model_dump(exclude_unset=True)

    temp_low = fields.get("temp_low", idr["temp_low"])
    temp_high = fields.get("temp_high", idr["temp_high"])

    if temp_low is not None and temp_high is not None and temp_low > temp_high:
        raise HTTPException(status_code=400, detail="temp_low cannot be greater than temp_high")

    rows = update_idr_header(idr_id, fields)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to save IDR header")

    if not rows:
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    return IdrResponse(
        status="success",
        message="IDR header saved",
        data=Idr.model_validate(rows[0]),
    )


@router.delete("/{idr_id}/reports/{report_id}", status_code=204, response_class=Response)
def delete_report(idr_id: UUID, report_id: UUID) -> Response:
    """
    Remove a report from a draft IDR, together with any addendums attached to it, and bump the IDR's updated_at.
    Takes the IDR and report uuids as path parameters.
    Returns an empty 204; raises 404 (no IDR), 409 (IDR not draft) and 404 (report not in this IDR).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    rows = delete_idr_report(idr_id, report_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to delete report")

    if not rows:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")

    return Response(status_code=204)


@router.get("/", response_model=IdrListResponse)
def list_project_idrs(
    project_id: Optional[str] = None,
    status: Optional[Literal["draft", "submitted"]] = None,
    reporter_uuid: Optional[UUID] = None,
) -> IdrListResponse:
    """
    List IDRs most recently edited first, each with report_count and has_general; every filter is optional.
    Takes optional project_id, status and reporter_uuid query parameters.
    Returns an IdrListResponse (empty data list when nothing matches), or raises 500 on a query failure.
    """
    rows = list_idrs(project_id, status, reporter_uuid)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to list IDRs")

    return IdrListResponse(
        status="success",
        message=f"{len(rows)} IDR(s)",
        data=[IdrListItem.model_validate(row) for row in rows],
    )
