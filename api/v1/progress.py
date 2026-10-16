from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.queries.contract_items import list_flag_pay_items
from api.queries.progress import list_flag_progress
from api.queries.properties import get_property
from api.queries.task_orders import get_task_order
from api.schemas.progress import (
    ProgressRollup,
    ProgressRollupResponse,
    ProgressScope,
    TaskOrderProgressRollup,
    TaskOrderProgressRollupResponse,
)
from api.services.auth import current_user, demo_project_fence, project_member, task_order_member
from api.services.pay_item_units import FLAG_UNITS
from api.services.progress_rollup import compute_progress

# The project's rollup sits under /v1/projects; a demo user reaches only their own project
project_router = APIRouter(
    prefix="/v1/projects", tags=["Progress"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)
# A task order's sits under /v1/task-orders
router = APIRouter(prefix="/v1/task-orders", tags=["Progress"], dependencies=[Depends(current_user)])


def _bundled_ids(task_order: dict[str, Any]) -> list[UUID]:
    """
    List the ids of a task order's live bundled properties.
    Takes the task order dict, with properties (as jsonb hands them back).
    Returns their uuids, in the task order's order.
    """
    return [UUID(str(prop["id"])) for prop in task_order["properties"]]


def _filter_ids(project_id: str, task_order_id: Optional[UUID], property_id: Optional[UUID]) -> Optional[list[UUID]]:
    """
    Work out which properties a project's rollup covers from its filters: a live task order of the project, and a live property of it; both together cover the property only if it is in the task order.
    Takes the project id and the two filters (None: not applied).
    Returns the property uuids (None for the whole project); raises 404 for a filter that isn't a live one of the project.
    """
    ids = None
    if task_order_id is not None:
        task_order = get_task_order(task_order_id)
        if task_order is None or task_order["project_id"] != project_id or task_order["deleted_at"] is not None:
            raise HTTPException(status_code=404, detail="Task order not found")
        ids = _bundled_ids(task_order)
    if property_id is not None:
        found = get_property(property_id)
        if found is None or found["project_id"] != project_id or found["deleted_at"] is not None:
            raise HTTPException(status_code=404, detail="Property not found")
        ids = [property_id] if ids is None else [i for i in ids if i == property_id]
    return ids


def _read(project_id: str, property_ids: Optional[list[UUID]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Read what a rollup is made of: the project's pay items a flag can fill, and its flags in scope by property, pay item and state.
    Takes the project id and the properties covered (None: all).
    Returns (pay items, flag rows); raises 500 when either can't be read.
    """
    pay_items = list_flag_pay_items(project_id, FLAG_UNITS)
    rows = list_flag_progress(project_id, property_ids)
    if pay_items is None or rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch progress")
    return pay_items, rows


@project_router.get(
    "/{project_id}/progress-rollup", response_model=ProgressRollupResponse, dependencies=[Depends(project_member)]
)
def get_project_progress(
    project_id: str, task_order_id: Optional[UUID] = None, property_id: Optional[UUID] = None,
    include_empty: bool = False,
) -> ProgressRollupResponse:
    """
    Roll a project's flags up: per pay item a flag can fill, what is approved, in flight and unclaimed, by value, with the unmapped flags per property and the approved share overall.
    Takes the project id and, as query parameters, task_order_id and property_id (both optional, narrowing to a live task order's properties and to one live property) and include_empty (true: list every pay item a flag can fill, not only those flags in scope are on).
    Returns a ProgressRollupResponse; raises 404 (no project, or a filter that isn't a live one of it), 403 (not on the project, and not an admin) and 500.
    """
    ids = _filter_ids(project_id, task_order_id, property_id)
    pay_items, rows = _read(project_id, ids)
    scope = ProgressScope(project_id=project_id, task_order_id=task_order_id, property_id=property_id)
    return ProgressRollupResponse(
        status="success",
        message=f"Progress for project {project_id}",
        data=ProgressRollup(scope=scope, **compute_progress(pay_items, rows, include_empty=include_empty)),
    )


@router.get("/{task_order_id}/progress-rollup", response_model=TaskOrderProgressRollupResponse)
def get_task_order_progress(
    include_empty: bool = False, found: dict = Depends(task_order_member)
) -> TaskOrderProgressRollupResponse:
    """
    Roll a task order's flags up as a project's are, over its live bundled properties (a deleted task order's bundle as it stands), with each property's own rollup and how many have started.
    Takes include_empty as a query parameter (true: list every pay item a flag can fill, in each rollup) and the task order (task_order_member: one the user may read).
    Returns a TaskOrderProgressRollupResponse; raises 404 (no task order), 403 (not on its project, and not an admin) and 500.
    """
    pay_items, rows = _read(found["project_id"], _bundled_ids(found))
    scope = ProgressScope(project_id=found["project_id"], task_order_id=found["id"])
    return TaskOrderProgressRollupResponse(
        status="success",
        message=f"Progress for task order {found['task_order_number']}",
        data=TaskOrderProgressRollup(scope=scope, **compute_progress(pay_items, rows, found["properties"], include_empty)),
    )
