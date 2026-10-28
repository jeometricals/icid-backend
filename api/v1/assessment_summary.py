from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.queries.assessment_summary import list_assessment_flags
from api.queries.projects import get_project_by_id, list_project_roles
from api.queries.task_orders import get_task_order
from api.schemas.assessment_summary import AssessmentSummary, AssessmentSummaryResponse
from api.schemas.auth import UserOut
from api.services.assessment_summary import (
    CSV,
    XLSX,
    MonthError,
    SummaryFooter,
    SummaryRow,
    build_tailored_workbook,
    build_workbook,
    every_month,
    flags_on_properties,
    monthly_csv,
    parse_month,
    person_name,
    project_engineers,
    project_total_label,
    sites_csv,
    summary_filename,
    summary_months,
    summary_payload,
    summary_project_total,
    summary_sites,
)
from api.services.auth import current_user, demo_project_fence, project_member
from api.services.export_common import signed_date
from api.v1._shared import day_range

# A third router under /v1/projects. Every route needs a signed-in user; a demo user reaches only their own project.
project_router = APIRouter(
    prefix="/v1/projects", tags=["Assessment summary"],
    dependencies=[Depends(current_user), Depends(demo_project_fence)],
)

TASK_ORDER_NOT_FOUND = "Task order not found"


def _project_and_flags(project_id: str, task_order_id: Optional[UUID] = None) -> tuple[
        dict[str, Any], list[dict[str, Any]], dict[str, Optional[Decimal]]]:
    """
    Read what every Assessment Summary is built from, narrowed to a task order's properties when one is given (ML1b).
    Takes the project id and the task order's id (None: every property).
    Returns (the project row, its claimed flags, its two rates); raises 404 for a task order that isn't a live one of the project, and 500 when a read fails.
    """
    project = get_project_by_id(project_id)
    flags = list_assessment_flags(project_id)
    if project is None or flags is None:
        raise HTTPException(status_code=500, detail="Failed to read the Assessment Summary's data")
    if task_order_id is not None:
        order = get_task_order(task_order_id)
        if order is None or order["project_id"] != project_id or order["deleted_at"] is not None:
            raise HTTPException(status_code=404, detail=TASK_ORDER_NOT_FOUND)
        flags = flags_on_properties(flags, {UUID(str(found["id"])) for found in order.get("properties") or []})
    rates = {"cost_per_sf_4in": project["cost_per_sf_4in"], "cost_per_sf_7in": project["cost_per_sf_7in"]}
    return project, flags, rates


def _footer(project_id: str, user: UserOut) -> SummaryFooter:
    """
    A download's footer (ML4b): the signed-in user as compiler, today in New York, and the project's REs.
    Takes the project id and the user.
    Returns the footer; raises 500 when the roles can't be read.
    """
    roles = list_project_roles(project_id)
    if roles is None:
        raise HTTPException(status_code=500, detail="Failed to read the project's roles")
    return SummaryFooter(person_name(user.model_dump()), signed_date(datetime.now(timezone.utc)),
                         project_engineers(roles))


def _file(content: bytes, media_type: str, filename: str) -> Response:
    """
    Answer with a download as an attachment.
    Takes the file's bytes, its media type and its name.
    Returns the response.
    """
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _month_range(month_from: Optional[str], month_to: Optional[str]) -> tuple[Optional[date], Optional[date]]:
    """
    Read the monthly downloads' range.
    Takes from and to as sent (YYYY-MM, either None).
    Returns (first, last), each a month's first day or None; raises 400 for a month that isn't YYYY-MM, or from after to.
    """
    try:
        first, last = parse_month(month_from, "from"), parse_month(month_to, "to")
    except MonthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if first and last and first > last:
        raise HTTPException(status_code=400, detail="from after to")
    return first, last


def _site_rows(project_id: str, date_from: Optional[str], date_to: Optional[str], property_id: Optional[list[UUID]],
               task_order_id: Optional[UUID]) -> tuple[dict[str, Any], list[SummaryRow], Optional[date], Optional[date]]:
    """
    The tailored downloads' rows: a row per site over a range of days, narrowed to the sites asked for and a task order's.
    Takes the project id and the query parameters as sent.
    Returns (the project row, the rows, first, last); raises 400, 404 and 500 as the routes say.
    """
    first, last = day_range(date_from, date_to)
    project, flags, rates = _project_and_flags(project_id, task_order_id)
    rows = summary_sites(flags, rates, first, last, set(property_id) if property_id is not None else None)
    return project, rows, first, last


@project_router.get("/{project_id}/assessment-summary", response_model=AssessmentSummaryResponse,
                    dependencies=[Depends(project_member)])
