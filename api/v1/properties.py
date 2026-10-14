from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.properties import (
    create_property,
    get_property,
    get_property_by_bbl,
    list_properties,
    soft_delete_property,
    undelete_property,
    update_property,
)
from api.schemas.auth import UserOut
from api.schemas.property import (
    PropertyConflict,
    PropertyCreate,
    PropertyListItem,
    PropertyListResponse,
    PropertyOut,
    PropertyResponse,
    PropertyUpdate,
)
from api.services.auth import (
    current_admin,
    current_user,
    demo_project_fence,
    no_demo_property_writes,
    project_member,
    property_member,
)
from api.v1._shared import clean_text, refuse_deleted_for_member

# A second router under /v1/projects, for a project's properties. A demo user reaches only their own project.
project_router = APIRouter(
    prefix="/v1/projects", tags=["Properties"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)
# One property by its id
router = APIRouter(prefix="/v1/properties", tags=["Properties"], dependencies=[Depends(current_user)])

# The longest value each text field takes
MAX_BBL_LENGTH = 32
MAX_ADDRESS_LENGTH = 200
MAX_NOTES_LENGTH = 2000
CONFLICT = {409: {"model": PropertyConflict}}
# What a member who isn't an admin is told when they change a soft-deleted property
PROPERTY_DELETED = "Property is deleted"


def _writable_property(
    found: dict = Depends(property_member), user: UserOut = Depends(no_demo_property_writes)
) -> dict:
    """
    FastAPI dependency for the writes to one property: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the property (loaded and checked by property_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the property dict; raises 403 for a demo user.
    """
    return found


def _clean_fields(body: PropertyCreate | PropertyUpdate) -> dict[str, Any]:
    """
    Read a property's fields off the request: text trimmed, BBL and address refused when blank or too long. A field sent as null is left out; notes are optional, and blank or null ones are None.
    Takes the request body.
    Returns the values keyed by column, for the fields sent; raises 400 for a value that doesn't fit.
    """
    fields: dict[str, Any] = {}
    if body.bbl is not None:
        fields["bbl"] = clean_text(body.bbl, "bbl", MAX_BBL_LENGTH)
    if body.address is not None:
        fields["address"] = clean_text(body.address, "address", MAX_ADDRESS_LENGTH)
    if body.property_type is not None:
        fields["property_type"] = body.property_type
    if "notes" in body.model_fields_set:
        fields["notes"] = clean_text(body.notes, "notes", MAX_NOTES_LENGTH, required=False)
    return fields


def _conflict(project_id: str, bbl: str, property_id: Optional[UUID] = None) -> JSONResponse:
    """
    Build the 409 for a BBL another live property of the project has.
    Takes the project id, the BBL and the id of the property being saved (None for a new one).
    Returns the response, naming that other property; raises 500 when no other has it (the statement failed for another reason).
    """
    holder = get_property_by_bbl(project_id, bbl)
    if holder is None or holder["id"] == property_id:
        raise HTTPException(status_code=500, detail="Failed to save property")
    conflict = PropertyConflict(
        detail="A property with this BBL already exists in this project", existing_property_id=holder["id"]
    )
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


def _load_property(property_id: UUID) -> dict[str, Any]:
    """
    Fetch a property for an admin route.
    Takes the property's id.
    Returns the property dict; raises 404 when there is none.
    """
    found = get_property(property_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return found


def _response(row: dict[str, Any], message: str) -> PropertyResponse:
    """
    Wrap one property in the response envelope.
    Takes the property dict and the message.
    Returns the PropertyResponse.
    """
    return PropertyResponse(status="success", message=message, data=PropertyOut.model_validate(row))


@project_router.get("/{project_id}/properties", response_model=PropertyListResponse)
def list_project_properties(
    project_id: str, include_deleted: bool = False, user: UserOut = Depends(project_member)
) -> PropertyListResponse:
    """
    List a project's properties, by address, each with its live PIR's status. For a user on the project, or an admin; only an admin gets soft-deleted ones, and only when asked (include_deleted from anyone else is ignored).
    Takes the project id as a path parameter, the include_deleted flag and the signed-in user.
    Returns a PropertyListResponse; raises 403 for a user who isn't on the project, 404 for an unknown project and 500 if the list can't be read.
    """
    rows = list_properties(project_id, include_deleted and user.role == "admin")

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch properties")

    return PropertyListResponse(
        status="success", message="Properties retrieved", data=[PropertyListItem.model_validate(row) for row in rows]
    )


@project_router.post(
    "/{project_id}/properties",
    response_model=PropertyResponse,
    status_code=201,
    responses=CONFLICT,
    dependencies=[Depends(no_demo_property_writes)],
)
def create_project_property(
    project_id: str, body: PropertyCreate, user: UserOut = Depends(project_member)
) -> PropertyResponse | JSONResponse:
    """
    Add a property to a project, created by the signed-in user. For a user on the project, in any role, or an admin; not a demo user.
    Takes the project id as a path parameter, the BBL, address, property type and optional notes in the body (text trimmed; blank notes are none) and the user.
    Returns 201 with a PropertyResponse; raises 400 for a blank or over-long value, 403 for a demo user or a user who isn't on the project, 404 for an unknown project, 422 for an unknown property type and 409 (with existing_property_id) when a live property of the project has the BBL.
    """
    fields = _clean_fields(body)
    row = create_property(project_id, user.uuid, fields)

    if row is None:
        return _conflict(project_id, fields["bbl"])

    return _response(row, "Property created")


@router.get("/{property_id}", response_model=PropertyResponse)
def get_property_record(found: dict = Depends(property_member)) -> PropertyResponse:
    """
    Fetch one property, soft-deleted or not. For a user on its project, or an admin.
    Takes the property (loaded and checked by property_member from the property_id path parameter).
    Returns a PropertyResponse; raises 403 for a user who isn't on its project and 404 for an unknown property.
    """
    return _response(found, "Property retrieved")


@router.patch("/{property_id}", response_model=PropertyResponse, responses=CONFLICT)
def update_property_record(
    property_id: UUID,
    body: PropertyUpdate,
    found: dict = Depends(_writable_property),
    user: UserOut = Depends(current_user),
) -> PropertyResponse | JSONResponse:
    """
    Change some of a property. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted one. A field left out, or sent as null, keeps its value; null or blank notes clear them.
    Takes the property's id as a path parameter, the fields to change in the body, the property (loaded by _writable_property) and the user.
    Returns a PropertyResponse with the property as it now is; raises 400 when nothing is sent, a value doesn't fit, or a user who isn't an admin edits a deleted property, 403 for a demo user or a user who isn't on its project, 404 for an unknown property, 422 for an unknown property type and 409 (with existing_property_id) when another live property of its project has the BBL it would end up with.
    """
    refuse_deleted_for_member(found, user, PROPERTY_DELETED)
    fields = _clean_fields(body)

    if not fields:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    row = update_property(property_id, fields)

    if row is None:
        current = _load_property(property_id)
        return _conflict(current["project_id"], fields.get("bbl", current["bbl"]), property_id)

    return _response(row, "Property updated")


@router.post("/{property_id}/admin/soft-delete", response_model=PropertyResponse)
def delete_property(property_id: UUID, admin: UserOut = Depends(current_admin)) -> PropertyResponse:
    """
    Soft-delete a property: it is marked deleted, with when and by whom, and kept. It leaves the project's list and frees its BBL. Admins only. Deleting a property that is already deleted succeeds and changes nothing.
    Takes the property's id as a path parameter and the signed-in admin.
    Returns a PropertyResponse with the deleted property; raises 403 for a user who isn't an admin and 404 for an unknown property.
    """
    current = _load_property(property_id)

    if current["deleted_at"] is not None:
        return _response(current, "Property was already deleted")

    row = soft_delete_property(property_id, admin.uuid)

    if row is None:
        # Deleted by someone else between the read and the statement: the same outcome
        return _response(_load_property(property_id), "Property was already deleted")

    return _response(row, "Property deleted")


@router.post(
    "/{property_id}/admin/undelete",
    response_model=PropertyResponse,
    responses=CONFLICT,
    dependencies=[Depends(current_admin)],
)
def undelete_property_record(property_id: UUID) -> PropertyResponse | JSONResponse:
    """
    Bring a soft-deleted property back, with the BBL it had. Admins only.
    Takes the property's id as a path parameter.
    Returns a PropertyResponse with the restored property; raises 403 for a user who isn't an admin, 404 for an unknown property or one that isn't deleted, and 409 (with existing_property_id) when another live property of its project now has its BBL.
    """
    current = _load_property(property_id)

    if current["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="Property is not deleted")

    row = undelete_property(property_id)

    if row is None:
        holder = get_property_by_bbl(current["project_id"], current["bbl"])

        if holder is None or holder["id"] == property_id:
            # Restored by someone else meanwhile
            raise HTTPException(status_code=404, detail="Property is not deleted")

        conflict = PropertyConflict(
            detail="Another property in this project now has this BBL", existing_property_id=holder["id"]
        )
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return _response(row, "Property restored")
