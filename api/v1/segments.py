from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.segments import (
    create_segment,
    list_segments_for_page,
    list_segments_for_pir,
    soft_delete_segment,
    update_segment,
)
from api.schemas.auth import UserOut
from api.schemas.segment import SegmentCreate, SegmentListResponse, SegmentOut, SegmentResponse, SegmentUpdate
from api.services.auth import current_user, no_demo_pir_writes, pir_member, pir_page_member, segment_member
from api.v1._shared import (
    PAGE_CHAIN,
    PAGE_DELETED,
    PAGE_PARENTS,
    changes_sent,
    check_pay_item,
    refuse_deleted_chain,
    refuse_deleted_for_member,
    validated,
)

# A page's segments under /v1/pir-pages, a PIR's under /v1/pirs, and one segment by its id. pir_page_member,
# pir_member and segment_member keep demo users to their own project; demo users only read.
page_router = APIRouter(prefix="/v1/pir-pages", tags=["Segments"], dependencies=[Depends(current_user)])
pir_router = APIRouter(prefix="/v1/pirs", tags=["Segments"], dependencies=[Depends(current_user)])
router = APIRouter(prefix="/v1/segments", tags=["Segments"], dependencies=[Depends(current_user)])

# The fields a PATCH can clear with a null; for any other, a null counts as not sent
CLEARABLE = {"pay_item_id", "note"}


