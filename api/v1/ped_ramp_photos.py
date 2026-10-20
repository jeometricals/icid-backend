from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.ped_ramp_photos import (
    delete_photo,
    insert_inspection_photo,
    insert_tif_photo,
    is_ramp_of_inspection,
    list_photos,
    update_photo,
)
from api.queries.projects import get_user_roles_on_project
from api.schemas.auth import UserOut
from api.schemas.ped_ramp_photo import (
    InspectionPhotoCreate,
    InspectionPhotoUploadIn,
    PhotoListResponse,
    PhotoOut,
    PhotoResponse,
    PhotoUpdate,
    PhotoUploadOut,
    PhotoUploadResponse,
    TifPhotoCreate,
    TifPhotoUploadIn,
)
from api.services.auth import (
    current_user,
    inspection_member,
    no_demo_property_writes,
    ped_ramp_photo_member,
    photo_tif_member,
)
from api.services.ped_ramp_photos import (
    NOT_A_RAMP,
    PhotoError,
    PhotoStorageError,
    complete_photo_upload,
    number_taken_message,
    remove_photo_file,
    request_photo_upload,
    with_signed_url,
)
from api.v1._shared import INSPECTION_APPROVED, INSPECTION_SUBMITTED

# An inspection's photos, under /v1/ped-ramp-inspections (a second router there)
inspection_router = APIRouter(prefix="/v1/ped-ramp-inspections", tags=["Ped ramp photos"],
                              dependencies=[Depends(current_user)])
# A TIF's photos and site layout, under /v1/ped-ramp-tifs (a second router there)
tif_router = APIRouter(prefix="/v1/ped-ramp-tifs", tags=["Ped ramp photos"], dependencies=[Depends(current_user)])
# One photo by its id
router = APIRouter(prefix="/v1/ped-ramp-photos", tags=["Ped ramp photos"], dependencies=[Depends(current_user)])

TIF_APPROVED = "TIF is approved — no further edits"
NUMBER_REQUIRED = "photo_number is required"
LAYOUT_NUMBER = "The site layout is always number 1"


