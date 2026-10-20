"""
Ped ramp photos (Pedramp Phase 3, S4): an inspection's and a TIF's photos, kept in the private pedramp-photos bucket.

    staging/{inspection or TIF id}/{uuid}.{ext}       a raw upload: the client PUTs the file there through a signed URL
    inspections/{inspection id}/{photo id}.jpg|.png   an inspection's photo
    tifs/{tif id}/{photo id}.jpg|.png                 a TIF's photo or its site layout

Uploading takes two steps, like a PIR's scan: ask for a signed upload URL, PUT the photo (JPEG, PNG or HEIC) to it,
then complete. Completing reads the file back, tells its type by its first bytes, converts a HEIC to JPEG, stores it
under its parent and adds its row in one statement that checks the form's cap and the number; the raw upload is then
removed, best-effort. The approval reads the photos back here for DOT's forms.
"""

import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from api.core.config import PEDRAMP_PHOTO_BUCKET_NAME, PEDRAMP_PHOTO_URL_EXPIRY_SECONDS
from api.queries.ped_ramp_photos import MAX_PHOTOS_PER_FORM, list_photos
from api.services.attachments import SUPABASE_UPLOAD_URL_LIFETIME_SECONDS, remove_storage_files
from api.storage.client import create_signed_upload_url, create_signed_url, download_file, object_exists, upload_file

register_heif_opener()  # lets Pillow open HEIC / HEIF photos

logger = logging.getLogger(__name__)

# The largest photo, as the bucket holds it (10 MB)
MAX_PHOTO_BYTES = 10 * 1024 * 1024
# The extensions a raw upload's name may have, and the one its staging file gets
UPLOAD_EXTENSIONS = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "heic": "heic", "heif": "heic"}
# The brands of an ISO media file's ftyp box that make it a HEIC / HEIF image
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
# A converted HEIC's JPEG quality
HEIC_JPEG_QUALITY = 90
# The parents' folders in the bucket
FOLDERS = {"inspection": "inspections", "tif": "tifs"}

UNSUPPORTED = "Unsupported file type: upload a JPEG, PNG or HEIC photo"
TOO_LARGE = "Photo too large: at most 10 MB"
FORM_FULL = f"A form takes at most {MAX_PHOTOS_PER_FORM} photos"
NOT_A_RAMP = "That ramp isn't at this inspection's corner"
LAYOUT_TAKEN = "This TIF already has a site layout"


def number_taken_message(photo_number: int) -> str:
    """
    Say that a photo number is already used.
    Takes the number.
    Returns the 409's detail.
    """
    return f"Photo number {photo_number} is already used on this form"


class PhotoError(Exception):
    """A photo the request can't have: its status (400, 409 or 413) and what to say."""

    def __init__(self, status_code: int, detail: str) -> None:
        """
        Record why the photo was refused.
        Takes the HTTP status and the message.
        Returns nothing.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PhotoStorageError(Exception):
    """Storage couldn't do its part (sign, read or store a file): a 502."""


def _staging_folder(parent_id: UUID) -> str:
    """
    Where a parent's raw uploads go.
    Takes the inspection's or TIF's id.
    Returns the folder path, with its trailing slash.
    """
    return f"staging/{parent_id}/"


def sniff_photo_type(data: bytes) -> Optional[str]:
    """
    Tell what a photo is by its first bytes, whatever it was declared as.
    Takes the file's bytes.
    Returns 'image/jpeg', 'image/png' or 'image/heic', or None for anything else.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[4:8] == b"ftyp" and data[8:12] in HEIF_BRANDS:
        return "image/heic"
    return None


def heic_to_jpeg(data: bytes) -> bytes:
    """
    Convert a HEIC photo to JPEG, turned upright by its EXIF.
    Takes the HEIC's bytes.
    Returns the JPEG's bytes; raises PhotoError (400) for a file that can't be read.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except Exception as exc:
        raise PhotoError(400, "Could not read the photo") from exc
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=HEIC_JPEG_QUALITY)
    return buffer.getvalue()


def stored_form(data: bytes) -> tuple[bytes, str, str]:
    """
    Turn an upload into the file kept: a JPEG or PNG as it is, a HEIC as a JPEG.
    Takes the file's bytes.
    Returns (bytes, content type, extension); raises PhotoError (400) for a file that isn't a JPEG, PNG or HEIC, or a HEIC that can't be read.
    """
    kind = sniff_photo_type(data)
    if kind == "image/jpeg":
        return data, kind, "jpg"
    if kind == "image/png":
        return data, kind, "png"
    if kind == "image/heic":
        return heic_to_jpeg(data), "image/jpeg", "jpg"
    raise PhotoError(400, UNSUPPORTED)


