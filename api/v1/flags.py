from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.flags import (
    create_flag,
    get_flag,
    list_flags,
    list_flags_for_page,
    soft_delete_flag,
    undelete_flag,
    update_flag,
)
from api.queries.pir_pages import list_pages_for_pir
from api.schemas.auth import UserOut
from api.schemas.flag import FlagCreate, FlagListResponse, FlagOut, FlagResponse, FlagUpdate
from api.services.auth import (
    current_admin,
    current_user,
    flag_member,
    no_demo_flag_writes,
    pir_member,
    pir_page_member,
)
from api.services.flag_area import compute_area
from api.v1._shared import (
    PAGE_CHAIN,
    PAGE_DELETED,
    PAGE_PARENTS,
    changes_sent,
    check_pay_item,
    refuse_deleted_chain,
    refuse_deleted_for_member,
    refuse_deleted_pir_for_member,
    require_dimensions_with_shape,
    validated,
)

# A second router under /v1/pirs (a PIR's flags across its pages), one under /v1/pir-pages (a page's flags), and
# one flag by its id. pir_member and pir_page_member keep demo users to their own project.
pir_router = APIRouter(prefix="/v1/pirs", tags=["Flags"], dependencies=[Depends(current_user)])
page_router = APIRouter(prefix="/v1/pir-pages", tags=["Flags"], dependencies=[Depends(current_user)])
router = APIRouter(prefix="/v1/flags", tags=["Flags"], dependencies=[Depends(current_user)])

# The fields a PATCH can clear with a null; for any other, a null counts as not sent
CLEARABLE = {
    "sketch_x", "sketch_y", "surveyed_non_assessable_reason", "pay_item_id", "reported_quantity", "quantity_delta_reason",
}
# A flag is the admin's once its page, PIR or property is soft-deleted
FLAG_PARENTS = PAGE_PARENTS


def _flag_writer_on_pir(found: dict = Depends(pir_member), user: UserOut = Depends(no_demo_flag_writes)) -> dict:
    """
    FastAPI dependency for the deprecated way of adding a flag, to a PIR: the signed-in user must be on its property's project (or an admin), and not a demo user.
    Takes the PIR (loaded and checked by pir_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the PIR dict; raises 403 for a demo user.
    """
    return found


def _flag_writer_on_page(found: dict = Depends(pir_page_member), user: UserOut = Depends(no_demo_flag_writes)) -> dict:
    """
    FastAPI dependency for adding a flag to a page: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the page (loaded and checked by pir_page_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the page dict; raises 403 for a demo user.
    """
    return found


def _writable_flag(found: dict = Depends(flag_member), user: UserOut = Depends(no_demo_flag_writes)) -> dict:
    """
    FastAPI dependency for the writes to one flag: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the flag (loaded and checked by flag_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the flag dict; raises 403 for a demo user.
    """
    return found


def _response(row: dict[str, Any], message: str) -> FlagResponse:
    """
    Wrap one flag in the response envelope.
    Takes the flag dict and the message.
    Returns the FlagResponse.
    """
    return FlagResponse(status="success", message=message, data=FlagOut.model_validate(row))


def _list_response(rows: Optional[list[dict[str, Any]]]) -> FlagListResponse:
    """
    Wrap a list of flags in the response envelope.
    Takes the flag dicts (None when they couldn't be read).
    Returns the FlagListResponse; raises 500 for None.
    """
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch flags")
    return FlagListResponse(status="success", message="Flags retrieved", data=[FlagOut.model_validate(row) for row in rows])


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


def _create_on_page(page: dict[str, Any], body: FlagCreate, user: UserOut) -> FlagResponse:
    """
    Add a flag to a live page, created by the user, its area computed from its shape.
    Takes the page dict, the checked flag and the user.
    Returns the FlagResponse; raises 400 for a pay item of another project or a page replaced meanwhile, and 500 if the statement fails otherwise.
    """
    check_pay_item(page["project_id"], body.pay_item_id)
    row = create_flag(page["pir_page_id"], user.uuid, body.model_dump(), compute_area(body.shape, body.dimensions))

    if row is None:
        # The page was replaced, or the pay item deleted or moved, between the checks and the statement
        check_pay_item(page["project_id"], body.pay_item_id)
        raise HTTPException(status_code=400, detail=PAGE_DELETED)

    return _response(row, "Flag created")