def _failure(exc: Exception) -> HTTPException:
    """
    Turn a photo failure into the response it calls for.
    Takes a PhotoError (its own status and message) or a PhotoStorageError (502).
    Returns the HTTPException to raise.
    """
    if isinstance(exc, PhotoError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    return HTTPException(status_code=502, detail=str(exc))


def _refuse_locked_inspection(status: str, project_id: str, user: UserOut) -> None:
    """
    Keep an inspection's photos as its status allows: anyone who may write changes a draft's; only the RE or an admin a submitted one's; nobody an approved one's.
    Takes the inspection's status, its project's id and the signed-in user.
    Returns nothing; raises 409 for an approved inspection and 403 for a submitted one and a user who isn't its RE.
    """
    if status == "approved":
        raise HTTPException(status_code=409, detail=INSPECTION_APPROVED)
    if status == "submitted" and user.role != "admin" and "re" not in get_user_roles_on_project(user.uuid, project_id):
        raise HTTPException(status_code=403, detail=INSPECTION_SUBMITTED)


def _refuse_locked_tif(tif_status: str, inspection_status: str) -> None:
    """
    Keep a TIF's photos while it is a draft and its inspection isn't approved.
    Takes the TIF's status and its inspection's.
    Returns nothing; raises 409 for an approved TIF or inspection.
    """
    if tif_status == "approved":
        raise HTTPException(status_code=409, detail=TIF_APPROVED)
    if inspection_status == "approved":
        raise HTTPException(status_code=409, detail=INSPECTION_APPROVED)


def _writable_inspection(found: dict = Depends(inspection_member),
                         user: UserOut = Depends(no_demo_property_writes)) -> dict:
    """
    FastAPI dependency for adding an inspection's photos: a member of its project (or an admin), not a demo user, and the inspection's lock.
    Takes the inspection (loaded and checked by inspection_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the inspection dict; raises 403 and 409 as _refuse_locked_inspection does.
    """
    _refuse_locked_inspection(found["status"], found["project_id"], user)
    return found


def _writable_tif(found: dict = Depends(photo_tif_member), user: UserOut = Depends(no_demo_property_writes)) -> dict:
    """
    FastAPI dependency for adding a TIF's photos: a member of its project (or an admin), not a demo user, on a draft TIF whose inspection isn't approved.
    Takes the TIF (loaded and checked by photo_tif_member) and the user (refused when a demo user).
    Returns the TIF dict; raises 409 as _refuse_locked_tif does.
    """
    _refuse_locked_tif(found["status"], found["inspection_status"])
    return found


def _writable_photo(found: dict = Depends(ped_ramp_photo_member), user: UserOut = Depends(no_demo_property_writes)) -> dict:
    """
    FastAPI dependency for changing one photo: a member of its project (or an admin), not a demo user, under its parent's lock (the TIF's, or the inspection's).
    Takes the photo (loaded and checked by ped_ramp_photo_member) and the user (refused when a demo user).
    Returns the photo dict; raises 403 and 409 as the parent's lock does.
    """
    if found["tif_id"] is not None:
        _refuse_locked_tif(found["tif_status"], found["inspection_status"])
    else:
        _refuse_locked_inspection(found["inspection_status"], found["project_id"], user)
    return found


def _signed(photos: list[dict[str, Any]]) -> list[PhotoOut]:
    """
    Give photos their signed URLs, as the responses carry them.
    Takes the photo dicts.
    Returns the PhotoOut models; raises 502 when Storage can't sign one.
    """
    try:
        return [PhotoOut.model_validate(with_signed_url(photo)) for photo in photos]
    except PhotoStorageError as exc:
        raise _failure(exc) from exc


def _upload_response(parent_id: UUID, file_name: Optional[str]) -> PhotoUploadResponse:
    """
    Sign a URL to PUT a photo to, in the parent's staging folder.
    Takes the inspection's or TIF's id and the file's name (None when not known).
    Returns the PhotoUploadResponse; raises 400 for a name that isn't a photo's and 502 if Storage can't sign one.
    """
    try:
        upload = request_photo_upload(parent_id, file_name)
    except (PhotoError, PhotoStorageError) as exc:
        raise _failure(exc) from exc
    return PhotoUploadResponse(status="success", message="Upload URL created", data=PhotoUploadOut(**upload))


def _list_response(rows: Optional[list[dict[str, Any]]]) -> PhotoListResponse:
    """
    Wrap a form's photos in the response envelope, each with its signed URL.
    Takes the photo dicts (None when they couldn't be read).
    Returns the PhotoListResponse; raises 500 for None and 502 if Storage can't sign a URL.
    """
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch photos")
    return PhotoListResponse(status="success", message="Photos retrieved", data=_signed(rows))


@inspection_router.post("/{inspection_id}/photos/upload-request", response_model=PhotoUploadResponse)
def request_inspection_photo_upload(
    inspection_id: UUID, body: InspectionPhotoUploadIn, found: dict = Depends(_writable_inspection)
) -> PhotoUploadResponse:
    """
    Start uploading a photo of one of an inspection's ramps: a signed URL to PUT a JPEG, PNG or HEIC to. For a member of its project (or an admin), not a demo user; a submitted inspection's only for its project's RE or an admin, an approved one's for nobody.
    Takes the inspection's id as a path parameter, the ramp's property_id and an optional file_name in the body, and the inspection (loaded by _writable_inspection).
    Returns a PhotoUploadResponse; raises 400 for a ramp that isn't a live ped ramp at the inspection's corner or a name that isn't a photo's, 403 for a demo user, a user off the project or a submitted inspection, 404 for an unknown inspection, 409 for an approved one and 502 if Storage can't sign a URL.
    """
    is_ramp = is_ramp_of_inspection(inspection_id, body.property_id)
    if is_ramp is None:
        raise HTTPException(status_code=500, detail="Failed to check the ramp")
    if not is_ramp:
        raise HTTPException(status_code=400, detail=NOT_A_RAMP)
    return _upload_response(inspection_id, body.file_name)


@inspection_router.post("/{inspection_id}/photos", response_model=PhotoResponse, status_code=201)
def add_inspection_photo(
    inspection_id: UUID, body: InspectionPhotoCreate, found: dict = Depends(_writable_inspection),
    user: UserOut = Depends(current_user),
) -> PhotoResponse:
    """
    Finish uploading an inspection's photo, once the file is PUT: it is checked by its first bytes, a HEIC converted to JPEG, stored, and its row added in one statement that checks the ramp, the cap and the number. Same access as the upload request.
    Takes the inspection's id as a path parameter, the staging path, the ramp's property_id, the photo_number and an optional caption in the body, the inspection and the user.
    Returns a PhotoResponse (201); raises 400 for a path that isn't one of its uploads or has no file, a file that isn't a JPEG, PNG or HEIC, a ramp not at its corner, or a form with 20 photos; 409 for a taken number or an inspection approved meanwhile; 413 for a photo over 10 MB; 403 and 404 as on the upload request; and 502 when Storage fails.
    """
    try:
        row = complete_photo_upload(
            "inspection", inspection_id, body.storage_path, body.photo_number, "photo", INSPECTION_APPROVED,
            lambda photo_id, path, content_type, size: insert_inspection_photo(
                photo_id, inspection_id, body.property_id, body.photo_number, body.caption, path, content_type, size,
                user.uuid,
            ),
        )
    except (PhotoError, PhotoStorageError) as exc:
        raise _failure(exc) from exc
    return PhotoResponse(status="success", message="Photo added", data=_signed([row])[0])


@inspection_router.get("/{inspection_id}/photos", response_model=PhotoListResponse)
def list_inspection_photos(found: dict = Depends(inspection_member)) -> PhotoListResponse:
    """
    List an inspection's photos, every ramp's, by number, each with a fresh signed URL. For a member of its project, or an admin.
    Takes the inspection (loaded and checked by inspection_member from the inspection_id path parameter).
    Returns a PhotoListResponse (empty when it has none); raises 403 for a user off the project, 404 for an unknown inspection, 500 if the photos can't be read and 502 if Storage can't sign a URL.
    """
    return _list_response(list_photos(inspection_id=found["id"]))


@tif_router.post("/{tif_id}/photos/upload-request", response_model=PhotoUploadResponse)
def request_tif_photo_upload(
    tif_id: UUID, body: TifPhotoUploadIn, found: dict = Depends(_writable_tif)
) -> PhotoUploadResponse:
    """
    Start uploading a TIF's photo or site layout: a signed URL to PUT a JPEG, PNG or HEIC to. For a member of its project (or an admin), not a demo user, while the TIF is a draft and its inspection isn't approved.
    Takes the TIF's id as a path parameter, an optional file_name and kind in the body, and the TIF (loaded by _writable_tif).
    Returns a PhotoUploadResponse; raises 400 for a name that isn't a photo's, 403 for a demo user or a user off the project, 404 for an unknown TIF, 409 for an approved TIF or inspection and 502 if Storage can't sign a URL.
    """
    return _upload_response(tif_id, body.file_name)


@tif_router.post("/{tif_id}/photos", response_model=PhotoResponse, status_code=201)
def add_tif_photo(
    tif_id: UUID, body: TifPhotoCreate, found: dict = Depends(_writable_tif), user: UserOut = Depends(current_user)
) -> PhotoResponse:
    """
    Finish uploading a TIF's photo (kind 'photo', numbered) or its site layout (kind 'layout', one a TIF, numbered 1), once the file is PUT, as an inspection's photo is finished. Same access as the upload request.
    Takes the TIF's id as a path parameter, the staging path, the kind, the photo_number (required for a photo; a layout's is 1) and an optional caption in the body, the TIF and the user.
    Returns a PhotoResponse (201); raises 400 for a photo without a number, a path that isn't one of its uploads or has no file, a file that isn't a JPEG, PNG or HEIC, or a TIF with 20 photos; 409 for a taken number, a second layout or a TIF or inspection approved meanwhile; 413 for a photo over 10 MB; 403 and 404 as on the upload request; and 502 when Storage fails.
    """
    if body.kind == "layout":
        number = 1
    elif body.photo_number is None:
        raise HTTPException(status_code=400, detail=NUMBER_REQUIRED)
    else:
        number = body.photo_number
    try:
        row = complete_photo_upload(
            "tif", tif_id, body.storage_path, number, body.kind, TIF_APPROVED,
            lambda photo_id, path, content_type, size: insert_tif_photo(
                photo_id, tif_id, body.kind, number, body.caption, path, content_type, size, user.uuid,
            ),
        )
    except (PhotoError, PhotoStorageError) as exc:
        raise _failure(exc) from exc
    message = "Site layout added" if body.kind == "layout" else "Photo added"
    return PhotoResponse(status="success", message=message, data=_signed([row])[0])


@tif_router.get("/{tif_id}/photos", response_model=PhotoListResponse)
def list_tif_photos(found: dict = Depends(photo_tif_member)) -> PhotoListResponse:
    """
    List a TIF's photos by number, then its site layout, each with a fresh signed URL. For a member of its project, or an admin.
    Takes the TIF (loaded and checked by photo_tif_member from the tif_id path parameter).
    Returns a PhotoListResponse (empty when it has none); raises 403 for a user off the project, 404 for an unknown TIF, 500 if the photos can't be read and 502 if Storage can't sign a URL.
    """
    return _list_response(list_photos(tif_id=found["id"]))


@router.patch("/{photo_id}", response_model=PhotoResponse)
def edit_photo(photo_id: UUID, body: PhotoUpdate, found: dict = Depends(_writable_photo)) -> PhotoResponse:
    """
    Renumber a photo or change its caption (null or blank clears it). Under its parent's lock, as adding one is.
    Takes the photo's id as a path parameter, photo_number and caption (either or both) in the body, and the photo (loaded by _writable_photo).
    Returns a PhotoResponse with the photo as it now is; raises 400 for renumbering a site layout, 403 for a demo user, a user off the project or a submitted inspection, 404 for an unknown photo or one deleted meanwhile, and 409 for a number another photo of the form has or a locked parent.
    """
    sent = body.model_fields_set
    if found["kind"] == "layout" and body.photo_number not in (None, 1):
        raise HTTPException(status_code=400, detail=LAYOUT_NUMBER)
    try:
        row = update_photo(photo_id, body.photo_number, "caption" in sent, body.caption)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    if row["id"] is None:
        raise HTTPException(status_code=409, detail=number_taken_message(body.photo_number))
    return PhotoResponse(status="success", message="Photo updated", data=_signed([row])[0])


@router.delete("/{photo_id}", status_code=204, response_class=Response)
def remove_photo(photo_id: UUID, found: dict = Depends(_writable_photo)) -> None:
    """
    Delete a photo: its row, then its file, best-effort. Under its parent's lock, as adding one is.
    Takes the photo's id as a path parameter and the photo (loaded by _writable_photo).
    Returns nothing (204); raises 403 for a demo user, a user off the project or a submitted inspection, 404 for an unknown photo or one deleted meanwhile, and 409 for a locked parent.
    """
    row = delete_photo(photo_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    remove_photo_file(row["storage_path"])
