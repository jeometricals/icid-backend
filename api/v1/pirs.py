from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.pirs import (
    create_pir,
    get_pir,
    get_pir_by_property,
    list_pirs_for_property,
    soft_delete_pir,
    undelete_pir,
    update_pir,
)
from api.queries.properties import get_property
from api.schemas.auth import UserOut
from api.schemas.pir import PirConflict, PirCreate, PirListResponse, PirOut, PirResponse, PirUpdate
from api.services.auth import (
    current_admin,
    current_user,
    no_demo_pir_writes,
    pir_member,
    property_member,
)
from api.v1._shared import clean_text, refuse_deleted_pir_for_member, writable_pir

# A second router under /v1/properties, for a property's PIR. property_member keeps demo users to their own project.
property_router = APIRouter(prefix="/v1/properties", tags=["PIRs"], dependencies=[Depends(current_user)])
# One PIR by its id
router = APIRouter(prefix="/v1/pirs", tags=["PIRs"], dependencies=[Depends(current_user)])

# The longest initials the field takes
MAX_INITIALS_LENGTH = 16
CONFLICT = {409: {"model": PirConflict}}


def _pir_writer_on_property(
    found: dict = Depends(property_member), user: UserOut = Depends(no_demo_pir_writes)
) -> dict:
    """
    FastAPI dependency for creating a property's PIR: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the property (loaded and checked by property_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the property dict; raises 403 for a demo user.
    """
    return found


def _clean_fields(body: PirCreate | PirUpdate) -> dict[str, Any]:
    """
    Read a PIR's fields off the request: the initials trimmed and refused when blank or too long. A field sent as null is left out.
    Takes the request body.
    Returns the values keyed by column, for the fields sent; raises 400 for a value that doesn't fit.
    """
    fields: dict[str, Any] = {}
    if body.inspector_initials is not None:
        fields["inspector_initials"] = clean_text(body.inspector_initials, "inspector_initials", MAX_INITIALS_LENGTH)
    if body.inspector_sign_date is not None:
        fields["inspector_sign_date"] = body.inspector_sign_date
    return fields


def _conflict(holder: dict[str, Any], detail: str) -> JSONResponse:
    """
    Build the 409 for a property that already has a live PIR.
    Takes that PIR's dict and the reason.
    Returns the response, naming it.
    """
    conflict = PirConflict(detail=detail, existing_pir_id=holder["id"])
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


def _load_pir(pir_id: UUID) -> dict[str, Any]:
    """
    Fetch a PIR for an admin route.
    Takes the PIR's id.
    Returns the PIR dict; raises 404 when there is none.
    """
    found = get_pir(pir_id)
    if found is None:
        raise HTTPException(status_code=404, detail="PIR not found")
    return found


def _response(row: dict[str, Any], message: str) -> PirResponse:
    """
    Wrap one PIR in the response envelope.
    Takes the PIR dict and the message.
    Returns the PirResponse.
    """
    return PirResponse(status="success", message=message, data=PirOut.model_validate(row))


@property_router.get("/{property_id}/pir", response_model=PirResponse)
def get_property_pir(found: dict = Depends(property_member)) -> PirResponse:
    """
    Fetch a property's live PIR. For a user on its project, or an admin.
    Takes the property (loaded and checked by property_member from the property_id path parameter).
    Returns a PirResponse; raises 403 for a user who isn't on its project and 404 for an unknown property or one with no live PIR.
    """
    pir = get_pir_by_property(found["id"])

    if pir is None:
        raise HTTPException(status_code=404, detail="No PIR for this property")

    return _response(pir, "PIR retrieved")


@property_router.get("/{property_id}/pirs", response_model=PirListResponse)
def list_property_pirs(
    include_deleted: bool = False, found: dict = Depends(property_member), user: UserOut = Depends(current_user)
) -> PirListResponse:
    """
    List a property's PIRs, most recently digitized first: the live one, and for an admin who asks, the soft-deleted ones (include_deleted from anyone else is ignored). For a user on its project, or an admin.
    Takes the include_deleted flag, the property (loaded and checked by property_member) and the signed-in user.
    Returns a PirListResponse; raises 403 for a user who isn't on its project, 404 for an unknown property and 500 if the list can't be read.
    """
    rows = list_pirs_for_property(found["id"], include_deleted and user.role == "admin")

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch PIRs")

    return PirListResponse(status="success", message="PIRs retrieved", data=[PirOut.model_validate(row) for row in rows])


