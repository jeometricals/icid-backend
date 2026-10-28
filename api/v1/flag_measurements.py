from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.flag_measurements import (
    form_bundle,
    get_measurement,
    list_remesh_neighbours,
    unbundle,
    update_measurement,
)
from api.queries.flags import get_flag
from api.schemas.auth import UserOut
from api.schemas.flag_measurement import BundleIn, MeasurementOut, MeasurementPatch, MeasurementResponse
from api.services.auth import current_user, measurement_member, no_demo_pir_writes, pir_page_member
from api.services.construction_sketch import placed_outline
from api.services.measurement_bundles import classification, unconnected
from api.v1._shared import PAGE_CHAIN, PAGE_DELETED, refuse_deleted_chain, require_roles

# Measurement bundles (Slice Y3b-2): forming one on a page, and reading and breaking up one by id
page_router = APIRouter(prefix="/v1/pir-pages", tags=["Measurements"], dependencies=[Depends(current_user)])
router = APIRouter(prefix="/v1/measurements", tags=["Measurements"], dependencies=[Depends(current_user)])

# Who bundles, reads and breaks up measurements: they change what homeowners and contractors are measured at
MEASUREMENT_ROLES = ("oe", "re")
CENT = Decimal("0.01")


def _page_writer(found: dict = Depends(pir_page_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for bundling on a page: the signed-in user must be on its project (or an admin), not a demo user, and hold the OE or RE role there (or be an admin).
    Takes the page (loaded and checked by pir_page_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the page dict; raises 403 for a demo user or a user without the role.
    """
    require_roles(found["project_id"], user, MEASUREMENT_ROLES)
    return found


def _measurement_user(found: dict = Depends(measurement_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for the routes of one measurement: as _page_writer, for its project.
    Takes the measurement (loaded and checked by measurement_member) and the user (refused when a demo user).
    Returns the measurement dict; raises 403 for a demo user or a user without the role.
    """
    require_roles(found["project_id"], user, MEASUREMENT_ROLES)
    return found


def _out(found: dict[str, Any], message: str) -> MeasurementResponse:
    """
    Shape a measurement for a response.
    Takes the measurement dict (get_measurement) and the message.
    Returns the MeasurementResponse.
    """
    return MeasurementResponse(status="success", message=message, data=MeasurementOut.model_validate(found))


def _name(flag: dict[str, Any]) -> str:
    """
    How a refusal names a flag: by its number on the PIR.
    Takes the flag.
    Returns 'Flag <display_order>'.
    """
    return f"Flag {flag['display_order']}"


def outer_area(rectangles: list[dict[str, float]]) -> Decimal:
    """
    A bundle's outer area: each rectangle's L x W, to the cent, halves up, summed.
    Takes the rectangles ([{L, W}]).
    Returns the square feet.
    """
    return sum((Decimal(str(r["L"])) * Decimal(str(r["W"]))).quantize(CENT, rounding=ROUND_HALF_UP)
               for r in rectangles)


def _refuse_unless_open(found: dict[str, Any]) -> None:
    """
    Refuse a measurement that isn't a live bundle open to change.
    Takes the measurement (get_measurement).
    Returns nothing; raises 400 for a solo measurement, 404 for a retired bundle and 409 for a frozen one.
    """
    if not found["bundled"]:
        raise HTTPException(status_code=400, detail="Not a bundle")
    if found["retired_at"] is not None:
        raise HTTPException(status_code=404, detail="Bundle is already retired")
    if found["frozen"]:
        raise HTTPException(status_code=409,
                            detail=f"A member is claimed by IDR {found['claimed_by_idr_number']}: the bundle is frozen")


def _refuse_unless_changeable(found: dict[str, Any], new_outline: bool) -> None:
    """
    Refuse a re-measurement of what isn't a live bundle, or a new outline of a frozen one (Slice Y4a).
    Takes the measurement (get_measurement) and whether a new outline is asked for.
    Returns nothing; raises 400 for a solo measurement, 404 for a retired bundle and 409 for a new outline of a frozen one.
    """
    if not found["bundled"]:
        raise HTTPException(status_code=400, detail="Not a bundle")
    if found["retired_at"] is not None:
        raise HTTPException(status_code=404, detail="Bundle is already retired")
    if new_outline and found["frozen"]:
        raise HTTPException(status_code=409, detail=f"A member is claimed by IDR {found['claimed_by_idr_number']}: "
                                                    "the bundle is frozen, its outline can't change")


@page_router.post("/{pir_page_id}/measurements/bundle", response_model=MeasurementResponse, status_code=201)
def bundle_flags(pir_page_id: UUID, body: BundleIn, page: dict = Depends(_page_writer),
                 user: UserOut = Depends(current_user)) -> MeasurementResponse:
    """
    Bundle a page's flags into one measurement (Slice Y3b-2): two or more live, unclaimed flags in solo measurements, of one thickness, assessment and pay item, sharing edges in one chain (within 0.1 ft). The measurement is the outline the OE measured (1 to 3 rectangles, outer_area their sum, needs_remeasure false), its deductions the flags' areas and subtracted features of 1 SF or more; each flag takes a share by its own area. In one statement, which checks the flags again. For an OE or RE on the project, or an admin; not a demo user.
    Takes the page's id as a path parameter, the flags' ids and the outline in the body, the page (loaded by _page_writer) and the user.
    Returns a MeasurementResponse (201) with the bundle and its members; raises 400 for a flag that can't be bundled (each case named) or a page that is deleted or has no scale, 403 for a demo user or a user without the role, 404 for an unknown page, 409 when the flags changed meanwhile and 422 for a malformed body.
    """
    if page["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    refuse_deleted_chain(page, user, PAGE_CHAIN)
    ids = body.flag_ids
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="A bundle takes at least two flags")
    twice = next((flag_id for index, flag_id in enumerate(ids) if flag_id in ids[:index]), None)
    if twice is not None:
        raise HTTPException(status_code=400, detail=f"Flag {twice} is listed twice")
    if not page["scale_px_per_ft"]:
        raise HTTPException(status_code=400, detail=f"Set the scale of PIR page {page['page_number']} first")

    neighbours = list_remesh_neighbours(pir_page_id)
    if neighbours is None:
        raise HTTPException(status_code=500, detail="Failed to read the page's flags")
    live = {row["id"]: row for row in neighbours}
    for flag_id in ids:
        if flag_id not in live:
            # Not a live flag of the page: deleted from it, or not on it at all
            found = get_flag(flag_id)
            if found is not None and found["pir_page_id"] == pir_page_id and found["deleted_at"] is not None:
                raise HTTPException(status_code=400, detail=f"{_name(found)} is deleted")
            raise HTTPException(status_code=400, detail=f"Flag {flag_id} is not on this page")
    flags = [live[flag_id] for flag_id in ids]
    for flag in flags:
        if flag["claimed_by_idr_id"] is not None:
            raise HTTPException(status_code=400,
                                detail=f"{_name(flag)} is claimed by IDR {flag['claimed_by_idr_number']}")
        if flag["bundled"]:
            raise HTTPException(status_code=400, detail=f"{_name(flag)} is already in a bundle")
        if not placed_outline(flag, float(page["scale_px_per_ft"])):
            raise HTTPException(status_code=400, detail=f"{_name(flag)} has no place on the page")
    first = classification(flags[0])
    odd = [flag for flag in flags if classification(flag) != first]
    if odd:
        raise HTTPException(status_code=400, detail="The flags differ in thickness, assessment or pay item: "
                                                    + ", ".join(_name(flag) for flag in odd) + f" differ from {_name(flags[0])}")
    apart = unconnected(flags, float(page["scale_px_per_ft"]))
    if apart:
        raise HTTPException(status_code=400, detail=", ".join(_name(flag) for flag in apart)
                            + (" doesn't" if len(apart) == 1 else " don't") + " abut the rest")

    rectangles = [rectangle.model_dump() for rectangle in body.outer_dimensions.rectangles]
    rows = form_bundle(pir_page_id, ids, (flags[0]["surveyed_thickness"], flags[0]["surveyed_is_assessable"],
                                          flags[0]["pay_item_id"]), rectangles, outer_area(rectangles), user.uuid)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to form the bundle")
    if not rows:
        raise HTTPException(status_code=409, detail="The flags changed meanwhile; read them again")
    formed = get_measurement(rows[0]["measurement_id"])
    if formed is None:
        raise HTTPException(status_code=500, detail="Failed to read the bundle")
    return _out(formed, f"Bundle formed: {len(ids)} flags")


@router.get("/{measurement_id}", response_model=MeasurementResponse)
def get_measurement_record(found: dict = Depends(_measurement_user)) -> MeasurementResponse:
    """
    Fetch one measurement, solo or a bundle, retired or not (Slice Y3b-2): its outline, deductions, classification, whether frozen, and its members with their shares and quantities. For an OE or RE on its project, or an admin; not a demo user.
    Takes the measurement (loaded and checked by _measurement_user from the measurement_id path parameter).
    Returns a MeasurementResponse; raises 403 for a demo user or a user without the role and 404 for an unknown measurement.
    """
    return _out(found, "Measurement retrieved")


@router.patch("/{measurement_id}", response_model=MeasurementResponse)
def update_measurement_record(measurement_id: UUID, body: MeasurementPatch, found: dict = Depends(_measurement_user),
                              user: UserOut = Depends(current_user)) -> MeasurementResponse:
    """
    Mark a bundle re-measured (Slice Y4a): {needs_remeasure: false}, and/or a new outline the OE measured ({outer_dimensions: {rectangles}}, outer_area their sum); either way needs_remeasure is cleared, since a new outline is the re-measurement. In one statement, each change a measurement_edits row by the user. A frozen bundle (a member claimed) can be marked re-measured but its outline can't change. For an OE or RE on its project, or an admin; not a demo user.
    Takes the measurement's id as a path parameter, the fields in the body, the measurement (loaded by _measurement_user) and the user.
    Returns a MeasurementResponse with the bundle as it now is; raises 400 for an empty body or a solo measurement, 403 for a demo user or a user without the role, 404 for an unknown or retired one, 409 for a new outline on a frozen bundle (also when it changed meanwhile) and 422 for a malformed body.
    """
    if body.needs_remeasure is None and body.outer_dimensions is None:
        raise HTTPException(status_code=400, detail="Send needs_remeasure: false, outer_dimensions, or both")
    rectangles = None if body.outer_dimensions is None else [r.model_dump() for r in body.outer_dimensions.rectangles]
    _refuse_unless_changeable(found, rectangles is not None)
    rows = update_measurement(measurement_id, rectangles, None if rectangles is None else outer_area(rectangles),
                              user.uuid)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to update the measurement")
    latest = get_measurement(measurement_id)
    if not rows:
        # Retired, or claimed, meanwhile
        if latest is not None:
            _refuse_unless_changeable(latest, rectangles is not None)
        raise HTTPException(status_code=409, detail="The bundle changed meanwhile; read it again")
    if latest is None:
        raise HTTPException(status_code=500, detail="Failed to read the measurement")
    return _out(latest, "Bundle re-measured" if rectangles is not None else "Bundle marked re-measured")


@router.delete("/{measurement_id}", status_code=204, response_class=Response)
def unbundle_measurement(measurement_id: UUID, found: dict = Depends(_measurement_user),
                         user: UserOut = Depends(current_user)) -> None:
    """
    Break a bundle up (Slice Y3b-2): each member goes back to a solo measurement of its own (needs_remeasure false) and the bundle is retired as it stood, in one statement. A frozen bundle (a member claimed) can't be. For an OE or RE on its project, or an admin; not a demo user.
    Takes the measurement's id as a path parameter, the measurement (loaded by _measurement_user) and the user.
    Returns nothing (204); raises 400 for a solo measurement, 403 for a demo user or a user without the role, 404 for an unknown or retired bundle and 409 for a frozen one (also when it changed meanwhile).
    """
    _refuse_unless_open(found)
    rows = unbundle(measurement_id, user.uuid)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to break up the bundle")
    if not rows or not rows[0]["retired"]:
        # Retired or claimed meanwhile
        latest = get_measurement(measurement_id)
        if latest is not None:
            _refuse_unless_open(latest)
        raise HTTPException(status_code=409, detail="The bundle changed meanwhile; read it again")
