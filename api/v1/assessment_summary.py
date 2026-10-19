from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.queries.assessment_summary import list_assessment_flags
from api.queries.projects import get_project_by_id
from api.schemas.assessment_summary import AssessmentSummary, AssessmentSummaryResponse
from api.services.assessment_summary import (
    XLSX,
    MonthError,
    build_tailored_workbook,
    build_workbook,
    parse_month,
    summary_filename,
    summary_months,
    summary_payload,
    summary_sites,
)
from api.services.auth import current_user, demo_project_fence, project_member
from api.v1._shared import day_range

# A third router under /v1/projects. Every route needs a signed-in user; a demo user reaches only their own project.
project_router = APIRouter(
    prefix="/v1/projects", tags=["Assessment summary"],
    dependencies=[Depends(current_user), Depends(demo_project_fence)],
)


def _project_and_flags(project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Optional[Decimal]]]:
    """
    Read what every Assessment Summary is built from.
    Takes the project id.
    Returns (the project row, its claimed flags, its two rates); raises 500 when either read fails.
    """
    project = get_project_by_id(project_id)
    flags = list_assessment_flags(project_id)
    if project is None or flags is None:
        raise HTTPException(status_code=500, detail="Failed to read the Assessment Summary's data")
    rates = {"cost_per_sf_4in": project["cost_per_sf_4in"], "cost_per_sf_7in": project["cost_per_sf_7in"]}
    return project, flags, rates


def _xlsx(content: bytes, filename: str) -> Response:
    """
    Answer with a workbook as an attachment.
    Takes the file's bytes and its name.
    Returns the response.
    """
    return Response(content=content, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@project_router.get("/{project_id}/assessment-summary", response_model=AssessmentSummaryResponse,
                    dependencies=[Depends(project_member)])
def get_assessment_sites(
    project_id: str,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
) -> AssessmentSummaryResponse:
    """
    List a project's constructed sites for the As-Builts & Assessments page: a row per property any approved IDR claimed flags on, its 4" and 7" square feet and amounts summed over the flags whose day (the Date Constructed, else the approval day) is in the range, and the totals. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter and, as query parameters, from and to (YYYY-MM-DD, inclusive, either left out).
    Returns an AssessmentSummaryResponse, the sites by address; raises 404 (no project), 403 (not on the project), 400 (a date that isn't YYYY-MM-DD, or from after to) and 500.
    """
    first, last = day_range(date_from, date_to)
    _, flags, rates = _project_and_flags(project_id)
    rows = summary_sites(flags, rates, first, last)
    data = AssessmentSummary.model_validate(summary_payload(rows, rates, first, last))
    return AssessmentSummaryResponse(status="success", message=f"{len(rows)} constructed sites", data=data)


@project_router.get("/{project_id}/assessment-summary/tailored.xlsx", response_class=Response,
                    responses={200: {"content": {XLSX: {}}}}, dependencies=[Depends(project_member)])
def get_tailored_assessment_summary(
    project_id: str,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    property_id: Optional[list[UUID]] = Query(None),
) -> Response:
    """
    Download the page's view as a workbook: one tab named for the range of days, a row per site as the page lists it (summed over the range), and the totals row. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter and, as query parameters, from and to (YYYY-MM-DD, inclusive, either left out) and property_id, repeated, for the sites the page shows (left out: every site).
    Returns the workbook as an attachment; raises 404 (no project), 403 (not on the project), 400 (a date that isn't YYYY-MM-DD, or from after to), 422 (a property_id that isn't a uuid) and 500.
    """
    first, last = day_range(date_from, date_to)
    project, flags, rates = _project_and_flags(project_id)
    rows = summary_sites(flags, rates, first, last, set(property_id) if property_id is not None else None)
    return _xlsx(build_tailored_workbook(project, rows, first, last), summary_filename(project_id, tailored=True))


@project_router.get("/{project_id}/assessment-summary.xlsx", response_class=Response,
                    responses={200: {"content": {XLSX: {}}}}, dependencies=[Depends(project_member)])
def get_assessment_summary(
    project_id: str,
    month_from: Optional[str] = Query(None, alias="from"),
    month_to: Optional[str] = Query(None, alias="to"),
) -> Response:
    """
    Download a project's Assessment Summary (the accumulative report): an .xlsx with one tab per month of construction that has any, named 'Nov 2026', a row per property with its 4" and 7" assessable, non-assessable and total square feet, their amounts at the DOT rates and the grand total, and a totals row. A project (or a range) with nothing is a workbook with one 'No activity' tab. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter and, as query parameters, from and to (YYYY-MM, inclusive, either left out).
    Returns the workbook as an attachment; raises 404 (no project), 403 (not on the project), 400 (a month that isn't YYYY-MM, or from after to) and 500.
    """
    try:
        first, last = parse_month(month_from, "from"), parse_month(month_to, "to")
    except MonthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if first and last and first > last:
        raise HTTPException(status_code=400, detail="from after to")

    project, flags, rates = _project_and_flags(project_id)
    months = summary_months(flags, rates, first, last)
    return _xlsx(build_workbook(project, months, filtered=bool(first or last)), summary_filename(project_id))
