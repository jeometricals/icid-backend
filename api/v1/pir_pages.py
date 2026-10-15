from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.pir_annotations import get_annotations, upsert_annotations
from api.queries.pir_pages import list_pages_for_pir, update_page_rotation, update_page_scale
from api.queries.pirs import get_pir
from api.schemas.auth import UserOut
from api.schemas.pir_annotation import PirAnnotationIn, PirAnnotationOut, PirAnnotationResponse
from api.schemas.pir import PirImage, PirImageResponse, PirImageScale, PirOut, PirResponse
from api.schemas.pir_page import (
    PirPageListResponse,
    PirPageOut,
    PirPageResponse,
    PirPageRotationIn,
    PirPageScaleIn,
    PirPageUploadCompleteIn,
    PirPageUploadCompleteOut,
    PirPageUploadCompleteResponse,
    PirPageUploadRequestIn,
    PirPageUploadRequestOut,
    PirPageUploadRequestResponse,
)
from api.services.auth import current_user, no_demo_pir_writes, pir_member, pir_page_member
from api.services.pir_image import (
    PirImageError,
    PirImageStorageError,
    complete_pir_upload,
    remove_pir_pages,
    request_pir_upload,
    with_signed_url,
)
from api.v1._shared import (
    PAGE_CHAIN,
    PAGE_DELETED,
    refuse_deleted_chain,
    refuse_deleted_pir_for_member,
    writable_pir,
)

# A PIR's pages and their upload, under /v1/pirs (a third router there); pir_member keeps demo users to their project
pir_router = APIRouter(prefix="/v1/pirs", tags=["PIR pages"], dependencies=[Depends(current_user)])
# One page by its id
router = APIRouter(prefix="/v1/pir-pages", tags=["PIR pages"], dependencies=[Depends(current_user)])

NO_IMAGE = "No image for this PIR"