@property_router.post(
    "/{property_id}/pir",
    response_model=PirResponse,
    status_code=201,
    responses=CONFLICT,
)
def create_property_pir(
    body: PirCreate, found: dict = Depends(_pir_writer_on_property), user: UserOut = Depends(current_user)
) -> PirResponse | JSONResponse:
    """
    Digitize a PIR for a property: digitized by the signed-in user, now. For a user on its project, in any role, or an admin; not a demo user.
    Takes the inspector's initials (trimmed) and sign date in the body, the property (loaded by _pir_writer_on_property) and the user.
    Returns 201 with a PirResponse; raises 400 for blank or over-long initials or a deleted property, 403 for a demo user or a user who isn't on its project, 404 for an unknown property, 422 for a malformed date and 409 (with existing_pir_id) when the property already has a live PIR.
    """
    if found["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail="Cannot add a PIR to a deleted property")

    fields = _clean_fields(body)
    row = create_pir(found["id"], user.uuid, fields)

    if row is None:
        holder = get_pir_by_property(found["id"])

        if holder is not None:
            return _conflict(holder, "This property already has a PIR")

        current = get_property(found["id"])
        if current is not None and current["deleted_at"] is not None:
            # Deleted between the check and the statement
            raise HTTPException(status_code=400, detail="Cannot add a PIR to a deleted property")

        raise HTTPException(status_code=500, detail="Failed to create PIR")

    return _response(row, "PIR created")


@router.get("/{pir_id}", response_model=PirResponse)
def get_pir_record(found: dict = Depends(pir_member)) -> PirResponse:
    """
    Fetch one PIR, soft-deleted or not. For a user on its property's project, or an admin.
    Takes the PIR (loaded and checked by pir_member from the pir_id path parameter).
    Returns a PirResponse; raises 403 for a user who isn't on its project and 404 for an unknown PIR.
    """
    return _response(found, "PIR retrieved")


@router.patch("/{pir_id}", response_model=PirResponse)
def update_pir_record(
    pir_id: UUID, body: PirUpdate, found: dict = Depends(writable_pir), user: UserOut = Depends(current_user)
) -> PirResponse:
    """
    Change a PIR's inspector initials or sign date. For a user on its property's project, or an admin; not a demo user. Only an admin changes a soft-deleted PIR, or a PIR of a soft-deleted property. A field left out, or sent as null, keeps its value; the as-built signature is never changed here.
    Takes the PIR's id as a path parameter, the fields to change in the body, the PIR (loaded by writable_pir) and the user.
    Returns a PirResponse with the PIR as it now is; raises 400 when nothing is sent, the initials don't fit, or a user who isn't an admin edits a deleted PIR or one of a deleted property, 403 for a demo user or a user who isn't on its project, 404 for an unknown PIR and 422 for a malformed date.
    """
    refuse_deleted_pir_for_member(found, user)
    fields = _clean_fields(body)

    if not fields:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    row = update_pir(pir_id, fields)

    if row is None:
        raise HTTPException(status_code=404, detail="PIR not found")

    return _response(row, "PIR updated")


@router.post("/{pir_id}/admin/soft-delete", response_model=PirResponse)
def delete_pir(pir_id: UUID, admin: UserOut = Depends(current_admin)) -> PirResponse:
    """
    Soft-delete a PIR: it is marked deleted, with when and by whom, and kept; its property can then take a new one. Admins only. Deleting a PIR that is already deleted succeeds and changes nothing.
    Takes the PIR's id as a path parameter and the signed-in admin.
    Returns a PirResponse with the deleted PIR; raises 403 for a user who isn't an admin and 404 for an unknown PIR.
    """
    current = _load_pir(pir_id)

    if current["deleted_at"] is not None:
        return _response(current, "PIR was already deleted")

    row = soft_delete_pir(pir_id, admin.uuid)

    if row is None:
        # Deleted by someone else between the read and the statement: the same outcome
        return _response(_load_pir(pir_id), "PIR was already deleted")

    return _response(row, "PIR deleted")


@router.post(
    "/{pir_id}/admin/undelete", response_model=PirResponse, responses=CONFLICT, dependencies=[Depends(current_admin)]
)
def undelete_pir_record(pir_id: UUID) -> PirResponse | JSONResponse:
    """
    Bring a soft-deleted PIR back. Admins only.
    Takes the PIR's id as a path parameter.
    Returns a PirResponse with the restored PIR; raises 403 for a user who isn't an admin, 404 for an unknown PIR or one that isn't deleted, and 409 (with existing_pir_id) when its property now has another live PIR.
    """
    current = _load_pir(pir_id)

    if current["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="PIR is not deleted")

    row = undelete_pir(pir_id)

    if row is None:
        holder = get_pir_by_property(current["property_id"])

        if holder is None or holder["id"] == pir_id:
            # Restored by someone else meanwhile
            raise HTTPException(status_code=404, detail="PIR is not deleted")

        return _conflict(holder, "This property now has another PIR")

    return _response(row, "PIR restored")

