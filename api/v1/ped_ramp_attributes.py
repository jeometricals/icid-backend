from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from api.queries.ped_ramp_attributes import delete_attributes, get_attributes, upsert_attributes
from api.schemas.auth import UserOut
from api.schemas.ped_ramp import (
    ALL_FIELDS,
    COMMON_FIELDS,
    TYPE_FIELDS,
    RampAttributesIn,
    RampAttributesOut,
    RampAttributesResponse,
)
from api.services.auth import current_user, no_demo_property_writes, property_member
from api.services.ped_ramp_ada import ada_warnings
from api.v1._shared import PROPERTY_DELETED, refuse_deleted_for_member

# A second router under /v1/properties, for a ped ramp's attributes
property_router = APIRouter(prefix="/v1/properties", tags=["Ped ramp attributes"], dependencies=[Depends(current_user)])

NOT_A_RAMP = "Only a ped ramp has ramp attributes"
NOT_ENTERED = "Ramp attributes not entered"


def _ramp(found: dict[str, Any]) -> dict[str, Any]:
    """
    Refuse a property that isn't a ped ramp.
    Takes the property dict.
    Returns it; raises 400 for a sidewalk.
    """
    if found["kind"] != "ped_ramp":
        raise HTTPException(status_code=400, detail=NOT_A_RAMP)
    return found


def _writable_ramp(found: dict = Depends(property_member), user: UserOut = Depends(no_demo_property_writes)) -> dict:
    """
    FastAPI dependency for the writes to a ped ramp's attributes: the signed-in user must be on its project (or an admin), and not a demo user; only an admin changes a soft-deleted ramp's.
    Takes the property (loaded and checked by property_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the property dict; raises 400 for a sidewalk or a deleted ramp of a user who isn't an admin, and 403 for a demo user.
    """
    refuse_deleted_for_member(found, user, PROPERTY_DELETED)
    return _ramp(found)


def _response(row: dict[str, Any], message: str) -> RampAttributesResponse:
    """
    Wrap a ramp's attributes in the response envelope, with the ADA warnings their values raise.
    Takes the attributes dict (with ramp_type) and the message.
    Returns the RampAttributesResponse.
    """
    data = RampAttributesOut.model_validate({**row, "warnings": ada_warnings(row, row["ramp_type"])})
    return RampAttributesResponse(status="success", message=message, data=data)


@property_router.get("/{property_id}/ped-ramp-attributes", response_model=RampAttributesResponse)
def get_ramp_attributes(found: dict = Depends(property_member)) -> RampAttributesResponse:
    """
    Fetch a ped ramp's attributes (the DOT form's section 5), with the ADA warnings they raise. For a user on its project, or an admin.
    Takes the property (loaded and checked by property_member from the property_id path parameter).
    Returns a RampAttributesResponse; raises 400 for a sidewalk, 403 for a user who isn't on its project, and 404 for an unknown property or a ramp whose attributes aren't entered.
    """
    _ramp(found)
    row = get_attributes(found["id"])

    if row is None:
        raise HTTPException(status_code=404, detail=NOT_ENTERED)

    return _response(row, "Ramp attributes retrieved")


@property_router.put("/{property_id}/ped-ramp-attributes", response_model=RampAttributesResponse)
def put_ramp_attributes(body: RampAttributesIn, found: dict = Depends(_writable_ramp)) -> RampAttributesResponse:
    """
    Save a ped ramp's attributes whole (a field left out is cleared): added the first time, replaced after. Values outside the form's advisories are saved and come back as warnings. For a user on its project, or an admin; not a demo user.
    Takes the attributes in the body (the fields every ramp has and those of its ramp type) and the property (loaded by _writable_ramp).
    Returns a RampAttributesResponse; raises 400 for a sidewalk or a deleted ramp of a user who isn't an admin, 403 for a demo user or a user who isn't on its project, 404 for an unknown property, and 422 for an unknown value or a field of another ramp type.
    """
    values = body.model_dump()
    taken = set(COMMON_FIELDS) | set(TYPE_FIELDS[found["ramp_type"]])
    stray = [field for field in ALL_FIELDS if field not in taken and values.get(field) is not None]

    if stray:
        raise HTTPException(status_code=422, detail=f"a {found['ramp_type']} ramp doesn't take {', '.join(stray)}")

    row = upsert_attributes(found["id"], values)

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save ramp attributes")

    return _response(row, "Ramp attributes saved")


@property_router.delete("/{property_id}/ped-ramp-attributes", status_code=204, response_class=Response)
def delete_ramp_attributes(found: dict = Depends(_writable_ramp)) -> Response:
    """
    Remove a ped ramp's attributes. For a user on its project, or an admin; not a demo user.
    Takes the property (loaded by _writable_ramp from the property_id path parameter).
    Returns 204; raises 400 for a sidewalk or a deleted ramp of a user who isn't an admin, 403 for a demo user or a user who isn't on its project, 404 for an unknown property or a ramp whose attributes aren't entered, and 500 if they can't be removed.
    """
    rows = delete_attributes(found["id"])

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to remove ramp attributes")
    if not rows:
        raise HTTPException(status_code=404, detail=NOT_ENTERED)

    return Response(status_code=204)
