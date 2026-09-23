from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException

from api.queries.completed_forms import get_completed_form, upsert_completed_form
from api.queries.projects import get_project_by_id, is_user_on_project
from api.queries.reports import (
    create_report,
    get_report_by_id,
    list_reports,
    submit_report,
    touch_report,
)
from api.queries.users import get_user_by_id
from api.schemas.general_form import GeneralFormData
from api.schemas.report import (
    GeneralFormSaved,
    GeneralFormSaveResponse,
    Report,
    ReportCreate,
    ReportListItem,
    ReportListResponse,
    ReportResponse,
    ReportWithGeneral,
    ReportWithGeneralResponse,
)

router = APIRouter(prefix="/v1/reports", tags=["Reports"])

GENERAL_TEMPLATE_ID = "GENERAL"


@router.post("/", response_model=ReportResponse, status_code=201)
def create_draft_report(body: ReportCreate) -> ReportResponse:
    """
    Create a new draft report for a project, dated today unless the body says otherwise.
    Takes a ReportCreate body with the project id, reporter uuid and optional report date.
    Returns a ReportResponse, raising 404 if the project or reporter is unknown and 403 if the reporter is not assigned to the project.
    """
    if get_project_by_id(body.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if get_user_by_id(str(body.reporter_uuid)) is None:
        raise HTTPException(status_code=404, detail="Reporter not found")

    if not is_user_on_project(body.reporter_uuid, body.project_id):
        raise HTTPException(status_code=403, detail="Reporter is not assigned to this project")

    row = create_report(body.project_id, body.reporter_uuid, body.report_date or date.today())

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create report")

    return ReportResponse(
        status="success",
        message="Draft report created",
        data=Report.model_validate(row),
    )


@router.get("/", response_model=ReportListResponse)
def list_project_reports(
    project_id: str,
    reporter_uuid: Optional[UUID] = None,
    status: Optional[Literal["draft", "submitted"]] = None,
) -> ReportListResponse:
    """
    List a project's reports, most recently edited first, with a General Form description preview.
    Takes project_id (required) and optional reporter_uuid and status query parameters.
    Returns a ReportListResponse (empty data list when nothing matches), or raises 500 on a query failure.
    """
    rows = list_reports(project_id, GENERAL_TEMPLATE_ID, reporter_uuid, status)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to list reports")

    return ReportListResponse(
        status="success",
        message=f"{len(rows)} report(s) for project {project_id}",
        data=[ReportListItem.model_validate(row) for row in rows],
    )


@router.put("/{report_id}/general", response_model=GeneralFormSaveResponse)
def save_general_form(report_id: UUID, body: GeneralFormData) -> GeneralFormSaveResponse:
    """
    Save the General Form data for a draft report, creating or replacing it.
    Takes the report uuid as a path parameter and the form as a GeneralFormData body.
    Returns a GeneralFormSaveResponse, or raises 404 if the report does not exist and 409 if it is not a draft.
    """
    report = get_report_by_id(report_id)

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    if report["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft reports can be edited")

    saved = upsert_completed_form(report_id, GENERAL_TEMPLATE_ID, body.model_dump(by_alias=True))

    if saved is None:
        raise HTTPException(status_code=500, detail="Failed to save General Form")

    touch_report(report_id)

    return GeneralFormSaveResponse(
        status="success",
        message="General Form saved",
        data=GeneralFormSaved(
            report_id=report_id,
            completed_form_id=saved["completed_form_id"],
            saved_at=saved["updated_at"],
        ),
    )


@router.post("/{report_id}/submit", response_model=ReportResponse)
def submit_draft_report(report_id: UUID) -> ReportResponse:
    """
    Submit a draft report, locking it from further edits and stamping submitted_at.
    Takes the report uuid as a path parameter; no body.
    Returns a ReportResponse, or raises 404 if the report does not exist and 409 if it is not a draft or its General Form was never saved.
    """
    report = get_report_by_id(report_id)

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    if report["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft reports can be submitted")

    if get_completed_form(report_id, GENERAL_TEMPLATE_ID) is None:
        raise HTTPException(status_code=409, detail="Save the General Form before submitting")

    row = submit_report(report_id)

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to submit report")

    return ReportResponse(
        status="success",
        message="Report submitted",
        data=Report.model_validate(row),
    )


@router.get("/{report_id}", response_model=ReportWithGeneralResponse)
def get_report(report_id: UUID) -> ReportWithGeneralResponse:
    """
    Return a report together with its saved General Form data, if any.
    Takes the report uuid as a path parameter.
    Returns a ReportWithGeneralResponse, or raises 404 if the report does not exist.
    """
    report = get_report_by_id(report_id)

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    form = get_completed_form(report_id, GENERAL_TEMPLATE_ID)

    return ReportWithGeneralResponse(
        status="success",
        message="Report detail",
        data=ReportWithGeneral.model_validate(
            {**report, "general_form": form["form_data"] if form else None}
        ),
    )
