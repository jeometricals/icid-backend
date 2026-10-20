"""
Signatures: a user's current signature, and the copy an IDR keeps from the moment it is submitted.

Files live in the private signatures bucket (SIGNATURE_BUCKET_NAME):
    users/{user uuid}/signature.png                 the user's current signature; a new upload replaces it, and it is
                                                    removed when an admin deletes the user for good
    idrs/{idr id}/inspector_{random}.png            the copy stamped on one IDR at submit; never replaced or removed
    idrs/{idr id}/re_{random}.png                   the RE's copy, stamped at final approval; the same rule
    ped-ramp-inspections/{id}/inspector_{random}.png  a ped ramp inspection's copies, at submit and at approval
    ped-ramp-inspections/{id}/re_{random}.png         (Pedramp Phase 3); the same rule
    ped-ramp-tifs/{id}/re_{random}.png                a TIF's copy of the RE's, at its approval; the same rule

Setting a signature takes two steps, like an attachment: ask for a signed upload URL, PUT the PNG to it, then
confirm, which records the file on the user's row. Submitting copies the user's current file to the IDR's own path
first, so a signature changed later doesn't change what was signed.
"""

import logging
from typing import Any, Optional
from uuid import UUID, uuid4

from api.core.config import SIGNATURE_BUCKET_NAME
from api.queries.users import set_user_signature
from api.services.attachments import SUPABASE_UPLOAD_URL_LIFETIME_SECONDS, remove_storage_files
from api.storage.client import copy_file, create_signed_upload_url, download_file, object_exists

logger = logging.getLogger(__name__)


def fetch_signature_file(path: str) -> bytes:
    """
    Fetch a signature file's bytes from the signatures bucket: an IDR's copy, for the documents that print it (the IDR export, the Construction Report).
    Takes the object path.
    Returns the bytes; raises the Storage client's error, or a timeout, when it can't be fetched.
    """
    return download_file(path, SIGNATURE_BUCKET_NAME)


class SignatureError(Exception):
    """Base for signature failures the endpoints turn into HTTP errors."""


class SignatureNotUploadedError(SignatureError):
    """Confirm was called but no file is at the user's signature path."""


class SignatureStorageError(SignatureError):
    """Storage could not be reached or refused the request."""


def user_signature_path(user_uuid: UUID) -> str:
    """
    Give the object path of a user's current signature.
    Takes the user's uuid.
    Returns the path within the signatures bucket.
    """
    return f"users/{user_uuid}/signature.png"


def idr_signature_path(idr_id: UUID, signer: str = "inspector") -> str:
    """
    Make a fresh object path for an IDR's own copy of a signature. Each call gives a new one, so a copy is never written over another.
    Takes the IDR's uuid and whose signature it is ('inspector' or 're').
    Returns the path within the signatures bucket.
    """
    return f"idrs/{idr_id}/{signer}_{uuid4().hex}.png"


def request_user_signature_upload(user_uuid: UUID) -> dict[str, Any]:
    """
    Start setting a user's signature: sign a URL the client uploads the PNG to, at the user's signature path (replacing any file already there).
    Takes the user's uuid.
    Returns {upload_url, storage_path, expires_in}; raises SignatureStorageError if Storage can't sign one.
    """
    path = user_signature_path(user_uuid)
    try:
        upload_url = create_signed_upload_url(path, SIGNATURE_BUCKET_NAME, upsert=True)
    except Exception as exc:
        logger.error("Storage could not sign a signature upload for %s: %s", path, exc)
        raise SignatureStorageError("Could not create an upload link") from exc
    # Supabase fixes a signed upload URL's lifetime; it can't be shortened from here
    return {"upload_url": upload_url, "storage_path": path, "expires_in": SUPABASE_UPLOAD_URL_LIFETIME_SECONDS}


def confirm_user_signature(user_uuid: UUID, signature_type: str) -> Optional[dict[str, Any]]:
    """
    Finish setting a user's signature once its file is uploaded: check the file is there, then record it on the user's row.
    Takes the user's uuid and how the signature was made ('drawn' or 'uploaded').
    Returns the updated user dict, or None if the user's row wasn't updated; raises SignatureNotUploadedError when no file is there and SignatureStorageError when Storage can't be asked.
    """
    path = user_signature_path(user_uuid)
    try:
        uploaded = object_exists(SIGNATURE_BUCKET_NAME, path)
    except Exception as exc:
        logger.error("Storage could not check for the signature at %s: %s", path, exc)
        raise SignatureStorageError("Could not check the uploaded signature") from exc
    if not uploaded:
        raise SignatureNotUploadedError("Upload the signature before confirming")
    return set_user_signature(user_uuid, path, signature_type)


def _snapshot(signature_path: str, copy_path: str, of_what: str) -> str:
    """
    Copy a user's current signature file to the path of the document that keeps it.
    Takes the signature's object path, the copy's path and what the copy is for ("this IDR"), for the error.
    Returns the copy's object path; raises SignatureStorageError if the copy fails.
    """
    try:
        copy_file(SIGNATURE_BUCKET_NAME, signature_path, copy_path)
    except Exception as exc:
        logger.error("Storage could not copy signature %s to %s: %s", signature_path, copy_path, exc)
        raise SignatureStorageError(f"Could not copy the signature for {of_what}") from exc
    return copy_path


def snapshot_signature_for_idr(signature_path: str, idr_id: UUID, signer: str = "inspector") -> str:
    """
    Copy a user's current signature file to an IDR's own path, ahead of submitting or approving that IDR.
    Takes the signature's object path, the IDR's uuid and whose signature it is ('inspector' or 're').
    Returns the copy's object path; raises SignatureStorageError if the copy fails.
    """
    return _snapshot(signature_path, idr_signature_path(idr_id, signer), "this IDR")


def snapshot_signature_for_inspection(signature_path: str, inspection_id: UUID, signer: str = "inspector") -> str:
    """
    Copy a user's current signature file to a ped ramp inspection's own fresh path, ahead of submitting or approving it.
    Takes the signature's object path, the inspection's uuid and whose signature it is ('inspector' or 're').
    Returns the copy's object path; raises SignatureStorageError if the copy fails.
    """
    copy_path = f"ped-ramp-inspections/{inspection_id}/{signer}_{uuid4().hex}.png"
    return _snapshot(signature_path, copy_path, "this inspection")


def snapshot_signature_for_tif(signature_path: str, tif_id: UUID) -> str:
    """
    Copy the RE's current signature file to a TIF's own fresh path, ahead of approving it.
    Takes the signature's object path and the TIF's uuid.
    Returns the copy's object path; raises SignatureStorageError if the copy fails.
    """
    return _snapshot(signature_path, f"ped-ramp-tifs/{tif_id}/re_{uuid4().hex}.png", "this TIF")


def remove_user_signature(user_uuid: UUID) -> None:
    """
    Remove a user's current signature file from Storage, best-effort, once the user has been deleted for good. The copies IDRs keep are not touched.
    Takes the user's uuid.
    Returns nothing; a file that can't be removed is logged at WARNING and left orphaned. No file there is fine.
    """
    remove_storage_files([user_signature_path(user_uuid)], SIGNATURE_BUCKET_NAME)
