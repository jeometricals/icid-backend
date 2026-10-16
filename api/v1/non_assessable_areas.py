from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.flags import get_flag, get_flag_placement
from api.queries.non_assessable_areas import (
    create_area,
    list_areas_for_flag,
    soft_delete_area,
    update_area,
)
from api.schemas.auth import UserOut
from api.schemas.non_assessable_area import (
    NonAssessableAreaCreate,
    NonAssessableAreaListResponse,
    NonAssessableAreaOut,
    NonAssessableAreaResponse,
    NonAssessableAreaUpdate,
)
from api.services.area_containment import TOLERANCE_SF, area_outside_flag, oversize_message
from api.services.auth import current_user, flag_member, no_demo_flag_writes, non_assessable_area_member
from api.services.flag_area import compute_area
from api.v1._shared import (
    PAGE_DELETED,
    PAGE_PARENTS,
    changes_sent,
    refuse_deleted_chain,
    refuse_deleted_for_member,
    require_dimensions_with_shape,
    validated,
)

# A flag's areas, under /v1/flags (a second router there), and one area by its id. flag_member and
# non_assessable_area_member keep demo users to their own project; demo users only read.
flag_router = APIRouter(prefix="/v1/flags", tags=["Non-assessable areas"], dependencies=[Depends(current_user)])
router = APIRouter(
    prefix="/v1/non-assessable-areas", tags=["Non-assessable areas"], dependencies=[Depends(current_user)]
)

FLAG_DELETED = "Flag is deleted"
# An area is the admin's once its flag, page, PIR or property is soft-deleted
AREA_PARENTS = [("flag_deleted_at", FLAG_DELETED), *PAGE_PARENTS]


def _area_writer_on_flag(found: dict = Depends(flag_member), user: UserOut = Depends(no_demo_flag_writes)) -> dict:
    """
    FastAPI dependency for adding an area to a flag: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the flag (loaded and checked by flag_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the flag dict; raises 403 for a demo user.
    """
    return found


def _writable_area(
    found: dict = Depends(non_assessable_area_member), user: UserOut = Depends(no_demo_flag_writes)
) -> dict:
    """
    FastAPI dependency for the writes to one area: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the area (loaded and checked by non_assessable_area_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the area dict; raises 403 for a demo user.
    """
    return found


def _response(row: dict[str, Any], message: str) -> NonAssessableAreaResponse:
    """
    Wrap one area in the response envelope.
    Takes the area dict and the message.
    Returns the NonAssessableAreaResponse.
    """
    return NonAssessableAreaResponse(status="success", message=message, data=NonAssessableAreaOut.model_validate(row))


def _refuse_oversize(area: dict[str, Any], flag_id: UUID) -> None:
    """
    Refuse an area that reaches past its flag (more than TOLERANCE_SF outside it), as both are placed on their page. Nothing is refused when that can't be told: either has no place, or the page has no scale.
    Takes the area's values (shape, dimensions, sketch_x, sketch_y, rotation, display_order) and its flag's id.
    Returns nothing; raises 400 naming how much lies outside, and 500 if the flag's place can't be read.
    """
    rows = get_flag_placement(flag_id)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to read the flag's place")
    if not rows:
        return  # gone meanwhile: the write itself decides
    outside = area_outside_flag(area, rows[0], rows[0].get("scale_px_per_ft"))
    if outside is not None and outside > TOLERANCE_SF:
        raise HTTPException(status_code=400, detail=oversize_message(area["display_order"], flag_id, outside))


def _order_taken(display_order: int) -> HTTPException:
    """
    The 409 for a display order another live area of the flag has.
    Takes the display order.
    Returns the HTTPException to raise.
    """
    return HTTPException(
        status_code=409, detail=f"A non-assessable area of this flag already has display_order {display_order}"
    )


@flag_router.get("/{flag_id}/non-assessable-areas", response_model=NonAssessableAreaListResponse)
def list_flag_areas(found: dict = Depends(flag_member)) -> NonAssessableAreaListResponse:
    """
    List a flag's live non-assessable areas, by display order. For a user on its project, or an admin.
    Takes the flag (loaded and checked by flag_member from the flag_id path parameter).
    Returns a NonAssessableAreaListResponse; raises 403 for a user who isn't on its project, 404 for an unknown flag and 500 if the list can't be read.
    """
    rows = list_areas_for_flag(found["id"])
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch non-assessable areas")
    return NonAssessableAreaListResponse(
        status="success", message="Non-assessable areas retrieved",
        data=[NonAssessableAreaOut.model_validate(row) for row in rows],
    )