@pir_router.get("/{pir_id}/flags", response_model=FlagListResponse)
def list_pir_flags(
    include_deleted: bool = False, found: dict = Depends(pir_member), user: UserOut = Depends(current_user)
) -> FlagListResponse:
    """
    List a PIR's flags across its live pages, by display order; for an admin who asks, every flag it has had (include_deleted from anyone else is ignored). For a user on its project, or an admin.
    Takes the include_deleted flag, the PIR (loaded and checked by pir_member) and the signed-in user.
    Returns a FlagListResponse; raises 403 for a user who isn't on its project, 404 for an unknown PIR and 500 if the list can't be read.
    """
    return _list_response(list_flags(found["id"], include_deleted and user.role == "admin"))


@pir_router.post("/{pir_id}/flags", response_model=FlagResponse, status_code=201, deprecated=True)
def create_pir_flag(
    body: FlagCreate, pir: dict = Depends(_flag_writer_on_pir), user: UserOut = Depends(current_user)
) -> FlagResponse:
    """
    DEPRECATED (R1e, remove in R1f): add a flag to a PIR, which puts it on the PIR's page 1. Use POST /v1/pir-pages/{id}/flags. For a user on the PIR's project, or an admin; not a demo user. Only an admin adds to a soft-deleted PIR, or to a PIR of a soft-deleted property.
    Takes the flag in the body (checked by FlagCreate), the PIR (loaded by _flag_writer_on_pir) and the user.
    Returns 201 with a FlagResponse; raises 400 "Upload the PIR image first" for a PIR without pages, and otherwise as the page route does.
    """
    refuse_deleted_pir_for_member(pir, user)
    pages = list_pages_for_pir(pir["id"])
    if pages is None:
        raise HTTPException(status_code=500, detail="Failed to fetch pages")
    if not pages:
        raise HTTPException(status_code=400, detail="Upload the PIR image first")
    return _create_on_page(pages[0], body, user)


@page_router.get("/{pir_page_id}/flags", response_model=FlagListResponse)
def list_page_flags(found: dict = Depends(pir_page_member)) -> FlagListResponse:
    """
    List the live flags drawn on one page, by display order. For a user on its project, or an admin.
    Takes the page (loaded and checked by pir_page_member from the pir_page_id path parameter).
    Returns a FlagListResponse; raises 403 for a user who isn't on its project, 404 for an unknown page and 500 if the list can't be read.
    """
    return _list_response(list_flags_for_page(found["pir_page_id"]))


