"""
Signatures: a user's current signature, and the copy an IDR keeps from the moment it is submitted.

Files live in the private signatures bucket (SIGNATURE_BUCKET_NAME):
    users/{user uuid}/signature.png                 the user's current signature; a new upload replaces it
    idrs/{idr id}/inspector_{random}.png            the copy stamped on one IDR at submit; never replaced or removed

Setting a signature takes two steps, like an attachment: ask for a signed upload URL, PUT the PNG to it, then
confirm, which records the file on the user's row. Submitting copies the user's current file to the IDR's own path
first, so a signature changed later doesn't change what was signed.
"""

import logging
from typing import Any, Optional
from uuid import UUID, uuid4

from api.core.config import SIGNATURE_BUCKET_NAME
from api.queries.users import set_user_signature
from api.services.attachments import SUPABASE_UPLOAD_URL_LIFETIME_SECONDS
from api.storage.client import copy_file, create_signed_upload_url, object_exists

logger = logging.getLogger(__name__)


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


def idr_signature_path(idr_id: UUID) -> str:
    """
    Make a fresh object path for an IDR's own copy of its inspector's signature. Each call gives a new one, so a copy is never written over another.
    Takes the IDR's uuid.
    Returns the path within the signatures bucket.
    """
    return f"idrs/{idr_id}/inspector_{uuid4().hex}.png"


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


def snapshot_signature_for_idr(signature_path: str, idr_id: UUID) -> str:
    """
    Copy a user's current signature file to an IDR's own path, ahead of submitting that IDR.
    Takes the signature's object path and the IDR's uuid.
    Returns the copy's object path; raises SignatureStorageError if the copy fails.
    """
    copy_path = idr_signature_path(idr_id)
    try:
        copy_file(SIGNATURE_BUCKET_NAME, signature_path, copy_path)
    except Exception as exc:
        logger.error("Storage could not copy signature %s to %s: %s", signature_path, copy_path, exc)
        raise SignatureStorageError("Could not copy the signature for this IDR") from exc
    return copy_path