@flag_router.post("/{flag_id}/non-assessable-areas", response_model=NonAssessableAreaResponse, status_code=201)
def create_flag_area(
    body: NonAssessableAreaCreate, flag: dict = Depends(_area_writer_on_flag), user: UserOut = Depends(current_user)
) -> NonAssessableAreaResponse:
    """
    Add a non-assessable area to a flag, created by the signed-in user, its area computed from its shape and taken off the flag's; it must lie within the flag (R1h.4). For a user on the flag's project, or an admin; not a demo user. Nobody adds to a deleted flag or one on a replaced page; only an admin adds under a soft-deleted PIR or property.
    Takes the area in the body (checked by NonAssessableAreaCreate), the flag (loaded by _area_writer_on_flag) and the user.
    Returns 201 with a NonAssessableAreaResponse; raises 400 for a deleted flag, page, PIR or property or an area reaching past its flag, 403 for a demo user or a user who isn't on the project, 404 for an unknown flag, 409 when a live area of the flag has the display order and 422 for dimensions or values that break a rule.
    """
    if flag["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=FLAG_DELETED)
    if flag["page_deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    refuse_deleted_chain(flag, user, PAGE_PARENTS)
    _refuse_oversize(body.model_dump(), flag["id"])

    row = create_area(flag["id"], user.uuid, body.model_dump(), compute_area(body.shape, body.dimensions))

    if row is None:
        # Deleted, or its page replaced, meanwhile; otherwise the display order is taken
        latest = get_flag(flag["id"])
        if latest is None or latest["deleted_at"] is not None:
            raise HTTPException(status_code=400, detail=FLAG_DELETED)
        if latest["page_deleted_at"] is not None:
            raise HTTPException(status_code=400, detail=PAGE_DELETED)
        raise _order_taken(body.display_order)

    return _response(row, "Non-assessable area created")


@router.get("/{non_assessable_area_id}", response_model=NonAssessableAreaResponse)
def get_area_record(found: dict = Depends(non_assessable_area_member)) -> NonAssessableAreaResponse:
    """
    Fetch one non-assessable area, soft-deleted or not. For a user on its project, or an admin.
    Takes the area (loaded and checked by non_assessable_area_member from the non_assessable_area_id path parameter).
    Returns a NonAssessableAreaResponse; raises 403 for a user who isn't on its project and 404 for an unknown area.
    """
    return _response(found, "Non-assessable area retrieved")


@router.patch("/{non_assessable_area_id}", response_model=NonAssessableAreaResponse)
def update_area_record(
    non_assessable_area_id: UUID, body: NonAssessableAreaUpdate, found: dict = Depends(_writable_area),
    user: UserOut = Depends(current_user),
) -> NonAssessableAreaResponse:
    """
    Change some of a non-assessable area. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted area, or one under a soft-deleted flag, page, PIR or property. The body is merged over the stored area and the whole is checked as a new area is (within its flag included); its area is computed again. A null counts as not sent.
    Takes the area's id as a path parameter, the fields to change in the body, the area (loaded by _writable_area) and the user.
    Returns a NonAssessableAreaResponse with the area as it now is; raises 400 when nothing is sent, the merged area reaches past its flag, or the area or something above it is deleted (a user who isn't an admin), 403 for a demo user or a user who isn't on its project, 404 for an unknown area, 409 when another live area of the flag has the display order and 422 when the merged area breaks a rule (a new shape needs its dimensions).
    """
    refuse_deleted_for_member(found, user, "Non-assessable area is deleted")
    refuse_deleted_chain(found, user, AREA_PARENTS)

    changes = changes_sent(body, set())
    require_dimensions_with_shape(changes, found["shape"])
    merged = validated(
        NonAssessableAreaCreate, {**{field: found[field] for field in NonAssessableAreaCreate.model_fields}, **changes}
    )

    _refuse_oversize(merged.model_dump(), found["flag_id"])

    row = update_area(non_assessable_area_id, merged.model_dump(), compute_area(merged.shape, merged.dimensions))

    if row is None:
        raise _order_taken(merged.display_order)

    return _response(row, "Non-assessable area updated")


@router.delete("/{non_assessable_area_id}", status_code=204, response_class=Response)
def delete_area_record(
    non_assessable_area_id: UUID, found: dict = Depends(_writable_area), user: UserOut = Depends(current_user)
) -> None:
    """
    Delete a non-assessable area: a soft delete, with when and by whom; the flag's net quantity grows back. For a user on its project, or an admin; not a demo user. Only an admin deletes one under a soft-deleted flag, page, PIR or property. Deleting one that is already deleted succeeds and changes nothing.
    Takes the area's id as a path parameter, the area (loaded by _writable_area) and the user.
    Returns nothing (204); raises 400 for something deleted above it (a user who isn't an admin), 403 for a demo user or a user who isn't on its project, and 404 for an unknown area.
    """
    refuse_deleted_chain(found, user, AREA_PARENTS)
    if found["deleted_at"] is None:
        soft_delete_area(non_assessable_area_id, user.uuid)  # None: deleted by someone else meanwhile, the same outcome