@page_router.post("/{pir_page_id}/flags", response_model=FlagResponse, status_code=201)
def create_page_flag(
    body: FlagCreate, page: dict = Depends(_flag_writer_on_page), user: UserOut = Depends(current_user)
) -> FlagResponse:
    """
    Add a flag to a page, created by the signed-in user, its area computed from its shape; sketch_x / sketch_y / rotation are its place on the page. For a user on the page's project, or an admin; not a demo user. Nobody adds to a replaced page; only an admin adds to a page of a soft-deleted PIR or property.
    Takes the flag in the body (checked by FlagCreate), the page (loaded by _flag_writer_on_page) and the user.
    Returns 201 with a FlagResponse; raises 400 for a deleted page, PIR or property or a pay item of another project, 403 for a demo user or a user who isn't on the project, 404 for an unknown page and 422 for dimensions or values that break a rule.
    """
    if page["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    refuse_deleted_chain(page, user, PAGE_CHAIN)
    return _create_on_page(page, body, user)


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
    Change some of a flag. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted flag, or a flag of a soft-deleted page, PIR or property. The body is merged over the stored flag and the whole is checked as a new flag is; the area is computed again. A null clears sketch_x, sketch_y, surveyed_non_assessable_reason, pay_item_id or quantity_delta_reason; on reported_quantity it reverts to the computed quantity and clears quantity_delta_reason; on anything else it counts as not sent.
    Takes the flag's id as a path parameter, the fields to change in the body, the flag (loaded by _writable_flag) and the user.
    Returns a FlagResponse with the flag as it now is; raises 400 when nothing is sent, the flag or its page, PIR or property is deleted (a user who isn't an admin) or the pay item is another project's, 403 for a demo user or a user who isn't on its project, 404 for an unknown flag and 422 when the merged flag breaks a rule (a new shape needs its dimensions).
    """
    refuse_deleted_for_member(found, user, "Flag is deleted")
    refuse_deleted_chain(found, user, FLAG_PARENTS)

    changes = changes_sent(body, CLEARABLE)
    require_dimensions_with_shape(changes, found["shape"])

    if "reported_quantity" in changes and changes["reported_quantity"] is None:
        changes["quantity_delta_reason"] = None  # back to the computed quantity: no override left to explain

    merged = validated(FlagCreate, {**{field: found[field] for field in FlagCreate.model_fields}, **changes})
    check_pay_item(found["project_id"], merged.pay_item_id)

    row = update_flag(flag_id, merged.model_dump(), compute_area(merged.shape, merged.dimensions))

    if row is None:
        # The pay item was deleted or moved between the check and the statement
        check_pay_item(found["project_id"], merged.pay_item_id)
        raise HTTPException(status_code=500, detail="Failed to update flag")

    return _response(row, "Flag updated")


@router.delete("/{flag_id}", status_code=204, response_class=Response)
def delete_flag_record(flag_id: UUID, found: dict = Depends(_writable_flag), user: UserOut = Depends(current_user)) -> None:
    """
    Delete a flag: a soft delete, with when and by whom (an admin can bring it back). For a user on its project, or an admin; not a demo user. Only an admin deletes a flag of a soft-deleted page, PIR or property. Deleting a flag that is already deleted succeeds and changes nothing.
    Takes the flag's id as a path parameter, the flag (loaded by _writable_flag) and the user.
    Returns nothing (204); raises 400 for a deleted page, PIR or property (a user who isn't an admin), 403 for a demo user or a user who isn't on its project, and 404 for an unknown flag.
    """
    refuse_deleted_chain(found, user, FLAG_PARENTS)
    if found["deleted_at"] is None:
        soft_delete_flag(flag_id, user.uuid)  # None: deleted by someone else meanwhile, the same outcome


@router.post("/{flag_id}/admin/soft-delete", response_model=FlagResponse)
def delete_flag(flag_id: UUID, admin: UserOut = Depends(current_admin)) -> FlagResponse:
    """
    Soft-delete a flag, returning it: as DELETE /v1/flags/{flag_id} does, for an admin. Admins only. Deleting a flag that is already deleted succeeds and changes nothing.
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
    Bring a soft-deleted flag back, onto its page, which must still be live, with the non-assessable areas deleted with it. Admins only.
    Takes the flag's id as a path parameter.
    Returns a FlagResponse with the restored flag; raises 400 for a flag with no page (one from before pages) or whose page was replaced, 403 for a user who isn't an admin and 404 for an unknown flag or one that isn't deleted.
    """
    current = _load_flag(flag_id)

    if current["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="Flag is not deleted")
    if current["pir_page_id"] is None:
        raise HTTPException(status_code=400, detail="This flag has no page; draw it again")
    if current["page_deleted_at"] is not None:
        raise HTTPException(status_code=400, detail="Its page was replaced")

    row = undelete_flag(flag_id)

    if row is None:
        # Restored by someone else, or its page replaced, meanwhile
        latest = _load_flag(flag_id)
        if latest["page_deleted_at"] is not None:
            raise HTTPException(status_code=400, detail="Its page was replaced")
        raise HTTPException(status_code=404, detail="Flag is not deleted")

    # Read again: the statement's own read sees the flag's areas as they were before it restored them
    return _response(_load_flag(flag_id), "Flag restored")
