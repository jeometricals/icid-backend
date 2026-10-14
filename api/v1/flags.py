from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from api.queries.contract_items import get_project_pay_item
from api.queries.flags import create_flag, get_flag, list_flags, soft_delete_flag, undelete_flag, update_flag
from api.queries.pirs import get_pir
from api.queries.properties import get_property
from api.schemas.auth import UserOut
from api.schemas.flag import FlagCreate, FlagListResponse, FlagOut, FlagResponse, FlagUpdate
from api.services.auth import current_admin, current_user, flag_member, no_demo_flag_writes, pir_member
from api.services.flag_area import compute_area
from api.v1._shared import refuse_deleted_for_member

# A second router under /v1/pirs, for a PIR's flags. pir_member keeps demo users to their own project.
pir_router = APIRouter(prefix="/v1/pirs", tags=["Flags"], dependencies=[Depends(current_user)])
# One flag by its id
router = APIRouter(prefix="/v1/flags", tags=["Flags"], dependencies=[Depends(current_user)])

# The fields a PATCH can clear with a null; for any other, a null counts as not sent
CLEARABLE = {
    "sketch_x", "sketch_y", "surveyed_non_assessable_reason", "pay_item_id", "reported_quantity", "quantity_delta_reason",
}
PAY_ITEM_ELSEWHERE = "Pay item belongs to a different project"


def _flag_writer_on_pir(found: dict = Depends(pir_member), user: UserOut = Depends(no_demo_flag_writes)) -> dict:
    """
    FastAPI dependency for adding a flag to a PIR: the signed-in user must be on its property's project (or an admin), and not a demo user.
    Takes the PIR (loaded and checked by pir_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the PIR dict; raises 403 for a demo user.
    """
    return found


def _writable_flag(found: dict = Depends(flag_member), user: UserOut = Depends(no_demo_flag_writes)) -> dict:
    """
    FastAPI dependency for the writes to one flag: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the flag (loaded and checked by flag_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the flag dict; raises 403 for a demo user.
    """
    return found


def _refuse_deleted_parents(pir: Optional[dict[str, Any]], user: UserOut) -> None:
    """
    Keep the flags of a soft-deleted PIR, or of a PIR whose property is soft-deleted, for admins.
    Takes the PIR dict and the signed-in user.
    Returns nothing; raises 400 "PIR is deleted" or "Property is deleted" for a user who isn't an admin.
    """
    if pir is None or user.role == "admin":
        return
    refuse_deleted_for_member(pir, user, "PIR is deleted")
    owner = get_property(pir["property_id"])
    if owner is not None:
        refuse_deleted_for_member(owner, user, "Property is deleted")


def _validated(record: dict[str, Any]) -> FlagCreate:
    """
    Check a flag's whole record, as a PATCH leaves it, by the rules a new flag is checked by.
    Takes the merged record keyed by field.
    Returns the parsed FlagCreate; raises a 422 shaped as FastAPI's own when a rule is broken.
    """
    try:
        return FlagCreate.model_validate(record)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors(include_url=False)]
        ) from exc


def _check_pay_item(project_id: str, pay_item_id: Optional[UUID]) -> None:
    """
    Refuse a pay item that isn't one of the project's.
    Takes the project id and the pay item's id (None for none).
    Returns nothing; raises 400 when the project has no such pay item.
    """
    if pay_item_id is not None and get_project_pay_item(project_id, pay_item_id) is None:
        raise HTTPException(status_code=400, detail=PAY_ITEM_ELSEWHERE)


def _response(row: dict[str, Any], message: str) -> FlagResponse:
    """
    Wrap one flag in the response envelope.
    Takes the flag dict and the message.
    Returns the FlagResponse.
    """
    return FlagResponse(status="success", message=message, data=FlagOut.model_validate(row))


