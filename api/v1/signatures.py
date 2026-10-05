from fastapi import APIRouter, Depends, HTTPException

from api.schemas.auth import UserOut
from api.schemas.signatures import ConfirmRequest, UploadRequest, UploadResponse
from api.services.auth import current_user, no_demo_signatures
from api.services.signatures import (
    SignatureNotUploadedError,
    SignatureStorageError,
    confirm_user_signature,
    request_user_signature_upload,
)

# Every route needs a signed-in user who isn't a demo user
router = APIRouter(
    prefix="/v1/signatures", tags=["Signatures"], dependencies=[Depends(current_user), Depends(no_demo_signatures)]
)


@router.post("/upload-request", response_model=UploadResponse)
def request_signature_upload(body: UploadRequest, user: UserOut = Depends(current_user)) -> UploadResponse:
    """
    Start setting the signed-in user's signature: returns a signed URL to PUT the PNG to.
    Takes an UploadRequest (content_type must be image/png) and the signed-in user.
    Returns an UploadResponse; raises 403 (demo user), 422 (not a PNG) and 502 (Storage).
    """
    try:
        return UploadResponse(**request_user_signature_upload(user.uuid))
    except SignatureStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/confirm", response_model=UserOut)
def confirm_signature(body: ConfirmRequest, user: UserOut = Depends(current_user)) -> UserOut:
    """
    Finish setting the signed-in user's signature once the PNG is uploaded: records it on their user row.
    Takes a ConfirmRequest (how the signature was made) and the signed-in user.
    Returns the updated user; raises 400 (nothing uploaded), 403 (demo user), 500 and 502 (Storage).
    """
    try:
        row = confirm_user_signature(user.uuid, body.signature_type)
    except SignatureNotUploadedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SignatureStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save signature")

    return UserOut.model_validate(row)
