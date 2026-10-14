"""
A PIR's image: the scanned sheet, kept in the private pir-images bucket.

    pirs/{pir id}/uploads/{random}        a raw upload: the client PUTs the file there through a signed URL
    pirs/{pir id}/{random}.png | .jpg     the PIR's image; each upload gets a new path

Setting one takes two steps, like a signature: ask for a signed upload URL, PUT the file (PDF, PNG or JPEG) to it,
then complete. Completing reads the file back, checks what it is by its first bytes, renders a PDF's first page to
PNG, stores the image at a new path, points the PIR at it, and then removes the PIR's previous image and the raw
upload, best-effort. So the PIR always points at a file that exists.
"""

import io
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import pypdfium2 as pdfium

from api.core.config import (
    PIR_IMAGE_BUCKET_NAME,
    PIR_IMAGE_MAX_BYTES,
    PIR_IMAGE_RENDER_DPI,
    PIR_IMAGE_URL_EXPIRY_SECONDS,
)
from api.queries.pirs import clear_pir_image, set_pir_image
from api.services.attachments import SUPABASE_UPLOAD_URL_LIFETIME_SECONDS, remove_storage_files
from api.storage.client import create_signed_upload_url, create_signed_url, download_file, object_exists, upload_file

logger = logging.getLogger(__name__)

# The types an upload may declare and be; PDF points at 72 to the inch
UPLOAD_TYPES = ("application/pdf", "image/png", "image/jpeg")
PDF_POINTS_PER_INCH = 72


class PirImageError(Exception):
    """An image the request can't have: its status (400 or 413) and what to say."""

    def __init__(self, status_code: int, detail: str) -> None:
        """
        Record why the image was refused.
        Takes the HTTP status and the message.
        Returns nothing.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PirImageStorageError(Exception):
    """Storage couldn't do its part (sign, read or store a file): a 502."""


def _uploads_folder(pir_id: UUID) -> str:
    """
    Where a PIR's raw uploads go.
    Takes the PIR's id.
    Returns the folder path, with its trailing slash.
    """
    return f"pirs/{pir_id}/uploads/"


def sniff_type(data: bytes) -> Optional[str]:
    """
    Tell what a file is by its first bytes, whatever it was declared as.
    Takes the file's bytes.
    Returns 'application/pdf', 'image/png' or 'image/jpeg', or None for anything else.
    """
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def render_pdf_first_page(data: bytes, dpi: int = PIR_IMAGE_RENDER_DPI) -> bytes:
    """
    Render a PDF's first page to PNG.
    Takes the PDF's bytes and the resolution in dots per inch.
    Returns the PNG's bytes; raises PirImageError (400) for a PDF that can't be read or has no pages.
    """
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise PirImageError(400, "Could not read the PDF") from exc
    try:
        if len(document) == 0:
            raise PirImageError(400, "The PDF has no pages")
        image = document[0].render(scale=dpi / PDF_POINTS_PER_INCH).to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        document.close()


def request_pir_image_upload(pir_id: UUID, content_type: str) -> dict[str, Any]:
    """
    Start setting a PIR's image: sign a URL the client PUTs the file to, at a fresh path under the PIR's uploads.
    Takes the PIR's id and the file's declared type.
    Returns {upload_url, upload_path, expires_in}; raises PirImageError (400) for a type that isn't a PDF, PNG or JPEG, and PirImageStorageError if Storage can't sign one.
    """
    if content_type not in UPLOAD_TYPES:
        raise PirImageError(400, "Unsupported file type")
    path = f"{_uploads_folder(pir_id)}{secrets.token_hex(16)}"
    try:
        upload_url = create_signed_upload_url(path, PIR_IMAGE_BUCKET_NAME)
    except Exception as exc:
        logger.error("Storage could not sign a PIR image upload for %s: %s", path, exc)
        raise PirImageStorageError("Could not create an upload link") from exc
    return {"upload_url": upload_url, "upload_path": path, "expires_in": SUPABASE_UPLOAD_URL_LIFETIME_SECONDS}


