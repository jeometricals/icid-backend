import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from api.queries.ped_ramp_corners import get_corner
from api.queries.ped_ramp_tifs import (
    WRITTEN_COLUMNS,
    approve_tif,
    create_tif,
    delete_tif,
    list_inspection_tifs,
    update_tif,
)
from api.schemas.auth import UserOut
from api.schemas.ped_ramp_inspection import DownloadOut, DownloadResponse
from api.schemas.ped_ramp_tif import TifApprove, TifListResponse, TifOut, TifResponse, TifUpdate
from api.services.auth import current_admin, current_user, inspection_member, no_demo_property_writes, tif_member
from api.services.ped_ramp_exports import PedRampExportError, signed_download, store_tif, tif_download_name
from api.services.signatures import SignatureStorageError, snapshot_signature_for_tif
from api.v1._shared import (
    INSPECTION_APPROVED,
    SIGNATURE_REQUIRED,
    changes_sent,
    refuse_locked_corner,
    require_roles,
    validated,
)

logger = logging.getLogger(__name__)

# A second router under /v1/ped-ramp-inspections, for an inspection's TIFs
inspection_router = APIRouter(prefix="/v1/ped-ramp-inspections", tags=["Ped ramp TIFs"],
                              dependencies=[Depends(current_user)])
# One TIF by its id
router = APIRouter(prefix="/v1/ped-ramp-tifs", tags=["Ped ramp TIFs"], dependencies=[Depends(current_user)])

# Who: an inspector or an RE drafts and edits a TIF; only an RE signs and approves it; an admin does any of it and
# alone deletes one. The inspection's lock holds too: submitted, only the RE; approved, nobody
DRAFTERS = ("inspector", "re")
SIGNERS = ("re",)
TIF_APPROVED = "TIF is approved — no further edits"
ALREADY_DRAFTED = "This ramp already has a TIF on this inspection"
NOT_AT_CORNER = "Only a ramp at the inspection's corner takes a TIF"


def _writable(found: dict[str, Any], user: UserOut) -> None:
    """
    Refuse a change to a TIF the user can't make: an approved TIF, an approved inspection's, a submitted one's for a user who isn't its RE, or a user who is neither an inspector nor an RE.
    Takes the TIF dict and the signed-in user.
    Returns nothing; raises 409 for an approved TIF or inspection and 403 otherwise.
    """
    if found["status"] == "approved":
        raise HTTPException(status_code=409, detail=TIF_APPROVED)
    refuse_locked_corner(found["corner_id"], found["project_id"], user)
    require_roles(found["project_id"], user, DRAFTERS)


def _response(row: Optional[dict[str, Any]], message: str, lost: str) -> TifResponse:
    """
    Wrap one TIF in the response envelope, or refuse a statement that changed nothing.
    Takes the row (None when the statement changed nothing), the message and the 409's detail for None.
    Returns the TifResponse; raises 409 when the row is None.
    """
    if row is None:
        raise HTTPException(status_code=409, detail=lost)
    return TifResponse(status="success", message=message, data=TifOut.model_validate(row))


@inspection_router.get("/{inspection_id}/tifs", response_model=TifListResponse)
def list_tifs(found: dict = Depends(inspection_member)) -> TifListResponse:
    """
    List an inspection's TIFs, oldest first. For a user on its project, or an admin.
    Takes the inspection (loaded and checked by inspection_member from the inspection_id path parameter).
    Returns a TifListResponse; raises 403 for a user who isn't on its project, 404 for an unknown inspection and 500 if they can't be read.
    """
    rows = list_inspection_tifs(found["id"])
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch TIFs")
    return TifListResponse(status="success", message="TIFs retrieved", data=[TifOut.model_validate(row) for row in rows])


@inspection_router.post("/{inspection_id}/ramps/{property_id}/tif", response_model=TifResponse, status_code=201)
def draft_tif(
    property_id: UUID, found: dict = Depends(inspection_member), user: UserOut = Depends(no_demo_property_writes)
) -> TifResponse:
    """
    Draft a TIF for one ramp of an inspection, prefilled with the corner's DOT id and permittee and the ramp's label and construction. For an inspector or an RE on its project, or an admin; not a demo user; the inspection's lock holds (submitted, the RE only; approved, nobody).
    Takes the ramp's (property's) id as a path parameter, the inspection (loaded by inspection_member) and the signed-in user.
    Returns 201 with a TifResponse; raises 400 for a ramp that isn't at its corner, 403 for a demo user, a user without the role or a submitted inspection and a user who isn't its RE, 404 for an unknown inspection and 409 for an approved inspection or a ramp that already has a TIF on it.
    """
    refuse_locked_corner(found["corner_id"], found["project_id"], user)
    require_roles(found["project_id"], user, DRAFTERS)
    corner = get_corner(found["corner_id"])
    if corner is None or not any(str(ramp["property_id"]) == str(property_id) for ramp in corner["ramps"]):
        raise HTTPException(status_code=400, detail=NOT_AT_CORNER)
    return _response(create_tif(found["id"], property_id, user.uuid), "TIF drafted", ALREADY_DRAFTED)