def request_photo_upload(parent_id: UUID, file_name: Optional[str]) -> dict[str, Any]:
    """
    Start a photo upload: sign a URL the client PUTs the photo to, at a fresh staging path.
    Takes the inspection's or TIF's id and the file's name (None when not known; its extension, if any, must be a photo's).
    Returns {upload_url, staging_path, expires_at}; raises PhotoError (400) for a name that isn't a photo's and PhotoStorageError if Storage can't sign one.
    """
    extension = "img"
    if file_name and "." in file_name:
        extension = UPLOAD_EXTENSIONS.get(file_name.rsplit(".", 1)[1].strip().lower(), "")
        if not extension:
            raise PhotoError(400, UNSUPPORTED)
    path = f"{_staging_folder(parent_id)}{uuid.uuid4().hex}.{extension}"
    try:
        upload_url = create_signed_upload_url(path, PEDRAMP_PHOTO_BUCKET_NAME)
    except Exception as exc:
        logger.error("Storage could not sign a ped ramp photo upload for %s: %s", path, exc)
        raise PhotoStorageError("Could not create an upload link") from exc
    # Supabase fixes a signed upload URL's lifetime; it can't be shortened from here
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SUPABASE_UPLOAD_URL_LIFETIME_SECONDS)
    return {"upload_url": upload_url, "staging_path": path, "expires_at": expires_at}


def _read_staged(parent_id: UUID, staging_path: str) -> bytes:
    """
    Read a raw upload back from Storage, once it is known to be this parent's and to be there.
    Takes the inspection's or TIF's id and the staging path.
    Returns its bytes; raises PhotoError (400) for a path that isn't one of the parent's uploads or has no file, PhotoError (413, the upload removed) for one over MAX_PHOTO_BYTES, and PhotoStorageError when Storage fails.
    """
    folder = _staging_folder(parent_id)
    name = staging_path[len(folder):] if staging_path.startswith(folder) else ""
    if not name or "/" in name or ".." in name:
        raise PhotoError(400, "storage_path is not an upload of this form")
    try:
        if not object_exists(PEDRAMP_PHOTO_BUCKET_NAME, staging_path):
            raise PhotoError(400, "Upload the photo before completing")
        data = download_file(staging_path, PEDRAMP_PHOTO_BUCKET_NAME)
    except PhotoError:
        raise
    except Exception as exc:
        logger.error("Storage could not read the ped ramp photo upload %s: %s", staging_path, exc)
        raise PhotoStorageError("Could not read the uploaded photo") from exc
    if len(data) > MAX_PHOTO_BYTES:
        remove_storage_files([staging_path], PEDRAMP_PHOTO_BUCKET_NAME)
        raise PhotoError(413, TOO_LARGE)
    return data


def _refusal(result: Optional[dict[str, Any]], photo_number: int, kind: str, locked: str) -> Optional[PhotoError]:
    """
    Read why the insert added no photo, from the checks its statement returned.
    Takes the statement's row (None when the parent was locked or gone), the number asked for, the kind and the 409's detail for a locked parent.
    Returns the PhotoError to raise, or None when the photo was added.
    """
    if result is None:
        return PhotoError(409, locked)
    if result.get("id") is not None:
        return None
    if not result["ramp_ok"]:
        return PhotoError(400, NOT_A_RAMP)
    if result["number_taken"]:
        return PhotoError(409, LAYOUT_TAKEN if kind == "layout" else number_taken_message(photo_number))
    if result["photo_count"] >= MAX_PHOTOS_PER_FORM:
        return PhotoError(400, FORM_FULL)
    return PhotoError(409, number_taken_message(photo_number))


