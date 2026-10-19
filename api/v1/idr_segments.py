from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.queries.idr_segments import (
    clear_segment_attribution,
    get_project_segment,
    segment_in_default_scope,
    set_segment_attribution,
)
from api.schemas.auth import UserOut
from api.schemas.idr_segment import IdrSegments, IdrSegmentsResponse
from api.services.auth import current_user, demo_idr_fence
from api.services.idr_segments import IdrSegmentError, idr_segments
from api.v1.idr_flags import attribution_editor, load_idr

# A fourth router under /v1/idrs, for the curb segments an IDR covers (curb billing). A demo user reaches only their
# own IDRs.
router = APIRouter(prefix="/v1/idrs", tags=["IDR segments"], dependencies=[Depends(current_user), Depends(demo_idr_fence)])


def _response(idr: dict[str, Any], message: str) -> IdrSegmentsResponse:
    """
    Read an IDR's segments into the response.
    Takes the IDR dict and the message.
    Returns an IdrSegmentsResponse; raises 500 when the segments can't be read.
    """
    try:
        data = idr_segments(idr)
    except IdrSegmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return IdrSegmentsResponse(status="success", message=message, data=IdrSegments.model_validate(data))


def _project_segment(idr: dict[str, Any], segment_id: UUID) -> dict[str, Any]:
    """
    Fetch a segment an IDR's override names, which must be one of its project's and not claimed by another IDR.
    Takes the IDR dict and the segment's uuid.
    Returns the segment dict; raises 404 (no such segment in the IDR's project) and 409 (another IDR claimed it).
    """
    segment = get_project_segment(segment_id)
    if segment is None or segment["project_id"] != idr["project_id"]:
        raise HTTPException(status_code=404, detail="Segment not found in this IDR's project")
    if segment["claimed_by_idr_id"] is not None and segment["claimed_by_idr_id"] != idr["idr_id"]:
        number = segment["claimed_by_idr_number"] or "without a number"
        raise HTTPException(status_code=409, detail=f"Segment is claimed by IDR {number}")
    return segment


def _in_default_scope(idr: dict[str, Any], segment_id: UUID) -> bool:
    """
    Whether the IDR holds a segment by default (it is on one of its task order's properties).
    Takes the IDR dict and the segment's uuid.
    Returns True or False; raises 500 when it can't be told.
    """
    in_scope = segment_in_default_scope(idr["idr_id"], segment_id)
    if in_scope is None:
        raise HTTPException(status_code=500, detail="Failed to read the IDR's task order")
    return in_scope


@router.get("/{idr_id}/attributed-segments", response_model=IdrSegmentsResponse)
def get_idr_segments(idr_id: UUID) -> IdrSegmentsResponse:
    """
    List the curb segments an IDR covers and what they come to per pay item. Before approval it is live: the live segments on its task order's properties and those included by an override, each attributed, excluded or claimed by another IDR, rolled up from the attributed ones. Once approved it is frozen: the segments it claimed, and the rollup it wrote at approval. For anyone who can read the IDR.
    Takes the IDR uuid as a path parameter.
    Returns an IdrSegmentsResponse; raises 404 for an unknown IDR and 500 when the segments can't be read.
    """
    return _response(load_idr(idr_id), "IDR segments")


@router.post("/{idr_id}/attributed-segments/{segment_id}/exclude", response_model=IdrSegmentsResponse)
def exclude_idr_segment(segment_id: UUID, idr: dict = Depends(attribution_editor),
                        user: UserOut = Depends(current_user)) -> IdrSegmentsResponse:
    """
    Leave a segment out of an IDR: one on its task order's properties is excluded by an override; one it had included is dropped back out. Excluding one already out changes nothing. Who may: as for flags.
    Takes the segment uuid as a path parameter, the IDR (loaded and checked as for flags) and the signed-in user.
    Returns the IDR's segments (IdrSegmentsResponse); raises 403, 404 and 409 as the flag routes do, 404 (no such segment in its project) and 409 (another IDR claimed it).
    """
    _project_segment(idr, segment_id)
    if _in_default_scope(idr, segment_id):
        done = set_segment_attribution(idr["idr_id"], segment_id, "exclude", user.uuid)
    else:
        done = clear_segment_attribution(idr["idr_id"], segment_id)
    if done is None:
        raise HTTPException(status_code=500, detail="Failed to exclude the segment")
    return _response(idr, "Segment excluded")


@router.post("/{idr_id}/attributed-segments/{segment_id}/include", response_model=IdrSegmentsResponse)
def include_idr_segment(segment_id: UUID, idr: dict = Depends(attribution_editor),
                        user: UserOut = Depends(current_user)) -> IdrSegmentsResponse:
    """
    Put a segment back into an IDR: one on its task order's properties loses its exclusion; one from elsewhere in its project is included by an override. Including one already in changes nothing. Who may: as for flags.
    Takes the segment uuid as a path parameter, the IDR (loaded and checked as for flags) and the signed-in user.
    Returns the IDR's segments (IdrSegmentsResponse); raises 403, 404 and 409 as the flag routes do, 404 (no such segment in its project), 400 (a deleted segment, or one on a deleted page, PIR or property) and 409 (another IDR claimed it).
    """
    segment = _project_segment(idr, segment_id)
    if segment["deleted"]:
        raise HTTPException(status_code=400, detail="Segment is deleted")
    if _in_default_scope(idr, segment_id):
        done = clear_segment_attribution(idr["idr_id"], segment_id)
    else:
        done = set_segment_attribution(idr["idr_id"], segment_id, "include", user.uuid)
    if done is None:
        raise HTTPException(status_code=500, detail="Failed to include the segment")
    return _response(idr, "Segment included")
