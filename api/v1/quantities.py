from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.queries.quantities import list_quantities
from api.schemas.quantity import Quantity, QuantityList, QuantityListResponse
from api.services.auth import current_user, demo_project_fence, project_member
from api.services.disciplines import disciplines_for_report_type, report_types_for_discipline
from api.services.quantities import totals_by_unit

# The most rows one response carries; past it the response says so (truncated) and the caller narrows the filters
MAX_ROWS = 10000

# A second router under /v1/projects. Every route needs a signed-in user; a demo user reaches only their own project.
router = APIRouter(
    prefix="/v1/projects", tags=["Quantities"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)


def _day(value: Optional[str], name: str) -> Optional[date]:
    """
    Read a date filter.
    Takes the query parameter's text (None when it wasn't sent) and its name, for the message.
    Returns the date, or None when it wasn't sent; raises 400 for text that isn't a YYYY-MM-DD date.
    """
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be a date, YYYY-MM-DD") from exc


def _report_types(disciplines: Optional[list[str]]) -> Optional[list[str]]:
    """
    Resolve a discipline filter to the report types that cover any of the disciplines.
    Takes the disciplines sent (None when the filter wasn't).
    Returns the report types, each once, or None without a filter; raises 400 for a discipline nothing covers.
    """
    if not disciplines:
        return None
    report_types: list[str] = []
    for discipline in disciplines:
        covered = report_types_for_discipline(discipline)
        if not covered:
            raise HTTPException(status_code=400, detail=f"Unknown discipline: {discipline}")
        report_types += [report_type for report_type in covered if report_type not in report_types]
    return report_types


@router.get("/{project_id}/quantities", response_model=QuantityListResponse, dependencies=[Depends(project_member)])
def list_project_quantities(
    project_id: str,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    pay_item: Optional[list[str]] = Query(None),
    budget_code: Optional[list[str]] = Query(None),
    discipline: Optional[list[str]] = Query(None),
    inspector_uuid: Optional[UUID] = None,
) -> QuantityListResponse:
    """
    List a project's quantities: the pay-item quantities of its approved IDRs, newest work date first, with their totals per unit. Every filter is optional; filters of different kinds must all hold, and a repeated filter matches any of its values.
    Takes the project id as a path parameter and, as query parameters, from and to (work dates, inclusive), pay_item and budget_code (repeatable, matched exactly), discipline (repeatable, by name) and inspector_uuid.
    Returns a QuantityListResponse: at most MAX_ROWS rows, their totals_by_unit, row_count, and truncated when more matched; raises 404 (no project), 403 (not on the project, and not an admin) and 400 (a date that isn't one, from after to, or an unknown discipline).
    """
    first, last = _day(date_from, "from"), _day(date_to, "to")

    if first is not None and last is not None and first > last:
        raise HTTPException(status_code=400, detail="from cannot be after to")

    found = list_quantities(
        project_id,
        date_from=first,
        date_to=last,
        pay_items=pay_item,
        budget_codes=budget_code,
        report_types=_report_types(discipline),
        reporter_uuid=inspector_uuid,
        limit=MAX_ROWS + 1,
    )

    if found is None:
        raise HTTPException(status_code=500, detail="Failed to fetch quantities")

    rows = found[:MAX_ROWS]

    return QuantityListResponse(
        status="success",
        message=f"Quantities for project {project_id}",
        data=QuantityList(
            rows=[Quantity(**row, disciplines=list(disciplines_for_report_type(row["report_type"]))) for row in rows],
            totals_by_unit=totals_by_unit(rows),
            row_count=len(rows),
            truncated=len(found) > MAX_ROWS,
        ),
    )
