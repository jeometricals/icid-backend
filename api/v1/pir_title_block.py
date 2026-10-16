import re
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException

from api.queries.pir_title_block import get_title_block, update_title_block
from api.schemas.pir_annotation import MAX_TEXT_LENGTH
from api.schemas.pir_title_block import PirTitleBlock, PirTitleBlockResponse
from api.schemas.auth import UserOut
from api.services.auth import current_user
from api.v1._shared import refuse_deleted_pir_for_member, writable_pir

# A third router under /v1/pirs: the title block the Construction Report prints
pir_router = APIRouter(prefix="/v1/pirs", tags=["PIR title block"], dependencies=[Depends(current_user)])

COMPASS_POINTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
DATE_FIELDS = ("date_notice_issued", "date_excavated", "date_constructed")
FIELDS = ("contract_no", *DATE_FIELDS, "north_bearing")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NO_PAGE_1 = "PIR has no page 1 to edit"


def _date(value: Any, field: str) -> date | None:
    """
    Read one of the title block's dates off the body.
    Takes the value sent and the field's name (for the error).
    Returns the date, or None for a null (which clears it); raises 400 for anything but YYYY-MM-DD naming a real day.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not ISO_DATE.match(value):
        raise HTTPException(status_code=400, detail=f"{field} must be a date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be a date (YYYY-MM-DD)") from exc


def _fields(body: dict[str, Any]) -> dict[str, Any]:
    """
    Read the title-block fields sent: contract_no trimmed (blank is none, at most MAX_TEXT_LENGTH as on the page's title block), the three dates, and north_bearing (a compass point). A null clears a field; one left out isn't there.
    Takes the request body.
    Returns the fields sent, keyed by name; raises 400 for an unknown field or a value that doesn't fit.
    """
    unknown = sorted(set(body) - set(FIELDS))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown field: {unknown[0]}")
    fields: dict[str, Any] = {}
    if "contract_no" in body:
        value = body["contract_no"]
        if value is not None and not isinstance(value, str):
            raise HTTPException(status_code=400, detail="contract_no must be text")
        text = (value or "").strip()
        if len(text) > MAX_TEXT_LENGTH:
            raise HTTPException(status_code=400,
                                detail=f"contract_no cannot be longer than {MAX_TEXT_LENGTH} characters")
        fields["contract_no"] = text or None
    for field in DATE_FIELDS:
        if field in body:
            fields[field] = _date(body[field], field)
    if "north_bearing" in body:
        bearing = body["north_bearing"]
        if bearing is not None and bearing not in COMPASS_POINTS:
            raise HTTPException(status_code=400, detail="Invalid north bearing")
        fields["north_bearing"] = bearing
    return fields


@pir_router.patch("/{pir_id}/title-block", response_model=PirTitleBlockResponse)
def update_pir_title_block(
    pir_id: UUID,
    body: dict[str, Any] = Body(...),
    found: dict = Depends(writable_pir),
    user: UserOut = Depends(current_user),
) -> PirTitleBlockResponse:
    """
    Change a PIR's title block as the Construction Report prints it: page 1's contract_no, date_notice_issued, date_excavated and date_constructed, and the PIR's north_bearing. A field left out is unchanged, a null clears one; both rows are written in one statement, and only where a value changes. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted PIR, or a PIR of a soft-deleted property.
    Takes the PIR's id as a path parameter, the fields in the body, the PIR (loaded by writable_pir) and the user.
    Returns a PirTitleBlockResponse with the whole title block as it now is; raises 400 (nothing sent, an unknown field, a value that doesn't fit, a deleted PIR or property, no live page 1), 403 (not on its project, or a demo user), 404 (no PIR) and 500.
    """
    refuse_deleted_pir_for_member(found, user)
    fields = _fields(body)
    if not fields:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    current = get_title_block(pir_id)
    if current is None:
        raise HTTPException(status_code=404, detail="PIR not found")
    if current["pir_page_id"] is None:
        raise HTTPException(status_code=400, detail=NO_PAGE_1)

    written = update_title_block(pir_id, current["pir_page_id"], fields)
    after = get_title_block(pir_id)
    if written is None or after is None:
        raise HTTPException(status_code=500, detail="Failed to save the title block")
    changed = bool(written[0]["bearing_written"] or written[0]["block_written"])
    return PirTitleBlockResponse(status="success", message="Title block saved" if changed else "Nothing changed",
                                 data=PirTitleBlock.model_validate(after))
