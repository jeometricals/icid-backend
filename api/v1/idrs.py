from typing import Union
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.queries.idr_reports import list_reports_for_idr
from api.queries.idrs import create_idr, get_idr_by_id, get_idr_id_for_day
from api.queries.projects import get_project_by_id, is_user_on_project
from api.schemas.idr import (
    Idr,
    IdrConflict,
    IdrCreate,
    IdrResponse,
    IdrWithReports,
    IdrWithReportsResponse,
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
