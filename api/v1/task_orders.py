from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from api.queries.properties import get_property
from api.queries.task_orders import (
    bundle_property,
    create_task_order,
    get_task_order,
    get_task_order_by_number,
    is_property_bundled,
    list_task_orders,
    soft_delete_task_order,
    unbundle_property,
    undelete_task_order,
    update_task_order,
)
from api.schemas.auth import UserOut
from api.schemas.task_order import (
    TaskOrderConflict,
    TaskOrderCreate,
    TaskOrderDetail,
    TaskOrderListItem,
    TaskOrderListResponse,
    TaskOrderResponse,
    TaskOrderUpdate,
)
from api.services.auth import (
    current_admin,
    current_user,
    demo_project_fence,
    no_demo_task_order_writes,
    project_member,
    task_order_member,
)
from api.v1._shared import clean_text

# A second router under /v1/projects, for a project's task orders. A demo user reaches only their own project.
project_router = APIRouter(
    prefix="/v1/projects", tags=["Task orders"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)
# One task order by its id, and its bundle
router = APIRouter(prefix="/v1/task-orders", tags=["Task orders"], dependencies=[Depends(current_user)])

# The longest value each text field takes
MAX_NUMBER_LENGTH = 50
MAX_NOTES_LENGTH = 2000
CONFLICT = {409: {"model": TaskOrderConflict}}


def _clean_fields(body: TaskOrderCreate | TaskOrderUpdate) -> dict[str, Any]:
    """
    Read a task order's fields off the request: the number trimmed and refused when blank or too long, the notes trimmed. A null number is left out; issued_date and estimate_notes are kept when sent, None (or blank notes) clearing them.
    Takes the request body.
    Returns the values keyed by column, for the fields sent; raises 400 for a value that doesn't fit.
    """
    fields: dict[str, Any] = {}
    if body.task_order_number is not None:
        fields["task_order_number"] = clean_text(body.task_order_number, "task_order_number", MAX_NUMBER_LENGTH)
    if "issued_date" in body.model_fields_set:
        fields["issued_date"] = body.issued_date
    if "estimate_notes" in body.model_fields_set:
        fields["estimate_notes"] = clean_text(body.estimate_notes, "estimate_notes", MAX_NOTES_LENGTH, required=False)
    return fields


def _conflict(holder: dict[str, Any], detail: str) -> JSONResponse:
    """
    Build the 409 for a number another live task order of the project has.
    Takes that task order's dict and the reason.
    Returns the response, naming it.
    """
    conflict = TaskOrderConflict(detail=detail, existing_task_order_id=holder["id"])
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


def _number_conflict(project_id: str, task_order_number: str, task_order_id: Optional[UUID] = None) -> JSONResponse:
    """
    Build the 409 for a create or an edit that found its number taken.
    Takes the project id, the number and the id of the task order being saved (None for a new one).
    Returns the response; raises 500 when no other live task order has the number (the statement failed for another reason).
    """
    holder = get_task_order_by_number(project_id, task_order_number)
    if holder is None or holder["id"] == task_order_id:
        raise HTTPException(status_code=500, detail="Failed to save task order")
    return _conflict(holder, "A task order with this number already exists in this project")


def _load_task_order(task_order_id: UUID) -> dict[str, Any]:
    """
    Fetch a task order, with its live bundled properties, for an admin route.
    Takes the task order's id.
    Returns the task order dict; raises 404 when there is none.
    """
    found = get_task_order(task_order_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Task order not found")
    return found


def _response(row: dict[str, Any], message: str) -> TaskOrderResponse:
    """
    Wrap one task order, with its properties, in the response envelope.
    Takes the task order dict and the message.
    Returns the TaskOrderResponse.
    """
    return TaskOrderResponse(status="success", message=message, data=TaskOrderDetail.model_validate(row))


def _writable_task_order(
    found: dict = Depends(task_order_member), user: UserOut = Depends(no_demo_task_order_writes)
) -> dict:
    """
    FastAPI dependency for the writes under one task order: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the task order (loaded and checked by task_order_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the task order dict, with its live bundled properties; raises 403 for a demo user.
    """
    return found


def _refuse_deleted_for_member(task_order: dict[str, Any], user: UserOut) -> None:
    """
    Keep a soft-deleted task order for its admins: a member who isn't one can't change it.
    Takes the task order dict and the signed-in user.
    Returns nothing; raises 400 for a deleted task order and a user who isn't an admin.
    """
    if task_order["deleted_at"] is not None and user.role != "admin":
        raise HTTPException(status_code=400, detail="Task order is deleted")


def _bundle_refusal(task_order: dict[str, Any], found: Optional[dict[str, Any]]) -> Optional[HTTPException]:
    """
    Say why a property can't go into a task order, if it can't.
    Takes the task order dict and the property dict (None when it doesn't exist).
    Returns the HTTPException to raise (404 for a missing property, 400 for a deleted side or another project's property), or None when it can go in.
    """
    if found is None:
        return HTTPException(status_code=404, detail="Property not found")
    if task_order["deleted_at"] is not None:
        return HTTPException(status_code=400, detail="Cannot bundle into a deleted task order")
    if found["deleted_at"] is not None:
        return HTTPException(status_code=400, detail="Cannot bundle a deleted property")
    if found["project_id"] != task_order["project_id"]:
        return HTTPException(status_code=400, detail="Property belongs to a different project")
    return None


@project_router.get("/{project_id}/task-orders", response_model=TaskOrderListResponse)
def list_project_task_orders(
    project_id: str, include_deleted: bool = False, user: UserOut = Depends(project_member)
) -> TaskOrderListResponse:
    """
    List a project's task orders, by number, each with how many of its bundled properties aren't deleted. For a user on the project, or an admin; only an admin gets soft-deleted ones, and only when asked (include_deleted from anyone else is ignored).
    Takes the project id as a path parameter, the include_deleted flag and the signed-in user.
    Returns a TaskOrderListResponse; raises 403 for a user who isn't on the project, 404 for an unknown project and 500 if the list can't be read.
    """
    rows = list_task_orders(project_id, include_deleted and user.role == "admin")

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch task orders")

    return TaskOrderListResponse(
        status="success", message="Task orders retrieved", data=[TaskOrderListItem.model_validate(row) for row in rows]
    )


@project_router.post(
    "/{project_id}/task-orders",
    response_model=TaskOrderResponse,
    status_code=201,
    responses=CONFLICT,
    dependencies=[Depends(no_demo_task_order_writes)],
)
def create_project_task_order(
    project_id: str, body: TaskOrderCreate, user: UserOut = Depends(project_member)
) -> TaskOrderResponse | JSONResponse:
    """
    Add a task order to a project, created by the signed-in user, with no properties yet. For a user on the project, in any role, or an admin; not a demo user.
    Takes the project id as a path parameter, the number and the optional issued date and estimate notes in the body (text trimmed; blank notes are none) and the user.
    Returns 201 with a TaskOrderResponse; raises 400 for a blank or over-long value, 403 for a demo user or a user who isn't on the project, 404 for an unknown project, 422 for a malformed date and 409 (with existing_task_order_id) when a live task order of the project has the number.
    """
    fields = _clean_fields(body)
    row = create_task_order(project_id, user.uuid, fields)

    if row is None:
        return _number_conflict(project_id, fields["task_order_number"])

    return _response(row, "Task order created")


@router.get("/{task_order_id}", response_model=TaskOrderResponse)
def get_task_order_record(found: dict = Depends(task_order_member)) -> TaskOrderResponse:
    """
    Fetch one task order, soft-deleted or not, with its bundled properties that aren't deleted. For a user on its project, or an admin.
    Takes the task order (loaded and checked by task_order_member from the task_order_id path parameter).
    Returns a TaskOrderResponse; raises 403 for a user who isn't on its project and 404 for an unknown task order.
    """
    return _response(found, "Task order retrieved")


@router.patch("/{task_order_id}", response_model=TaskOrderResponse, responses=CONFLICT)
def update_task_order_record(
    task_order_id: UUID,
    body: TaskOrderUpdate,
    found: dict = Depends(_writable_task_order),
    user: UserOut = Depends(current_user),
) -> TaskOrderResponse | JSONResponse:
    """
    Change some of a task order. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted one. A number left out or sent as null keeps its value; a null issued date, or null or blank notes, clear them.
    Takes the task order's id as a path parameter, the fields to change in the body, the task order (loaded by _writable_task_order) and the user.
    Returns a TaskOrderResponse with the task order as it now is; raises 400 when nothing is sent, a value doesn't fit, or a user who isn't an admin edits a deleted task order, 403 for a demo user or a user who isn't on its project, 404 for an unknown task order, 422 for a malformed date and 409 (with existing_task_order_id) when another live task order of its project has the number it would end up with.
    """
    _refuse_deleted_for_member(found, user)
    fields = _clean_fields(body)

    if not fields:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    row = update_task_order(task_order_id, fields)

    if row is None:
        current = _load_task_order(task_order_id)
        return _number_conflict(
            current["project_id"], fields.get("task_order_number", current["task_order_number"]), task_order_id
        )

    return _response(row, "Task order updated")


@router.post("/{task_order_id}/admin/soft-delete", response_model=TaskOrderResponse)
def delete_task_order(task_order_id: UUID, admin: UserOut = Depends(current_admin)) -> TaskOrderResponse:
    """
    Soft-delete a task order: it is marked deleted, with when and by whom, and kept with its bundle. It leaves the project's list and frees its number. Admins only. Deleting a task order that is already deleted succeeds and changes nothing.
    Takes the task order's id as a path parameter and the signed-in admin.
    Returns a TaskOrderResponse with the deleted task order; raises 403 for a user who isn't an admin and 404 for an unknown task order.
    """
    current = _load_task_order(task_order_id)

    if current["deleted_at"] is not None:
        return _response(current, "Task order was already deleted")

    row = soft_delete_task_order(task_order_id, admin.uuid)

    if row is None:
        # Deleted by someone else between the read and the statement: the same outcome
        return _response(_load_task_order(task_order_id), "Task order was already deleted")

    return _response(row, "Task order deleted")


@router.post(
    "/{task_order_id}/admin/undelete",
    response_model=TaskOrderResponse,
    responses=CONFLICT,
    dependencies=[Depends(current_admin)],
)
def undelete_task_order_record(task_order_id: UUID) -> TaskOrderResponse | JSONResponse:
    """
    Bring a soft-deleted task order back, with its number and its bundle. Admins only.
    Takes the task order's id as a path parameter.
    Returns a TaskOrderResponse with the restored task order; raises 403 for a user who isn't an admin, 404 for an unknown task order or one that isn't deleted, and 409 (with existing_task_order_id) when another live task order of its project now has its number.
    """
    current = _load_task_order(task_order_id)

    if current["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="Task order is not deleted")

    row = undelete_task_order(task_order_id)

    if row is None:
        holder = get_task_order_by_number(current["project_id"], current["task_order_number"])

        if holder is None or holder["id"] == task_order_id:
            # Restored by someone else meanwhile
            raise HTTPException(status_code=404, detail="Task order is not deleted")

        return _conflict(holder, "Another task order in this project now has this number")

    return _response(row, "Task order restored")


@router.post(
    "/{task_order_id}/properties/{property_id}", response_model=TaskOrderResponse, status_code=201
)
def bundle_task_order_property(
    task_order_id: UUID,
    property_id: UUID,
    task_order: dict = Depends(_writable_task_order),
    user: UserOut = Depends(current_user),
) -> TaskOrderResponse:
    """
    Put a property into a task order, added by the signed-in user. For a user on its project, or an admin; not a demo user. The property must belong to the task order's project, and neither may be deleted.
    Takes the task order's and the property's ids as path parameters, the task order (loaded by _writable_task_order) and the user.
    Returns 201 with a TaskOrderResponse, the task order with its properties; raises 400 for a deleted task order or property or one of another project, 403 for a demo user or a user who isn't on its project, 404 for an unknown task order or property, and 409 when the property is already in the task order.
    """
    refusal = _bundle_refusal(task_order, get_property(property_id))

    if refusal is not None:
        raise refusal

    if bundle_property(task_order_id, property_id, user.uuid) is None:
        if is_property_bundled(task_order_id, property_id):
            raise HTTPException(status_code=409, detail="Property is already in this task order")

        # One side was deleted between the checks and the statement
        refusal = _bundle_refusal(_load_task_order(task_order_id), get_property(property_id))
        raise refusal or HTTPException(status_code=500, detail="Failed to bundle property")

    return _response(_load_task_order(task_order_id), "Property bundled")


@router.delete("/{task_order_id}/properties/{property_id}", status_code=204, response_class=Response)
def unbundle_task_order_property(
    task_order_id: UUID,
    property_id: UUID,
    found: dict = Depends(_writable_task_order),
    user: UserOut = Depends(current_user),
) -> None:
    """
    Take a property out of a task order, whether or not the property is deleted. For a user on its project, or an admin; not a demo user. Only an admin unbundles from a soft-deleted task order.
    Takes the task order's and the property's ids as path parameters, the task order (loaded by _writable_task_order) and the user.
    Returns nothing (204); raises 400 when a user who isn't an admin unbundles from a deleted task order, 403 for a demo user or a user who isn't on its project, and 404 for an unknown task order or a property that isn't in it.
    """
    _refuse_deleted_for_member(found, user)

    if not unbundle_property(task_order_id, property_id):
        raise HTTPException(status_code=404, detail="Property is not in this task order")
