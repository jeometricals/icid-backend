from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from api.queries.ped_ramp_inspections import (
    approve_inspection,
    create_inspection,
    get_corner_inspection,
    return_inspection,
    set_requirements_met,
    submit_inspection,
)
from api.queries.projects import get_user_roles_on_project
from api.schemas.auth import UserOut
from api.schemas.ped_ramp_inspection import (
    CornerInspectionResponse,
    DownloadOut,
    DownloadResponse,
    InspectionOut,
    InspectionResponse,
    InspectionReturn,
    InspectionUpdate,
)
from api.services.auth import corner_member, current_user, inspection_member, no_demo_property_writes
from api.services.ped_ramp_exports import (
    PedRampExportError,
    inspection_download_name,
    signed_download,
    store_inspection_package,
)
from api.services.signatures import SignatureStorageError, snapshot_signature_for_inspection
from api.v1._shared import INSPECTION_APPROVED, INSPECTION_SUBMITTED, SIGNATURE_REQUIRED, require_roles

# A second router under /v1/ped-ramp-corners, for a corner's inspection
corner_router = APIRouter(prefix="/v1/ped-ramp-corners", tags=["Ped ramp inspections"],
                          dependencies=[Depends(current_user)])
# One inspection by its id
router = APIRouter(prefix="/v1/ped-ramp-inspections", tags=["Ped ramp inspections"], dependencies=[Depends(current_user)])

# Who does what: an inspection is started and edited as a draft by the project's inspectors and REs, submitted by an
# inspector, and returned or approved by an RE; an admin does any of it
STARTERS = ("inspector", "re")
SUBMITTERS = ("inspector",)
REVIEWERS = ("re",)
ALREADY_STARTED = "This corner already has an inspection"
DRAFT_TIFS = "Approve or delete this inspection's draft TIFs first"


def _require_status(found: dict[str, Any], status: str, detail: str) -> None:
    """
    Refuse an inspection that isn't in the status a move starts from.
    Takes the inspection dict, the status it must be in and the 409's detail.
    Returns nothing; raises 409 otherwise.
    """
    if found["status"] != status:
        raise HTTPException(status_code=409, detail=detail)


def _signature_copy(found: dict[str, Any], user: UserOut, signer: str) -> str:
    """
    Copy the signing user's current signature to the inspection's own path, before the statement that signs it.
    Takes the inspection dict, the signed-in user and whose signature it is ('inspector' or 're').
    Returns the copy's path; raises 400 for a user with no signature and 502 when the copy fails.
    """
    if user.signature_path is None:
        raise HTTPException(status_code=400, detail=SIGNATURE_REQUIRED)
    try:
        return snapshot_signature_for_inspection(user.signature_path, found["id"], signer)
    except SignatureStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _response(row: Optional[dict[str, Any]], message: str, lost: str) -> InspectionResponse:
    """
    Wrap one inspection in the response envelope, or refuse a statement that changed nothing.
    Takes the row the statement returned (None when it changed nothing), the message and the 409's detail for None.
    Returns the InspectionResponse; raises 409 when the row is None (the inspection changed meanwhile).
    """
    if row is None:
        raise HTTPException(status_code=409, detail=lost)
    return InspectionResponse(status="success", message=message, data=InspectionOut.model_validate(row))


@corner_router.get("/{corner_id}/inspection", response_model=CornerInspectionResponse)
def get_corner_inspection_record(found: dict = Depends(corner_member)) -> CornerInspectionResponse:
    """
    Fetch a corner's inspection: its live one, else its latest approved one; null when none has been started. For a user on its project, or an admin.
    Takes the corner (loaded and checked by corner_member from the corner_id path parameter).
    Returns a CornerInspectionResponse; raises 403 for a user who isn't on its project and 404 for an unknown corner.
    """
    row = get_corner_inspection(found["corner_id"])
    data = InspectionOut.model_validate(row) if row else None
    return CornerInspectionResponse(status="success", message="Inspection retrieved" if row else "No inspection yet",
                                    data=data)


