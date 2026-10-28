from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.queries.flag_measurements import list_remesh_neighbours
from api.queries.flags import list_flags_for_page
from api.queries.sidewalk_strip import clear_sidewalk_strip, set_sidewalk_strip
from api.schemas.auth import UserOut
from api.schemas.sidewalk_strip import SidewalkStrip, SidewalkStripIn, SidewalkStripResponse, StripFlag
from api.services.auth import current_user, no_demo_pir_writes, pir_page_member
from api.services.measurement_bundles import join_target
from api.services.property_line import StripError, check_strip, outside_flags, strip_frontage_ft, strip_points
from api.v1._shared import PAGE_CHAIN, PAGE_DELETED, refuse_deleted_chain, require_roles

# A page's sidewalk strip, under /v1/pir-pages (Slice Y1.5)
page_router = APIRouter(prefix="/v1/pir-pages", tags=["Sidewalk strip"], dependencies=[Depends(current_user)])

# Who draws a strip: it changes what homeowners are assessed
STRIP_ROLES = ("oe", "re")


def _strip_writer(found: dict = Depends(pir_page_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for drawing or clearing a page's strip: the signed-in user must be on its project (or an admin), not a demo user, and hold the OE or RE role there (or be an admin).
    Takes the page (loaded and checked by pir_page_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the page dict; raises 403 for a demo user or a user without the role.
    """
    require_roles(found["project_id"], user, STRIP_ROLES)
    return found


def _strip(row: dict[str, Any], scale: Any, flipped: list[dict[str, Any]],
           outside_claimed: list[dict[str, Any]]) -> SidewalkStrip:
    """
    Shape a page's strip for the response.
    Takes the title block's strip columns, the page's px/ft, the flags it marked non-assessable and the claimed ones outside it.
    Returns the SidewalkStrip.
    """
    points = row["sidewalk_strip_points"]
    return SidewalkStrip(
        pir_page_id=row["pir_page_id"], points=points, source=row["sidewalk_strip_source"],
        set_at=row["sidewalk_strip_set_at"], set_by=row["sidewalk_strip_set_by"],
        frontage_ft=strip_frontage_ft(strip_points(points), scale) if points else None,
        flipped=[StripFlag.model_validate(flag) for flag in flipped],
        outside_claimed=[StripFlag.model_validate(flag) for flag in outside_claimed],
    )


def _message(flipped: int, claimed: int) -> str:
    """
    Say what a saved strip did.
    Takes how many flags it marked non-assessable and how many claimed flags outside it it left.
    Returns the message.
    """
    message = f"Sidewalk strip saved; {flipped} flag{'' if flipped == 1 else 's'} marked non-assessable"
    if claimed:
        left = "it was" if claimed == 1 else "they were"
        message += f"; {claimed} claimed flag{'' if claimed == 1 else 's'} outside it left as {left}"
    return message


@page_router.put("/{pir_page_id}/sidewalk-strip", response_model=SidewalkStripResponse)
def put_sidewalk_strip(
    pir_page_id: UUID, body: SidewalkStripIn, page: dict = Depends(_strip_writer), user: UserOut = Depends(current_user)
) -> SidewalkStripResponse:
    """
    Draw a page's sidewalk strip, the sidewalk in front of the lot (Slice Y1.5): a closed polygon whose first edge is the property line. The flags wholly outside it that are assessable and unclaimed are marked non-assessable (outside_property_line), each change written to flag_edits, in the same statement; claimed ones outside it are left as they are and listed. Each flag marked takes its measurement with it, into the one bundle open to it or out of a bundle it was in (Slice Y3b-1). points null clears the strip; flags it marked stay non-assessable. For an OE or RE on the page's project, or an admin; not a demo user. Nobody draws on a replaced page; only an admin on a page of a soft-deleted PIR or property.
    Takes the page's id as a path parameter, the points in the body (null to clear), the page (loaded by _strip_writer) and the user.
    Returns a SidewalkStripResponse; raises 400 for points that aren't a simple polygon, a page without a scale, a deleted page, PIR or property, 403 for a demo user, a user who isn't on the project or holds neither role, 404 for an unknown page and 422 for a malformed body.
    """
    if page["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    refuse_deleted_chain(page, user, PAGE_CHAIN)

    if body.points is None:
        rows = clear_sidewalk_strip(pir_page_id)
        if rows is None:
            raise HTTPException(status_code=500, detail="Failed to clear the sidewalk strip")
        cleared = rows[0] if rows else {"pir_page_id": pir_page_id, "sidewalk_strip_points": None,
                                        "sidewalk_strip_source": None, "sidewalk_strip_set_at": None,
                                        "sidewalk_strip_set_by": None}
        return SidewalkStripResponse(status="success", message="Sidewalk strip cleared; flags stay as they are",
                                     data=_strip(cleared, None, [], []))

    raw = [point.model_dump() for point in body.points]
    points = strip_points(raw)
    try:
        check_strip(points)
    except StripError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scale: Optional[Any] = page["scale_px_per_ft"]
    if not scale:
        raise HTTPException(status_code=400, detail=f"Set the scale of PIR page {page['page_number']} first")

    flags = list_flags_for_page(pir_page_id)
    if flags is None:
        raise HTTPException(status_code=500, detail="Failed to read the page's flags")
    outside = [flag for flag in outside_flags(points, flags, scale) if flag["surveyed_is_assessable"]]
    claimed = [flag for flag in outside if flag["claimed_by_idr_id"] is not None]
    to_flip = [flag["id"] for flag in outside if flag["claimed_by_idr_id"] is None]

    targets = {}
    if to_flip:
        # Each flag flipped is reclassified: the bundle it joins, if exactly one is open to it (Slice Y3b-1)
        neighbours = list_remesh_neighbours(pir_page_id)
        if neighbours is None:
            raise HTTPException(status_code=500, detail="Failed to read the page's flags")
        moving = frozenset(to_flip)
        flipping = [flag for flag in outside if flag["id"] in moving]
        targets = {flag["id"]: join_target({**flag, "surveyed_is_assessable": False}, neighbours, moving - {flag["id"]})
                   for flag in flipping}

    row = set_sidewalk_strip(pir_page_id, raw, user.uuid, to_flip, targets)
    if row is None:
        # Replaced between the read and the statement
        raise HTTPException(status_code=400, detail=PAGE_DELETED)

    return SidewalkStripResponse(status="success", message=_message(len(row["flipped"]), len(claimed)),
                                 data=_strip(row, scale, row["flipped"], claimed))