@router.get("/{tif_id}", response_model=TifResponse)
def get_tif_record(found: dict = Depends(tif_member)) -> TifResponse:
    """
    Fetch one TIF. For a user on its project, or an admin.
    Takes the TIF (loaded and checked by tif_member from the tif_id path parameter).
    Returns a TifResponse; raises 403 for a user who isn't on its project and 404 for an unknown TIF.
    """
    return _response(found, "TIF retrieved", "")


@router.patch("/{tif_id}", response_model=TifResponse)
def update_tif_record(
    body: TifUpdate, found: dict = Depends(tif_member), user: UserOut = Depends(no_demo_property_writes)
) -> TifResponse:
    """
    Change some of a draft TIF, merged over it and written whole. A field left out keeps its value; a null clears a text or a choice and unticks a box. For an inspector or an RE on its project, or an admin; not a demo user; the inspection's lock holds.
    Takes the fields to change in the body, the TIF (loaded by tif_member) and the signed-in user.
    Returns a TifResponse; raises 400 when nothing is sent, 403 for a demo user, a user without the role or a submitted inspection and a user who isn't its RE, 404 for an unknown TIF, 409 for an approved TIF or inspection and 422 for an unknown or over-long value.
    """
    _writable(found, user)
    changes = changes_sent(body, set(WRITTEN_COLUMNS))
    merged = validated(TifUpdate, {**{column: found.get(column) for column in WRITTEN_COLUMNS}, **changes})
    return _response(update_tif(found["id"], merged.model_dump()), "TIF updated", TIF_APPROVED)


@router.post("/{tif_id}/approve", response_model=TifResponse)
def approve_tif_record(
    body: TifApprove, found: dict = Depends(tif_member), user: UserOut = Depends(no_demo_property_writes)
) -> TifResponse:
    """
    Sign and approve a draft TIF as the RE: their name and New York licence number as they print, and their current signature copied to the TIF first. For an RE on its project, or an admin.
    Takes the signer's name and licence in the body, the TIF (loaded by tif_member) and the signed-in user.
    Returns a TifResponse; raises 400 for a user with no signature, 403 for a demo user or a user who isn't an RE, 404 for an unknown TIF, 409 for an approved TIF or inspection, 422 for a blank name or licence and 502 when the signature can't be copied.
    """
    require_roles(found["project_id"], user, SIGNERS)
    if found["status"] == "approved":
        raise HTTPException(status_code=409, detail=TIF_APPROVED)
    if found["inspection_status"] == "approved":
        raise HTTPException(status_code=409, detail=INSPECTION_APPROVED)
    if user.signature_path is None:
        raise HTTPException(status_code=400, detail=SIGNATURE_REQUIRED)
    try:
        copy = snapshot_signature_for_tif(user.signature_path, found["id"])
    except SignatureStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    row = approve_tif(found["id"], user.uuid, body.signer_name, body.signer_ny_license_no, copy)
    return _response(row, "TIF approved", TIF_APPROVED)


@router.delete("/{tif_id}", status_code=204, response_class=Response)
def delete_tif_record(found: dict = Depends(tif_member), admin: UserOut = Depends(current_admin)) -> Response:
    """
    Delete a TIF for good, draft or approved, with its photos' rows; not once its inspection is approved. Admins only.
    Takes the TIF (loaded by tif_member) and the signed-in admin.
    Returns 204; raises 403 for a user who isn't an admin, 404 for an unknown TIF, 409 for an approved inspection's and 500 if it can't be deleted.
    """
    if found["inspection_status"] == "approved":
        raise HTTPException(status_code=409, detail=INSPECTION_APPROVED)
    result = delete_tif(found["id"])
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to delete TIF")
    if result["id"] is None:
        raise HTTPException(status_code=409, detail=INSPECTION_APPROVED)
    if result["photo_paths"]:
        logger.info("TIF %s deleted; its photo files stay in Storage: %s", found["id"], result["photo_paths"])
    logger.info("TIF deleted: tif_id=%s admin=%s at=%s", found["id"], admin.uuid, datetime.now(timezone.utc).isoformat())
    return Response(status_code=204)


@router.get("/{tif_id}/dot-pdf", response_model=DownloadResponse)
def download_tif_pdf(found: dict = Depends(tif_member)) -> DownloadResponse:
    """
    Fill this TIF's DOT form as it stands (an approved one with the RE's signature), store it and hand out a signed URL to it. For a user on its project, or an admin.
    Takes the TIF (loaded and checked by tif_member from the tif_id path parameter).
    Returns a DownloadResponse ({url, expires_at, file_name}); raises 403 for a user who isn't on its project, 404 for an unknown TIF and 502 when it can't be filled, stored or signed.
    """
    try:
        path = store_tif(found)
        data = signed_download(path, tif_download_name(found, get_corner(found["corner_id"]), found["id"]))
    except PedRampExportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DownloadResponse(status="success", message="TIF form ready", data=DownloadOut(**data))