@corner_router.post("/{corner_id}/inspection/start", response_model=InspectionResponse, status_code=201)
def start_inspection(
    found: dict = Depends(corner_member), user: UserOut = Depends(no_demo_property_writes)
) -> InspectionResponse:
    """
    Start a corner's inspection as a draft. For an inspector or an RE on its project, or an admin; not a demo user. One inspection per corner for the pilot.
    Takes the corner (loaded and checked by corner_member) and the signed-in user.
    Returns 201 with an InspectionResponse; raises 400 for a deleted corner, 403 for a demo user or a user without the role, 404 for an unknown corner and 409 when the corner already has an inspection.
    """
    require_roles(found["project_id"], user, STARTERS)
    if found["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail="Corner is deleted")
    return _response(create_inspection(found["corner_id"], user.uuid), "Inspection started", ALREADY_STARTED)


@router.get("/{inspection_id}", response_model=InspectionResponse)
def get_inspection_record(found: dict = Depends(inspection_member)) -> InspectionResponse:
    """
    Fetch one inspection. For a user on its corner's project, or an admin.
    Takes the inspection (loaded and checked by inspection_member from the inspection_id path parameter).
    Returns an InspectionResponse; raises 403 for a user who isn't on its project and 404 for an unknown inspection.
    """
    return _response(found, "Inspection retrieved", "")


@router.patch("/{inspection_id}", response_model=InspectionResponse)
def update_inspection(
    body: InspectionUpdate, found: dict = Depends(inspection_member), user: UserOut = Depends(no_demo_property_writes)
) -> InspectionResponse:
    """
    Record the answer to "are all ADA requirements met?". As a draft, for an inspector or an RE on its project; submitted, for an RE; approved, for nobody. An admin stands in for either.
    Takes the answer in the body (null clears it), the inspection (loaded by inspection_member) and the signed-in user.
    Returns an InspectionResponse; raises 403 for a demo user, a user without the role or a submitted inspection and a user who isn't an RE, 404 for an unknown inspection and 409 for an approved one.
    """
    if found["status"] == "approved":
        raise HTTPException(status_code=409, detail=INSPECTION_APPROVED)
    if found["status"] == "submitted":
        if user.role != "admin" and "re" not in get_user_roles_on_project(user.uuid, found["project_id"]):
            raise HTTPException(status_code=403, detail=INSPECTION_SUBMITTED)
    else:
        require_roles(found["project_id"], user, STARTERS)
    return _response(set_requirements_met(found["id"], body.requirements_met), "Inspection updated", INSPECTION_APPROVED)


@router.post("/{inspection_id}/submit", response_model=InspectionResponse)
def submit_inspection_record(
    found: dict = Depends(inspection_member), user: UserOut = Depends(no_demo_property_writes)
) -> InspectionResponse:
    """
    Submit a draft inspection for the RE's review, signed by the submitting inspector: their current signature is copied to the inspection first. For an inspector on its project, or an admin.
    Takes the inspection (loaded by inspection_member) and the signed-in user.
    Returns an InspectionResponse; raises 400 for a user with no signature, 403 for a demo user or a user without the role, 404 for an unknown inspection, 409 for one that isn't a draft and 502 when the signature can't be copied.
    """
    require_roles(found["project_id"], user, SUBMITTERS)
    _require_status(found, "draft", "Only a draft inspection can be submitted")
    copy = _signature_copy(found, user, "inspector")
    return _response(submit_inspection(found["id"], user.uuid, copy), "Inspection submitted",
                     "Only a draft inspection can be submitted")


@router.post("/{inspection_id}/return", response_model=InspectionResponse)
def return_inspection_record(
    body: InspectionReturn, found: dict = Depends(inspection_member), user: UserOut = Depends(no_demo_property_writes)
) -> InspectionResponse:
    """
    Send a submitted inspection back to its inspector as a draft, with a comment (required). For an RE on its project, or an admin.
    Takes the comment in the body, the inspection (loaded by inspection_member) and the signed-in user.
    Returns an InspectionResponse; raises 403 for a demo user or a user without the role, 404 for an unknown inspection, 409 for one that isn't submitted and 422 for a blank comment.
    """
    require_roles(found["project_id"], user, REVIEWERS)
    _require_status(found, "submitted", "Only a submitted inspection can be returned")
    return _response(return_inspection(found["id"], user.uuid, body.comment), "Inspection returned",
                     "Only a submitted inspection can be returned")


@router.post("/{inspection_id}/approve", response_model=InspectionResponse)
def approve_inspection_record(
    found: dict = Depends(inspection_member), user: UserOut = Depends(no_demo_property_writes)
) -> InspectionResponse:
    """
    Approve a submitted inspection, signed by the approving RE: their current signature is copied to the inspection, and its DOT forms (the inspection form and each approved TIF) are filled and stored, before the statement that approves it. Refused while one of its TIFs is a draft. For an RE on its project, or an admin.
    Takes the inspection (loaded by inspection_member) and the signed-in user.
    Returns an InspectionResponse; raises 400 for a user with no signature, 403 for a demo user or a user without the role, 404 for an unknown inspection, 409 for one that isn't submitted or has a draft TIF and 502 when the signature can't be copied or the forms can't be filled or stored.
    """
    require_roles(found["project_id"], user, REVIEWERS)
    _require_status(found, "submitted", "Only a submitted inspection can be approved")
    if found["draft_tif_count"] > 0:
        raise HTTPException(status_code=409, detail=DRAFT_TIFS)
    copy = _signature_copy(found, user, "re")
    try:
        package = store_inspection_package(found)
    except PedRampExportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _response(approve_inspection(found["id"], user.uuid, copy, package), "Inspection approved",
                     "Only a submitted inspection with no draft TIF can be approved")


@router.get("/{inspection_id}/dot-pdf", response_model=DownloadResponse)
def download_dot_pdf(found: dict = Depends(inspection_member)) -> DownloadResponse:
    """
    Hand out a signed URL to an approved inspection's DOT forms as filed at approval: one PDF, or a ZIP of the form's copies and its TIFs. For a user on its project, or an admin.
    Takes the inspection (loaded and checked by inspection_member from the inspection_id path parameter).
    Returns a DownloadResponse ({url, expires_at, file_name}); raises 403 for a user who isn't on its project, 404 for an unknown inspection or one with no stored forms (not approved) and 502 when no link can be signed.
    """
    path = found["dot_pdf_path"]
    if found["status"] != "approved" or not path:
        raise HTTPException(status_code=404, detail="The DOT forms are filed when the inspection is approved")
    try:
        data = signed_download(path, inspection_download_name(found, path))
    except PedRampExportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DownloadResponse(status="success", message="DOT forms ready", data=DownloadOut(**data))