def _load_flag(flag_id: UUID) -> dict[str, Any]:
    """
    Fetch a flag for an admin route.
    Takes the flag's id.
    Returns the flag dict; raises 404 when there is none.
    """
    found = get_flag(flag_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Flag not found")
    return found


@pir_router.get("/{pir_id}/flags", response_model=FlagListResponse)
def list_pir_flags(
    include_deleted: bool = False, found: dict = Depends(pir_member), user: UserOut = Depends(current_user)
) -> FlagListResponse:
    """
    List a PIR's flags by display order, the soft-deleted ones only for an admin who asks (include_deleted from anyone else is ignored). For a user on its project, or an admin.
    Takes the include_deleted flag, the PIR (loaded and checked by pir_member) and the signed-in user.
    Returns a FlagListResponse; raises 403 for a user who isn't on its project, 404 for an unknown PIR and 500 if the list can't be read.
    """
    rows = list_flags(found["id"], include_deleted and user.role == "admin")

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch flags")

    return FlagListResponse(status="success", message="Flags retrieved", data=[FlagOut.model_validate(row) for row in rows])


@pir_router.post("/{pir_id}/flags", response_model=FlagResponse, status_code=201)
def create_pir_flag(
    body: FlagCreate, pir: dict = Depends(_flag_writer_on_pir), user: UserOut = Depends(current_user)
) -> FlagResponse:
    """
    Add a flag to a PIR, created by the signed-in user, its area computed from its shape. For a user on the PIR's project, or an admin; not a demo user. Only an admin adds to a soft-deleted PIR, or to a PIR of a soft-deleted property.
    Takes the flag in the body (checked by FlagCreate), the PIR (loaded by _flag_writer_on_pir) and the user.
    Returns 201 with a FlagResponse; raises 400 for a deleted PIR or property (a user who isn't an admin) or a pay item of another project, 403 for a demo user or a user who isn't on the project, 404 for an unknown PIR and 422 for dimensions or values that break a rule.
    """
    _refuse_deleted_parents(pir, user)
    _check_pay_item(pir["project_id"], body.pay_item_id)

    record = body.model_dump()
    row = create_flag(pir["id"], user.uuid, record, compute_area(body.shape, body.dimensions))

    if row is None:
        # The pay item was deleted or moved between the check and the statement
        _check_pay_item(pir["project_id"], body.pay_item_id)
        raise HTTPException(status_code=500, detail="Failed to create flag")

    return _response(row, "Flag created")


@router.get("/{flag_id}", response_model=FlagResponse)
def get_flag_record(found: dict = Depends(flag_member)) -> FlagResponse:
    """
    Fetch one flag, soft-deleted or not. For a user on its project, or an admin.
    Takes the flag (loaded and checked by flag_member from the flag_id path parameter).
    Returns a FlagResponse; raises 403 for a user who isn't on its project and 404 for an unknown flag.
    """
    return _response(found, "Flag retrieved")


@router.patch("/{flag_id}", response_model=FlagResponse)
def update_flag_record(
    flag_id: UUID, body: FlagUpdate, found: dict = Depends(_writable_flag), user: UserOut = Depends(current_user)
) -> FlagResponse:
    """
    Change some of a flag. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted flag, or a flag of a soft-deleted PIR or property. The body is merged over the stored flag and the whole is checked as a new flag is; the area is computed again. A null clears sketch_x, sketch_y, surveyed_non_assessable_reason, pay_item_id or quantity_delta_reason; on reported_quantity it reverts to the computed quantity and clears quantity_delta_reason; on anything else it counts as not sent.
    Takes the flag's id as a path parameter, the fields to change in the body, the flag (loaded by _writable_flag) and the user.
    Returns a FlagResponse with the flag as it now is; raises 400 when nothing is sent, the flag or its PIR or property is deleted (a user who isn't an admin) or the pay item is another project's, 403 for a demo user or a user who isn't on its project, 404 for an unknown flag and 422 when the merged flag breaks a rule (a new shape needs its dimensions).
    """
    refuse_deleted_for_member(found, user, "Flag is deleted")
    _refuse_deleted_parents(get_pir(found["pir_id"]) if user.role != "admin" else None, user)

    changes = {
        field: value for field, value in body.model_dump(exclude_unset=True).items()
        if value is not None or field in CLEARABLE
    }

    if not changes:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    if changes.get("shape", found["shape"]) != found["shape"] and "dimensions" not in changes:
        raise RequestValidationError([{
            "type": "missing", "loc": ("body", "dimensions"), "msg": "dimensions are required when the shape changes",
            "input": None,
        }])

    if "reported_quantity" in changes and changes["reported_quantity"] is None:
        changes["quantity_delta_reason"] = None  # back to the computed quantity: no override left to explain

    merged = _validated({**{field: found[field] for field in FlagCreate.model_fields}, **changes})
    _check_pay_item(found["project_id"], merged.pay_item_id)

    row = update_flag(flag_id, merged.model_dump(), compute_area(merged.shape, merged.dimensions))

    if row is None:
        # The pay item was deleted or moved between the check and the statement
        _check_pay_item(found["project_id"], merged.pay_item_id)
        raise HTTPException(status_code=500, detail="Failed to update flag")

    return _response(row, "Flag updated")


@router.post("/{flag_id}/admin/soft-delete", response_model=FlagResponse)
def delete_flag(flag_id: UUID, admin: UserOut = Depends(current_admin)) -> FlagResponse:
    """
    Soft-delete a flag: it is marked deleted, with when and by whom, and kept. Admins only. Deleting a flag that is already deleted succeeds and changes nothing.
    Takes the flag's id as a path parameter and the signed-in admin.
    Returns a FlagResponse with the deleted flag; raises 403 for a user who isn't an admin and 404 for an unknown flag.
    """
    current = _load_flag(flag_id)

    if current["deleted_at"] is not None:
        return _response(current, "Flag was already deleted")

    row = soft_delete_flag(flag_id, admin.uuid)

    if row is None:
        # Deleted by someone else between the read and the statement: the same outcome
        return _response(_load_flag(flag_id), "Flag was already deleted")

    return _response(row, "Flag deleted")


@router.post("/{flag_id}/admin/undelete", response_model=FlagResponse, dependencies=[Depends(current_admin)])
def undelete_flag_record(flag_id: UUID) -> FlagResponse:
    """
    Bring a soft-deleted flag back. Admins only.
    Takes the flag's id as a path parameter.
    Returns a FlagResponse with the restored flag; raises 403 for a user who isn't an admin and 404 for an unknown flag or one that isn't deleted.
    """
    current = _load_flag(flag_id)

    if current["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="Flag is not deleted")

    row = undelete_flag(flag_id)

    if row is None:
        # Restored by someone else meanwhile
        raise HTTPException(status_code=404, detail="Flag is not deleted")

    return _response(row, "Flag restored")