def get_assessment_sites(
    project_id: str,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    task_order_id: Optional[UUID] = Query(None),
) -> AssessmentSummaryResponse:
    """
    List a project's constructed sites for the As-Builts & Assessments page: a row per property any approved IDR claimed flags on, its 4" and 7" square feet and amounts summed over the flags whose day (the Date Constructed, else the approval day) is in the range, and the totals. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter and, as query parameters, from and to (YYYY-MM-DD, inclusive, either left out) and task_order_id (its bundled properties only; ML1b).
    Returns an AssessmentSummaryResponse, the sites by address; raises 404 (no project, or no such task order), 403 (not on the project), 400 (a date that isn't YYYY-MM-DD, or from after to) and 500.
    """
    first, last = day_range(date_from, date_to)
    _, flags, rates = _project_and_flags(project_id, task_order_id)
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
    task_order_id: Optional[UUID] = Query(None),
    user: UserOut = Depends(current_user),
) -> Response:
    """
    Download the page's view as a workbook: one tab named for the range of days, a row per site as the page lists it (summed over the range), the totals row and the footer (ML4b). For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter, as query parameters from and to (YYYY-MM-DD, inclusive, either left out), property_id, repeated, for the sites the page shows (left out: every site) and task_order_id (its bundled properties only; ML1b), and the user.
    Returns the workbook as an attachment; raises 404 (no project, or no such task order), 403 (not on the project), 400 (a date that isn't YYYY-MM-DD, or from after to), 422 (an id that isn't a uuid) and 500.
    """
    project, rows, first, last = _site_rows(project_id, date_from, date_to, property_id, task_order_id)
    workbook = build_tailored_workbook(project, rows, first, last, _footer(project_id, user))
    return _file(workbook, XLSX, summary_filename(project_id, tailored=True))


@project_router.get("/{project_id}/assessment-summary/tailored.csv", response_class=Response,
                    responses={200: {"content": {"text/csv": {}}}}, dependencies=[Depends(project_member)])
def get_tailored_assessment_csv(
    project_id: str,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    property_id: Optional[list[UUID]] = Query(None),
    task_order_id: Optional[UUID] = Query(None),
) -> Response:
    """
    Download the page's view as a CSV (ML1c): the tailored workbook's rows, a row per site, its thirteen columns and Remeasure Pending; no totals or footer. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter and, as query parameters, the tailored workbook's from, to, property_id and task_order_id.
    Returns the CSV as an attachment; raises as the tailored workbook does.
    """
    _, rows, _, _ = _site_rows(project_id, date_from, date_to, property_id, task_order_id)
    return _file(sites_csv(rows), CSV, summary_filename(project_id, tailored=True, extension="csv"))


def _monthly(project_id: str, month_from: Optional[str], month_to: Optional[str], task_order_id: Optional[UUID]) -> tuple[
        dict[str, Any], dict[date, list[SummaryRow]], list[SummaryRow], Optional[date], Optional[date]]:
    """
    The monthly downloads' rows: every month of the range (ML1a) and the Project Total's, narrowed to a task order's properties.
    Takes the project id and the query parameters as sent.
    Returns (the project row, the months, the Project Total's rows, first, last); raises 400, 404 and 500 as the routes say.
    """
    first, last = _month_range(month_from, month_to)
    project, flags, rates = _project_and_flags(project_id, task_order_id)
    months = every_month(summary_months(flags, rates, first, last), first, last)
    return project, months, summary_project_total(flags, rates, first, last), first, last


@project_router.get("/{project_id}/assessment-summary.xlsx", response_class=Response,
                    responses={200: {"content": {XLSX: {}}}}, dependencies=[Depends(project_member)])
def get_assessment_summary(
    project_id: str,
    month_from: Optional[str] = Query(None, alias="from"),
    month_to: Optional[str] = Query(None, alias="to"),
    task_order_id: Optional[UUID] = Query(None),
    user: UserOut = Depends(current_user),
) -> Response:
    """
    Download a project's Assessment Summary (the accumulative report): an .xlsx with a tab for every month of the range (ML1a: from the month asked for, else the first with rows, to the month asked for, else the last with rows; a month without rows an empty tab), named 'Nov 2026', a row per property with its 4" and 7" assessable, non-assessable and total square feet, their amounts at the DOT rates and the grand total, and a totals row; then the Project Total tab. Every tab ends with the footer (ML4b). A project with nothing and no range is a workbook with one 'No activity' tab. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter, as query parameters from and to (YYYY-MM, inclusive, either left out) and task_order_id (its bundled properties only; ML1b), and the user.
    Returns the workbook as an attachment; raises 404 (no project, or no such task order), 403 (not on the project), 400 (a month that isn't YYYY-MM, or from after to), 422 (a task_order_id that isn't a uuid) and 500.
    """
    project, months, totals, first, last = _monthly(project_id, month_from, month_to, task_order_id)
    workbook = build_workbook(project, months, bool(first or last), totals, project_total_label(first, last),
                              _footer(project_id, user))
    return _file(workbook, XLSX, summary_filename(project_id))


@project_router.get("/{project_id}/assessment-summary.csv", response_class=Response,
                    responses={200: {"content": {"text/csv": {}}}}, dependencies=[Depends(project_member)])
def get_assessment_summary_csv(
    project_id: str,
    month_from: Optional[str] = Query(None, alias="from"),
    month_to: Optional[str] = Query(None, alias="to"),
    task_order_id: Optional[UUID] = Query(None),
) -> Response:
    """
    Download the accumulative report as one CSV (ML1c): a row per month and property, led by its Month (YYYY-MM), the workbook's thirteen columns and Remeasure Pending; no totals, Project Total or footer. For a user on the project, or an admin; a demo user reads their own project's.
    Takes the project id as a path parameter and, as query parameters, the workbook's from, to and task_order_id.
    Returns the CSV as an attachment; raises as the workbook does.
    """
    _, months, _, _, _ = _monthly(project_id, month_from, month_to, task_order_id)
    return _file(monthly_csv(months), CSV, summary_filename(project_id, extension="csv"))
