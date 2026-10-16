from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.queries.assessment_summary import list_assessment_flags
from api.queries.projects import get_project_by_id
from api.services.assessment_summary import (
    XLSX,
    MonthError,
    build_workbook,
    parse_month,
    summary_filename,
    summary_months,
)
from api.services.auth import current_user, demo_project_fence, project_member

# A third router under /v1/projects. Every route needs a signed-in user; a demo user reaches only their own project.
project_router = APIRouter(
    prefix="/v1/projects", tags=["Assessment summary"],
    dependencies=[Depends(current_user), Depends(demo_project_fence)],
)


@project_router.get("/{project_id}/assessment-summary.xlsx", response_class=Response,
                    responses={200: {"content": {XLSX: {}}}}, dependencies=[Depends(project_member)])
def get_assessment_summary(
    project_id: str,
    month_from: Optional[str] = Query(None, alias="from"),
    month_to: Optional[str] = Query(None, alias="to"),
) -> Response:
    """
    Download a project's Assessment Summary: an .xlsx with one tab per month of construction that has any, a row per property with its 4" and 7" assessable, non-assessable and total square feet, their amounts at the DOT rates and the grand total, and a totals row. A project (or a range) with nothing is a workbook with one 'No activity' tab. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter and, as query parameters, from and to (YYYY-MM, inclusive, either left out).
    Returns the workbook as an attachment; raises 404 (no project), 403 (not on the project), 400 (a month that isn't YYYY-MM, or from after to) and 500.
    """
    try:
        first, last = parse_month(month_from, "from"), parse_month(month_to, "to")
    except MonthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if first and last and first > last:
        raise HTTPException(status_code=400, detail="from after to")

    project = get_project_by_id(project_id)
    flags = list_assessment_flags(project_id)
    if project is None or flags is None:
        raise HTTPException(status_code=500, detail="Failed to read the Assessment Summary's data")

    months = summary_months(flags, {"cost_per_sf_4in": project["cost_per_sf_4in"],
                                    "cost_per_sf_7in": project["cost_per_sf_7in"]}, first, last)
    content = build_workbook(project, months, filtered=bool(first or last))
    return Response(content=content, media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{summary_filename(project_id)}"'})