def _writable_page(found: dict = Depends(pir_page_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for the writes to one page: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the page (loaded and checked by pir_page_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the page dict; raises 403 for a demo user.
    """
    return found


def _failure(exc: Exception) -> HTTPException:
    """
    Turn an upload failure into the response it calls for.
    Takes a PirImageError (its own status and message) or a PirImageStorageError (502).
    Returns the HTTPException to raise.
    """
    if isinstance(exc, PirImageError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    return HTTPException(status_code=502, detail=str(exc))


def _positive(scale: Optional[Decimal]) -> Optional[Decimal]:
    """
    Check a scale.
    Takes the scale in pixels per foot (None for none).
    Returns it; raises 400 when it is set and isn't above 0.
    """
    if scale is not None and scale <= 0:
        raise HTTPException(status_code=400, detail="scale must be a positive number")
    return scale


def _signed(pages: list[dict[str, Any]]) -> list[PirPageOut]:
    """
    Give pages their signed URLs, as the responses carry them.
    Takes the page dicts.
    Returns the PirPageOut models; raises 502 when Storage can't sign one.
    """
    try:
        return [PirPageOut.model_validate(with_signed_url(page)) for page in pages]
    except PirImageStorageError as exc:
        raise _failure(exc) from exc


def _page_response(row: Optional[dict[str, Any]], message: str) -> PirPageResponse:
    """
    Wrap one changed page in the response envelope, with its signed URL.
    Takes the page dict (None when the page was replaced meanwhile) and the message.
    Returns the PirPageResponse; raises 400 "Page is deleted" for None.
    """
    if row is None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    return PirPageResponse(status="success", message=message, data=_signed([row])[0])


def _first_page(pir_id: UUID) -> dict[str, Any]:
    """
    Fetch a PIR's live page 1, for the deprecated R1d routes.
    Takes the PIR's id.
    Returns the page dict; raises 404 "No image for this PIR" when it has no pages, and 500 if they can't be read.
    """
    pages = list_pages_for_pir(pir_id)
    if pages is None:
        raise HTTPException(status_code=500, detail="Failed to fetch pages")
    if not pages:
        raise HTTPException(status_code=404, detail=NO_IMAGE)
    return pages[0]


def _delete_pages(pir_id: UUID, user: UserOut) -> None:
    """
    Take a PIR's pages away (their live flags soft-deleted with them).
    Takes the PIR's id and the user deleting them.
    Returns nothing; raises 500 if the statement fails.
    """
    if remove_pir_pages(pir_id, user.uuid) is None:
        raise HTTPException(status_code=500, detail="Failed to delete pages")


@pir_router.post("/{pir_id}/image/upload-request", response_model=PirPageUploadRequestResponse)
def request_upload(
    pir_id: UUID, body: PirPageUploadRequestIn, found: dict = Depends(writable_pir),
    user: UserOut = Depends(current_user),
) -> PirPageUploadRequestResponse:
    """
    Start uploading a PIR's scan: a signed URL to PUT the file (a PDF, PNG or JPEG) to, at a fresh staging path. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted PIR, or a PIR of a soft-deleted property.
    Takes the PIR's id as a path parameter, the file's mime (or R1d's content_type), name and size in the body, the PIR (loaded by writable_pir) and the user.
    Returns a PirPageUploadRequestResponse; raises 400 for no type, another type ("Unsupported file type") or a deleted PIR or property, 413 for a size over the limit, 403 for a demo user or a user who isn't on its project, 404 for an unknown PIR and 502 if Storage can't sign a URL.
    """
    refuse_deleted_pir_for_member(found, user)
    mime = body.mime or body.content_type
    if not mime:
        raise HTTPException(status_code=400, detail="mime is required")
    try:
        upload = request_pir_upload(pir_id, mime, body.size_bytes)
    except (PirImageError, PirImageStorageError) as exc:
        raise _failure(exc) from exc
    return PirPageUploadRequestResponse(
        status="success", message="Upload URL created", data=PirPageUploadRequestOut(**upload)
    )


@pir_router.post("/{pir_id}/image/complete", response_model=PirPageUploadCompleteResponse)
def complete_upload(
    pir_id: UUID, body: PirPageUploadCompleteIn, found: dict = Depends(writable_pir),
    user: UserOut = Depends(current_user),
) -> PirPageUploadCompleteResponse:
    """
    Finish uploading a PIR's scan, once the file is PUT: every page of a PDF (or the one image) is rendered to PNG within the caps and stored, and the PIR's pages are replaced by them, its live flags on the old pages soft-deleted in the same statement. Same access as the upload request.
    Takes the PIR's id as a path parameter, the staging path (or R1d's upload_path) and an optional scale for every page (scale_px_per_ft, or R1d's scale) in the body, the PIR and the user.
    Returns a PirPageUploadCompleteResponse: the PIR with its new pages (each with a signed URL) and page 1's link; raises 400 for a missing path, a scale that isn't above 0, a path that isn't one of the PIR's uploads or has no file, a file that isn't a PDF, PNG or JPEG ("Unsupported file type"), one that can't be read, one over a cap (the message names it), or a deleted PIR or property; 413 for a file too large; 403 and 404 as on the upload request; and 502 when Storage fails.
    """
    refuse_deleted_pir_for_member(found, user)
    path = body.staging_path or body.upload_path
    if not path:
        raise HTTPException(status_code=400, detail="staging_path is required")
    legacy_scale = None if body.scale is None else Decimal(str(body.scale))
    scale = _positive(body.scale_px_per_ft if body.scale_px_per_ft is not None else legacy_scale)
    try:
        rows = complete_pir_upload(pir_id, path, scale, user.uuid)
    except (PirImageError, PirImageStorageError) as exc:
        raise _failure(exc) from exc
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to save the pages")

    pages = _signed(rows)
    pir = get_pir(pir_id)
    if pir is None:
        raise HTTPException(status_code=404, detail="PIR not found")
    return PirPageUploadCompleteResponse(
        status="success",
        message=f"PIR image set: {len(pages)} page{'s' if len(pages) != 1 else ''}",
        data=PirPageUploadCompleteOut(
            **PirOut.model_validate(pir).model_dump(), pages=pages,
            signed_url=pages[0].image_url, expires_at=pages[0].image_url_expires_at,
        ),
    )


@pir_router.get("/{pir_id}/pages", response_model=PirPageListResponse)
def list_pages(found: dict = Depends(pir_member)) -> PirPageListResponse:
    """
    List a PIR's live pages, in order, each with a fresh signed URL. For a user on its project, or an admin.
    Takes the PIR (loaded and checked by pir_member from the pir_id path parameter).
    Returns a PirPageListResponse (empty when it has none); raises 403 for a user who isn't on its project, 404 for an unknown PIR, 500 if the pages can't be read and 502 if Storage can't sign a URL.
    """
    rows = list_pages_for_pir(found["id"])
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch pages")
    return PirPageListResponse(status="success", message="Pages retrieved", data=_signed(rows))


@pir_router.delete("/{pir_id}/pages", status_code=204, response_class=Response)
def delete_pages(pir_id: UUID, found: dict = Depends(writable_pir), user: UserOut = Depends(current_user)) -> None:
    """
    Take a PIR's pages away: they and their live flags are soft-deleted in one statement, their files kept. A PIR without pages is left as it is. Same access as the upload request.
    Takes the PIR's id as a path parameter, the PIR (loaded by writable_pir) and the user.
    Returns nothing (204); raises 400 for a deleted PIR or property, 403 for a demo user or a user who isn't on its project, 404 for an unknown PIR and 500 if the statement fails.
    """
    refuse_deleted_pir_for_member(found, user)
    _delete_pages(pir_id, user)


@router.patch("/{pir_page_id}/scale", response_model=PirPageResponse)
def set_page_scale(
    pir_page_id: UUID, body: PirPageScaleIn, found: dict = Depends(_writable_page), user: UserOut = Depends(current_user)
) -> PirPageResponse:
    """
    Set one page's scale (recalibrating), leaving the other pages alone. For a user on its project, or an admin; not a demo user. Only an admin changes a page whose PIR or property is soft-deleted; nobody changes a replaced page.
    Takes the page's id as a path parameter, the scale in pixels per foot in the body, the page (loaded by _writable_page) and the user.
    Returns a PirPageResponse with the page as it now is; raises 400 for a scale that isn't above 0 or a deleted page, PIR or property, 403 for a demo user or a user who isn't on its project, and 404 for an unknown page.
    """
    refuse_deleted_chain(found, user, PAGE_CHAIN)
    return _page_response(update_page_scale(pir_page_id, _positive(body.scale_px_per_ft)), "Page scale updated")


@router.patch("/{pir_page_id}/rotation", response_model=PirPageResponse)
def set_page_rotation(
    pir_page_id: UUID, body: PirPageRotationIn, found: dict = Depends(_writable_page),
    user: UserOut = Depends(current_user),
) -> PirPageResponse:
    """
    Set one page's rotation, a quarter turn at a time. Same access as the scale.
    Takes the page's id as a path parameter, the rotation (0, 90, 180 or 270) in the body, the page and the user.
    Returns a PirPageResponse with the page as it now is; raises 400 for a deleted page, PIR or property, 403 and 404 as for the scale, and 422 for another angle.
    """
    refuse_deleted_chain(found, user, PAGE_CHAIN)
    return _page_response(update_page_rotation(pir_page_id, body.rotation_deg), "Page rotation updated")


@router.get("/{pir_page_id}/annotations", response_model=PirAnnotationResponse)
def get_page_annotations(found: dict = Depends(pir_page_member)) -> PirAnnotationResponse:
    """
    Fetch a page's title block. For a user on its project, or an admin.
    Takes the page (loaded and checked by pir_page_member from the pir_page_id path parameter).
    Returns a PirAnnotationResponse, its data None when the page has none yet; raises 403 for a user who isn't on its project and 404 for an unknown page.
    """
    row = get_annotations(found["pir_page_id"])
    return PirAnnotationResponse(
        status="success", message="Annotations retrieved" if row else "No annotations for this page",
        data=None if row is None else PirAnnotationOut.model_validate(row),
    )


@router.put("/{pir_page_id}/annotations", response_model=PirAnnotationResponse)
def put_page_annotations(
    pir_page_id: UUID, body: PirAnnotationIn, found: dict = Depends(_writable_page), user: UserOut = Depends(current_user)
) -> PirAnnotationResponse:
    """
    Set a page's title block, every field at once (a field left out is cleared). Same access as the scale.
    Takes the page's id as a path parameter, the title block in the body, the page (loaded by _writable_page) and the user.
    Returns a PirAnnotationResponse with the title block as it now is; raises 400 for a deleted page, PIR or property, 403 and 404 as for the scale, and 422 for a value that doesn't fit.
    """
    refuse_deleted_chain(found, user, PAGE_CHAIN)
    row = upsert_annotations(pir_page_id, body.model_dump())
    if row is None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    return PirAnnotationResponse(status="success", message="Annotations saved", data=PirAnnotationOut.model_validate(row))


# ---------------------------------------------------------------------------------------------------------------------
# DEPRECATED (R1e): R1d's single-image routes, kept as thin wrappers over page 1 for the deployed image card.
# Remove in R1f, with source_image_url / source_image_scale on PIR responses.
# ---------------------------------------------------------------------------------------------------------------------


@pir_router.get("/{pir_id}/image", response_model=PirImageResponse, deprecated=True)
def get_pir_image(found: dict = Depends(pir_member)) -> PirImageResponse:
    """
    DEPRECATED (R1e, remove in R1f): a signed link to a PIR's page 1, with its scale. For a user on its project, or an admin.
    Takes the PIR (loaded and checked by pir_member from the pir_id path parameter).
    Returns a PirImageResponse; raises 403 for a user who isn't on its project, 404 for an unknown PIR or one with no pages, and 502 if Storage can't sign a URL.
    """
    page = _signed([_first_page(found["id"])])[0]
    return PirImageResponse(
        status="success", message="PIR image link created",
        data=PirImage(signed_url=page.image_url, expires_at=page.image_url_expires_at, scale=page.scale_px_per_ft),
    )


@pir_router.patch("/{pir_id}/image", response_model=PirResponse, deprecated=True)
def update_pir_image_scale(
    pir_id: UUID, body: PirImageScale, found: dict = Depends(writable_pir), user: UserOut = Depends(current_user)
) -> PirResponse:
    """
    DEPRECATED (R1e, remove in R1f): set the scale of a PIR's page 1. Same access as the upload request.
    Takes the PIR's id as a path parameter, the scale (pixels per foot) in the body, the PIR and the user.
    Returns a PirResponse with the PIR as it now is; raises 400 for a scale that isn't above 0 or a deleted PIR or property, 403 for a demo user or a user who isn't on its project, and 404 for an unknown PIR or one with no pages.
    """
    refuse_deleted_pir_for_member(found, user)
    scale = _positive(Decimal(str(body.scale)))
    if update_page_scale(_first_page(pir_id)["pir_page_id"], scale) is None:
        raise HTTPException(status_code=404, detail=NO_IMAGE)
    pir = get_pir(pir_id)
    if pir is None:
        raise HTTPException(status_code=404, detail="PIR not found")
    return PirResponse(status="success", message="PIR image scale updated", data=PirOut.model_validate(pir))


@pir_router.delete("/{pir_id}/image", status_code=204, response_class=Response, deprecated=True)
def delete_pir_image(pir_id: UUID, found: dict = Depends(writable_pir), user: UserOut = Depends(current_user)) -> None:
    """
    DEPRECATED (R1e, remove in R1f): take a PIR's pages away, as DELETE /v1/pirs/{pir_id}/pages does. Same access.
    Takes the PIR's id as a path parameter, the PIR and the user.
    Returns nothing (204); raises as DELETE /v1/pirs/{pir_id}/pages does.
    """
    refuse_deleted_pir_for_member(found, user)
    _delete_pages(pir_id, user)