def _read_upload(pir_id: UUID, upload_path: str) -> bytes:
    """
    Read a raw upload back from Storage, once it is known to be this PIR's and to be there.
    Takes the PIR's id and the upload's path.
    Returns its bytes; raises PirImageError (400) for a path that isn't one of the PIR's uploads or has no file, PirImageError (413) for one over PIR_IMAGE_MAX_BYTES, and PirImageStorageError when Storage fails.
    """
    folder = _uploads_folder(pir_id)
    name = upload_path[len(folder):] if upload_path.startswith(folder) else ""
    if not name or "/" in name or ".." in name:
        raise PirImageError(400, "upload_path is not an upload of this PIR")
    try:
        if not object_exists(PIR_IMAGE_BUCKET_NAME, upload_path):
            raise PirImageError(400, "Upload the file before completing")
        data = download_file(upload_path, PIR_IMAGE_BUCKET_NAME)
    except PirImageError:
        raise
    except Exception as exc:
        logger.error("Storage could not read the PIR image upload %s: %s", upload_path, exc)
        raise PirImageStorageError("Could not read the uploaded file") from exc
    if len(data) > PIR_IMAGE_MAX_BYTES:
        remove_storage_files([upload_path], PIR_IMAGE_BUCKET_NAME)
        raise PirImageError(413, "File too large")
    return data


def complete_pir_image_upload(pir_id: UUID, upload_path: str, scale: float) -> Optional[dict[str, Any]]:
    """
    Finish setting a PIR's image: read the upload, check what it is, render a PDF's first page to PNG, store the image at a new path, point the PIR at it with its scale, then remove the previous image and the upload, best-effort.
    Takes the PIR's id, the path the file was uploaded to and the scale (pixels per foot of the stored image).
    Returns the updated PIR dict (with previous_image_path), or None if the PIR is gone; raises PirImageError (400 for a file that isn't a PDF, PNG or JPEG or a PDF that can't be read, 413 for one too large) and PirImageStorageError when Storage fails.
    """
    data = _read_upload(pir_id, upload_path)
    kind = sniff_type(data)
    if kind is None:
        remove_storage_files([upload_path], PIR_IMAGE_BUCKET_NAME)
        raise PirImageError(400, "Unsupported file type")
    if kind == "application/pdf":
        try:
            data, kind = render_pdf_first_page(data), "image/png"
        except PirImageError:
            remove_storage_files([upload_path], PIR_IMAGE_BUCKET_NAME)
            raise

    path = f"pirs/{pir_id}/{secrets.token_hex(16)}.{'png' if kind == 'image/png' else 'jpg'}"
    try:
        upload_file(PIR_IMAGE_BUCKET_NAME, path, data, kind)
    except Exception as exc:
        logger.error("Storage could not store the PIR image %s: %s", path, exc)
        raise PirImageStorageError("Could not store the image") from exc

    try:
        row = set_pir_image(pir_id, path, scale)
    except Exception:
        remove_storage_files([path], PIR_IMAGE_BUCKET_NAME)
        raise
    if row is None:
        remove_storage_files([path], PIR_IMAGE_BUCKET_NAME)
        return None

    leftovers = [upload_path] + ([row["previous_image_path"]] if row["previous_image_path"] not in (None, path) else [])
    remove_storage_files(leftovers, PIR_IMAGE_BUCKET_NAME)
    return row


def remove_pir_image(pir_id: UUID) -> Optional[dict[str, Any]]:
    """
    Take a PIR's image away: clear its path and scale, then remove the file, best-effort. A PIR without one is left as it is.
    Takes the PIR's id.
    Returns the updated PIR dict (with previous_image_path), or None if no PIR matches.
    """
    row = clear_pir_image(pir_id)
    if row is not None and row["previous_image_path"]:
        remove_storage_files([row["previous_image_path"]], PIR_IMAGE_BUCKET_NAME)
    return row


def pir_image_signed_url(pir_id: UUID, path: str) -> dict[str, Any]:
    """
    Sign a URL to a PIR's image, good for PIR_IMAGE_URL_EXPIRY_SECONDS.
    Takes the PIR's id (for the file's name) and the image's Storage path.
    Returns {signed_url, expires_at}; raises PirImageStorageError when Storage can't sign one.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PIR_IMAGE_URL_EXPIRY_SECONDS)
    try:
        url = create_signed_url(path, PIR_IMAGE_URL_EXPIRY_SECONDS, f"pir-{pir_id}.{path.rsplit('.', 1)[-1]}",
                                PIR_IMAGE_BUCKET_NAME)
    except Exception as exc:
        logger.error("Storage could not sign the PIR image %s: %s", path, exc)
        raise PirImageStorageError("Could not create a link to the image") from exc
    return {"signed_url": url, "expires_at": expires_at}
