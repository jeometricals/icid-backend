from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.queries.contract_items import list_project_contract_totals
from api.queries.quantities import list_quantities, list_quantities_with_budget
from api.schemas.quantity import Quantity, QuantityList, QuantityListResponse, QuantityRange
from api.services.auth import current_user, demo_project_fence, project_member
from api.services.budget_rollup import compute_rollup
from api.services.disciplines import disciplines_for_report_type
from api.services.quantities import totals_by_unit
from api.v1._shared import day_range, report_types_for

# The most rows one response carries; past it the response says so (truncated) and the caller narrows the filters
MAX_ROWS = 10000

# A second router under /v1/projects. Every route needs a signed-in user; a demo user reaches only their own project.
router = APIRouter(
    prefix="/v1/projects", tags=["Quantities"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)


@router.get("/{project_id}/quantities", response_model=QuantityListResponse, dependencies=[Depends(project_member)])
def list_project_quantities(
    project_id: str,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    pay_item: Optional[list[str]] = Query(None),
    budget_code: Optional[list[str]] = Query(None),
    discipline: Optional[list[str]] = Query(None),
    inspector_uuid: Optional[UUID] = None,
    cumulative: bool = False,
) -> QuantityListResponse:
    """
    List a project's quantities: the pay-item quantities of its approved IDRs, newest work date first, with their totals per unit, and what they come to against the project's contract items, per pay item, per budget code and for the project. Every filter is optional; filters of different kinds must all hold, and a repeated filter matches any of its values. The filters pick quantities; the budget is always every contract item of the project.
    Takes the project id as a path parameter and, as query parameters, from and to (work dates, inclusive), pay_item and budget_code (repeatable, matched exactly), discipline (repeatable, by name), inspector_uuid, and cumulative (true: from and to are not applied).
    Returns a QuantityListResponse: the range asked for, at most MAX_ROWS rows, their totals_by_unit, row_count, truncated when more matched, and the roll-up (pay_items, totals_by_budget_code, project_total, unbudgeted_total); raises 404 (no project), 403 (not on the project, and not an admin) and 400 (a date that isn't one, from after to, or an unknown discipline).
    """
    first, last = day_range(date_from, date_to)

    filters = {
        "date_from": None if cumulative else first,
        "date_to": None if cumulative else last,
        "pay_items": pay_item,
        "budget_codes": budget_code,
        "report_types": report_types_for(discipline),
        "reporter_uuid": inspector_uuid,
    }

    found = list_quantities(project_id, **filters, limit=MAX_ROWS + 1)

    if found is None:
        raise HTTPException(status_code=500, detail="Failed to fetch quantities")

    actuals = list_quantities_with_budget(project_id, **filters)
    contract_items = list_project_contract_totals(project_id)

    if actuals is None or contract_items is None:
        raise HTTPException(status_code=500, detail="Failed to fetch quantities")

    rows = found[:MAX_ROWS]

    return QuantityListResponse(
        status="success",
        message=f"Quantities for project {project_id}",
        data=QuantityList(
            range=QuantityRange(date_from=first, date_to=last, cumulative=cumulative),
            rows=[Quantity(**row, disciplines=list(disciplines_for_report_type(row["report_type"]))) for row in rows],
            totals_by_unit=totals_by_unit(rows),
            row_count=len(rows),
            truncated=len(found) > MAX_ROWS,
            **compute_rollup(actuals, contract_items),
        ),
    )