def complete_photo_upload(
    parent: str, parent_id: UUID, staging_path: str, photo_number: int, kind: str, locked: str,
    insert: Callable[[UUID, str, str, int], Optional[dict[str, Any]]],
) -> dict[str, Any]:
    """
    Finish a photo upload: read the file, keep it as a JPEG or PNG under its parent, and add its row through the insert given; then remove the raw upload, best-effort. A refused row removes the stored file and keeps the upload, so it can be completed again with another number.
    Takes the parent's kind ('inspection' or 'tif') and id, the staging path, the number and kind asked for, the 409's detail for a locked parent, and the insert: (photo id, storage path, content type, byte size) -> the statement's row.
    Returns the new photo dict; raises PhotoError (400, 409 or 413) and PhotoStorageError when Storage fails.
    """
    data = _read_staged(parent_id, staging_path)
    try:
        stored, content_type, extension = stored_form(data)
    except PhotoError:
        remove_storage_files([staging_path], PEDRAMP_PHOTO_BUCKET_NAME)
        raise
    if len(stored) > MAX_PHOTO_BYTES:
        remove_storage_files([staging_path], PEDRAMP_PHOTO_BUCKET_NAME)
        raise PhotoError(413, TOO_LARGE)

    photo_id = uuid.uuid4()
    path = f"{FOLDERS[parent]}/{parent_id}/{photo_id}.{extension}"
    try:
        upload_file(PEDRAMP_PHOTO_BUCKET_NAME, path, stored, content_type)
    except Exception as exc:
        logger.error("Storage could not store the ped ramp photo %s: %s", path, exc)
        raise PhotoStorageError("Could not store the photo") from exc

    try:
        result = insert(photo_id, path, content_type, len(stored))
    except Exception:
        remove_storage_files([path], PEDRAMP_PHOTO_BUCKET_NAME)
        raise
    refusal = _refusal(result, photo_number, kind, locked)
    if refusal is not None:
        remove_storage_files([path], PEDRAMP_PHOTO_BUCKET_NAME)
        raise refusal

    remove_storage_files([staging_path], PEDRAMP_PHOTO_BUCKET_NAME)
    return result


def remove_photo_file(path: str) -> None:
    """
    Remove a deleted photo's file, best-effort.
    Takes its Storage path.
    Returns nothing; a failure is logged, the file left orphaned.
    """
    remove_storage_files([path], PEDRAMP_PHOTO_BUCKET_NAME)


def with_signed_url(photo: dict[str, Any]) -> dict[str, Any]:
    """
    Give a photo row its signed URL, good for PEDRAMP_PHOTO_URL_EXPIRY_SECONDS.
    Takes the photo dict.
    Returns it with image_url and image_url_expires_at; raises PhotoStorageError when Storage can't sign one.
    """
    path = photo["storage_path"]
    name = f"{photo['kind']}-{photo['photo_number']}.{path.rsplit('.', 1)[-1]}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PEDRAMP_PHOTO_URL_EXPIRY_SECONDS)
    try:
        url = create_signed_url(path, PEDRAMP_PHOTO_URL_EXPIRY_SECONDS, name, PEDRAMP_PHOTO_BUCKET_NAME)
    except Exception as exc:
        logger.error("Storage could not sign the ped ramp photo %s: %s", path, exc)
        raise PhotoStorageError("Could not create a link to the photo") from exc
    return {**photo, "image_url": url, "image_url_expires_at": expires_at}


def _fetch(photo: dict[str, Any]) -> Optional[bytes]:
    """
    Download one photo's file for a form.
    Takes the photo dict.
    Returns its bytes, or None when it can't be fetched (logged).
    """
    try:
        return download_file(photo["storage_path"], PEDRAMP_PHOTO_BUCKET_NAME)
    except Exception as exc:
        logger.warning("Ped ramp photo %s could not be fetched, left out: %s", photo["storage_path"], exc)
        return None


def inspection_photo_bytes(inspection_id: UUID) -> dict[str, list[tuple[int, Optional[str], bytes]]]:
    """
    Fetch an inspection's photos for DOT's inspection form, ramp by ramp; a file that can't be fetched is logged and left out.
    Takes the inspection's id.
    Returns {str(property_id): [(photo_number, caption, bytes), ...]} in number order; raises RuntimeError if the photos can't be listed.
    """
    rows = list_photos(inspection_id=inspection_id)
    if rows is None:
        raise RuntimeError("Failed to list the inspection's photos")
    photos: dict[str, list[tuple[int, Optional[str], bytes]]] = {}
    for row in rows:
        data = _fetch(row)
        if data is not None:
            photos.setdefault(str(row["property_id"]), []).append((row["photo_number"], row["caption"], data))
    return photos


def tif_photo_bytes(tif_id: UUID) -> tuple[list[tuple[int, Optional[str], bytes]], Optional[bytes]]:
    """
    Fetch a TIF's photos and its site layout for DOT's TIF form; a file that can't be fetched is logged and left out.
    Takes the TIF's id.
    Returns ([(photo_number, caption, bytes), ...] in number order, the layout's bytes or None); raises RuntimeError if the photos can't be listed.
    """
    rows = list_photos(tif_id=tif_id)
    if rows is None:
        raise RuntimeError("Failed to list the TIF's photos")
    photos: list[tuple[int, Optional[str], bytes]] = []
    layout: Optional[bytes] = None
    for row in rows:
        data = _fetch(row)
        if data is None:
            continue
        if row["kind"] == "layout":
            layout = data
        else:
            photos.append((row["photo_number"], row["caption"], data))
    return photos, layout