def _segment_writer_on_page(found: dict = Depends(pir_page_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for adding a segment to a page: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the page (loaded and checked by pir_page_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the page dict; raises 403 for a demo user.
    """
    return found


def _writable_segment(found: dict = Depends(segment_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for the writes to one segment: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the segment (loaded and checked by segment_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the segment dict; raises 403 for a demo user.
    """
    return found


def _response(row: dict[str, Any], message: str) -> SegmentResponse:
    """
    Wrap one segment in the response envelope.
    Takes the segment dict and the message.
    Returns the SegmentResponse.
    """
    return SegmentResponse(status="success", message=message, data=SegmentOut.model_validate(row))


def _list_response(rows: Optional[list[dict[str, Any]]]) -> SegmentListResponse:
    """
    Wrap a list of segments in the response envelope.
    Takes the segment dicts (None when they couldn't be read).
    Returns the SegmentListResponse; raises 500 for None.
    """
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch segments")
    return SegmentListResponse(
        status="success", message="Segments retrieved", data=[SegmentOut.model_validate(row) for row in rows]
    )


@page_router.get("/{pir_page_id}/segments", response_model=SegmentListResponse)
def list_page_segments(found: dict = Depends(pir_page_member)) -> SegmentListResponse:
    """
    List the live segments drawn on one page, in the order they were drawn. For a user on its project, or an admin.
    Takes the page (loaded and checked by pir_page_member from the pir_page_id path parameter).
    Returns a SegmentListResponse; raises 403 for a user who isn't on its project, 404 for an unknown page and 500 if the list can't be read.
    """
    return _list_response(list_segments_for_page(found["pir_page_id"]))


@pir_router.get("/{pir_id}/segments", response_model=SegmentListResponse)
def list_pir_segments(found: dict = Depends(pir_member)) -> SegmentListResponse:
    """
    List a PIR's live segments across its live pages, by page. For a user on its project, or an admin.
    Takes the PIR (loaded and checked by pir_member from the pir_id path parameter).
    Returns a SegmentListResponse; raises 403 for a user who isn't on its project, 404 for an unknown PIR and 500 if the list can't be read.
    """
    return _list_response(list_segments_for_pir(found["id"]))


@page_router.post("/{pir_page_id}/segments", response_model=SegmentResponse, status_code=201)
def create_page_segment(
    body: SegmentCreate, page: dict = Depends(_segment_writer_on_page), user: UserOut = Depends(current_user)
) -> SegmentResponse:
    """
    Add a segment to a page, created by the signed-in user, its length worked out from its points and the page's scale (none while the page has no scale). For a user on the page's project, or an admin; not a demo user. Nobody adds to a replaced page; only an admin adds to a page of a soft-deleted PIR or property.
    Takes the segment in the body (checked by SegmentCreate), the page (loaded by _segment_writer_on_page) and the user.
    Returns 201 with a SegmentResponse; raises 400 for a deleted page, PIR or property or a pay item of another project, 403 for a demo user or a user who isn't on the project, 404 for an unknown page and 422 for values that break a rule.
    """
    if page["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    refuse_deleted_chain(page, user, PAGE_CHAIN)
    check_pay_item(page["project_id"], body.pay_item_id)

    row = create_segment(page["pir_page_id"], user.uuid, body.model_dump())

    if row is None:
        # The page replaced or the pay item moved between the checks and the statement
        check_pay_item(page["project_id"], body.pay_item_id)
        raise HTTPException(status_code=400, detail=PAGE_DELETED)

    return _response(row, "Segment created")


@router.get("/{segment_id}", response_model=SegmentResponse)
def get_segment_record(found: dict = Depends(segment_member)) -> SegmentResponse:
    """
    Fetch one segment, soft-deleted or not. For a user on its project, or an admin.
    Takes the segment (loaded and checked by segment_member from the segment_id path parameter).
    Returns a SegmentResponse; raises 403 for a user who isn't on its project and 404 for an unknown segment.
    """
    return _response(found, "Segment retrieved")


@router.patch("/{segment_id}", response_model=SegmentResponse)
def update_segment_record(
    segment_id: UUID, body: SegmentUpdate, found: dict = Depends(_writable_segment), user: UserOut = Depends(current_user)
) -> SegmentResponse:
    """
    Change some of a segment. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted segment, or one on a soft-deleted page, PIR or property. The body is merged over the stored segment and the whole is checked as a new segment is; its length is worked out again. A null clears pay_item_id or note; on anything else it counts as not sent.
    Takes the segment's id as a path parameter, the fields to change in the body, the segment (loaded by _writable_segment) and the user.
    Returns a SegmentResponse with the segment as it now is; raises 400 when nothing is sent, the segment or something above it is deleted (a user who isn't an admin) or the pay item is another project's, 403 for a demo user or a user who isn't on its project, 404 for an unknown segment and 422 when the merged segment breaks a rule.
    """
    refuse_deleted_for_member(found, user, "Segment is deleted")
    refuse_deleted_chain(found, user, PAGE_PARENTS)

    changes = changes_sent(body, CLEARABLE)
    merged = validated(SegmentCreate, {**{field: found[field] for field in SegmentCreate.model_fields}, **changes})
    check_pay_item(found["project_id"], merged.pay_item_id)

    row = update_segment(segment_id, merged.model_dump())

    if row is None:
        check_pay_item(found["project_id"], merged.pay_item_id)
        raise HTTPException(status_code=500, detail="Failed to update segment")

    return _response(row, "Segment updated")


@router.delete("/{segment_id}", status_code=204, response_class=Response)
def delete_segment_record(
    segment_id: UUID, found: dict = Depends(_writable_segment), user: UserOut = Depends(current_user)
) -> None:
    """
    Delete a segment: a soft delete, with when and by whom. For a user on its project, or an admin; not a demo user. Only an admin deletes one on a soft-deleted page, PIR or property. Deleting one that is already deleted succeeds and changes nothing.
    Takes the segment's id as a path parameter, the segment (loaded by _writable_segment) and the user.
    Returns nothing (204); raises 400 for something deleted above it (a user who isn't an admin), 403 for a demo user or a user who isn't on its project, and 404 for an unknown segment.
    """
    refuse_deleted_chain(found, user, PAGE_PARENTS)
    if found["deleted_at"] is None:
        soft_delete_segment(segment_id, user.uuid)  # None: deleted by someone else meanwhile, the same outcome
