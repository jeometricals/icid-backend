from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.queries.flags import get_flag
from api.queries.idr_flags import clear_attribution, in_default_scope, set_attribution
from api.queries.idrs import get_idr_by_id
from api.schemas.auth import UserOut
from api.schemas.idr_flag import IdrFlags, IdrFlagsResponse
from api.services.auth import current_user, demo_idr_fence, no_demo_idr_flag_writes, stage_reviewer
from api.services.idr_flags import IdrFlagError, idr_flags

# A third router under /v1/idrs, for the flags an IDR covers (R1f). A demo user reaches only their own IDRs.
router = APIRouter(prefix="/v1/idrs", tags=["IDR flags"], dependencies=[Depends(current_user), Depends(demo_idr_fence)])

# The review statuses a stage's reviewer changes an IDR's flags in
_IN_REVIEW = ("stage1_review", "stage2_review")


def load_idr(idr_id: UUID) -> dict[str, Any]:
    """
    Fetch the IDR a flags route acts on.
    Takes the IDR uuid.
    Returns the IDR dict; raises 404 if there is none.
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")
    return idr


def _response(idr: dict[str, Any], message: str) -> IdrFlagsResponse:
    """
    Read an IDR's flags into the response.
    Takes the IDR dict and the message.
    Returns an IdrFlagsResponse; raises 500 when the flags can't be read.
    """
    try:
        data = idr_flags(idr)
    except IdrFlagError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return IdrFlagsResponse(status="success", message=message, data=IdrFlags.model_validate(data))


def attribution_editor(idr_id: UUID, user: UserOut = Depends(no_demo_idr_flag_writes)) -> dict[str, Any]:
    """
    FastAPI dependency for excluding and including an IDR's flags and its curb segments (api/v1/idr_segments.py): its inspector while it is a draft (before it is submitted), the reviewer who accepted it at the stage it is in while it is in review, or an admin either way; never a demo user.
    Takes the idr_id path parameter and the signed-in user (refused when a demo user).
    Returns the IDR dict; raises 404 (no IDR), 409 (deleted, approved, or submitted and not yet accepted) and 403 (anyone else).
    """
    idr = load_idr(idr_id)
    if idr["deleted_at"] is not None:
        raise HTTPException(status_code=409, detail="IDR is deleted")
    if idr["status"] == "approved":
        raise HTTPException(status_code=409, detail="IDR is approved: its flags are claimed; unlock it to change them")
    if idr["status"] in _IN_REVIEW:
        stage_reviewer(idr_id, user)
        return idr
    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="A submitted IDR's flags change once a reviewer accepts it")
    if user.role != "admin" and idr["reporter_uuid"] != user.uuid:
        raise HTTPException(status_code=403, detail="Only the IDR's inspector can change its flags while it is a draft")
    return idr


def _project_flag(idr: dict[str, Any], flag_id: UUID) -> dict[str, Any]:
    """
    Fetch a flag an IDR's override names, which must be one of its project's and not claimed by another IDR.
    Takes the IDR dict and the flag's uuid.
    Returns the flag dict; raises 404 (no such flag in the IDR's project) and 409 (another IDR claimed it).
    """
    flag = get_flag(flag_id)
    if flag is None or flag["project_id"] != idr["project_id"]:
        raise HTTPException(status_code=404, detail="Flag not found in this IDR's project")
    if flag["claimed_by_idr_id"] is not None and flag["claimed_by_idr_id"] != idr["idr_id"]:
        number = flag["claimed_by_idr_number"] or "without a number"
        raise HTTPException(status_code=409, detail=f"Flag is claimed by IDR {number}")
    return flag


def _in_default_scope(idr: dict[str, Any], flag_id: UUID) -> bool:
    """
    Whether the IDR holds a flag by default (it is on one of its task order's properties).
    Takes the IDR dict and the flag's uuid.
    Returns True or False; raises 500 when it can't be told.
    """
    in_scope = in_default_scope(idr["idr_id"], flag_id)
    if in_scope is None:
        raise HTTPException(status_code=500, detail="Failed to read the IDR's task order")
    return in_scope


@router.get("/{idr_id}/attributed-flags", response_model=IdrFlagsResponse)
def get_idr_flags(idr_id: UUID) -> IdrFlagsResponse:
    """
    List the flags an IDR covers and what they come to per pay item. Before approval it is live: the live flags on its task order's properties and those included by an override, each attributed, excluded or claimed by another IDR, rolled up from the attributed ones. Once approved it is frozen: the flags it claimed, and the rollup it wrote at approval. For anyone who can read the IDR.
    Takes the IDR uuid as a path parameter.
    Returns an IdrFlagsResponse; raises 404 for an unknown IDR and 500 when the flags can't be read.
    """
    return _response(load_idr(idr_id), "IDR flags")


@router.post("/{idr_id}/attributed-flags/{flag_id}/exclude", response_model=IdrFlagsResponse)
def exclude_idr_flag(flag_id: UUID, idr: dict = Depends(attribution_editor),
                     user: UserOut = Depends(current_user)) -> IdrFlagsResponse:
    """
    Leave a flag out of an IDR: one on its task order's properties is excluded by an override; one it had included is dropped back out. Excluding one already out changes nothing.
    Takes the flag uuid as a path parameter, the IDR (loaded and checked by attribution_editor) and the signed-in user.
    Returns the IDR's flags (IdrFlagsResponse); raises 403, 404 and 409 as attribution_editor does, 404 (no such flag in its project) and 409 (another IDR claimed it).
    """
    _project_flag(idr, flag_id)
    if _in_default_scope(idr, flag_id):
        done = set_attribution(idr["idr_id"], flag_id, "exclude", user.uuid)
    else:
        done = clear_attribution(idr["idr_id"], flag_id)
    if done is None:
        raise HTTPException(status_code=500, detail="Failed to exclude the flag")
    return _response(idr, "Flag excluded")


@router.post("/{idr_id}/attributed-flags/{flag_id}/include", response_model=IdrFlagsResponse)
def include_idr_flag(flag_id: UUID, idr: dict = Depends(attribution_editor),
                     user: UserOut = Depends(current_user)) -> IdrFlagsResponse:
    """
    Put a flag back into an IDR: one on its task order's properties loses its exclusion; one from elsewhere in its project is included by an override. Including one already in changes nothing.
    Takes the flag uuid as a path parameter, the IDR (loaded and checked by attribution_editor) and the signed-in user.
    Returns the IDR's flags (IdrFlagsResponse); raises 403, 404 and 409 as attribution_editor does, 404 (no such flag in its project), 400 (a deleted flag, or one on a deleted page, PIR or property) and 409 (another IDR claimed it).
    """
    flag = _project_flag(idr, flag_id)
    if any(flag[key] is not None for key in ("deleted_at", "page_deleted_at", "pir_deleted_at", "property_deleted_at")):
        raise HTTPException(status_code=400, detail="Flag is deleted")
    if _in_default_scope(idr, flag_id):
        done = clear_attribution(idr["idr_id"], flag_id)
    else:
        done = set_attribution(idr["idr_id"], flag_id, "include", user.uuid)
    if done is None:
        raise HTTPException(status_code=500, detail="Failed to include the flag")
    return _response(idr, "Flag included")
