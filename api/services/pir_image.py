"""
A PIR's scan, as pages, kept in the private pir-images bucket.

    staging/{pir id}/{uuid}.{pdf|png|jpg}             a raw upload: the client PUTs the file there through a signed URL
    pirs/{pir id}/{upload uuid}/page-{n}.png          the PIR's pages, one PNG each; each upload gets a new folder

Uploading takes two steps, like a signature: ask for a signed upload URL, PUT the file (PDF, PNG or JPEG) to it, then
complete. Completing reads the file back, checks what it is by its first bytes, renders every page of a PDF (or the
one image) to PNG within the caps, stores the pages in a new folder, and then, in one statement, replaces the PIR's
pages (its live flags and pages are soft-deleted; the new pages go in). The old pages' files are kept: flags that
were on them keep their history. The raw upload is removed, best-effort.
"""

import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import pypdfium2 as pdfium
from PIL import Image

from api.core.config import (
    PIR_IMAGE_BUCKET_NAME,
    PIR_IMAGE_MAX_BYTES,
    PIR_IMAGE_RENDER_DPI,
    PIR_IMAGE_URL_EXPIRY_SECONDS,
    PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE,
    PIR_UPLOAD_MAX_PAGES,
)
from api.queries.pir_pages import delete_pages_for_pir, replace_pages
from api.services.attachments import SUPABASE_UPLOAD_URL_LIFETIME_SECONDS, remove_storage_files
from api.storage.client import create_signed_upload_url, create_signed_url, download_file, object_exists, upload_file

logger = logging.getLogger(__name__)

# The types an upload may be, and the extension its staging file gets; PDF points are 72 to the inch
UPLOAD_EXTENSIONS = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}
PDF_POINTS_PER_INCH = 72
PAGE_MIME = "image/png"


class PirImageError(Exception):
    """An upload the request can't have: its status (400 or 413) and what to say."""

    def __init__(self, status_code: int, detail: str) -> None:
        """
        Record why the upload was refused.
        Takes the HTTP status and the message.
        Returns nothing.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PirImageStorageError(Exception):
    """Storage couldn't do its part (sign, read or store a file): a 502."""


def _staging_folder(pir_id: UUID) -> str:
    """
    Where a PIR's raw uploads go.
    Takes the PIR's id.
    Returns the folder path, with its trailing slash.
    """
    return f"staging/{pir_id}/"


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


def _check_megapixels(width: float, height: float, what: str) -> None:
    """
    Refuse a page larger than PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE.
    Takes its size in pixels and how to name it in the message.
    Returns nothing; raises PirImageError (400) naming the cap when it is over.
    """
    megapixels = width * height / 1_000_000
    if megapixels > PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE:
        raise PirImageError(
            400,
            f"{what} is {megapixels:.1f} megapixels; a page can be at most {PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE:g} "
            "(PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE)",
        )


def _png(image: Image.Image) -> bytes:
    """
    Encode an image as PNG.
    Takes the PIL image.
    Returns the PNG's bytes.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_pdf_pages(data: bytes, dpi: int = PIR_IMAGE_RENDER_DPI) -> list[tuple[bytes, int, int]]:
    """
    Render every page of a PDF to PNG, after checking the caps: at most PIR_UPLOAD_MAX_PAGES pages, each at most PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE at the resolution.
    Takes the PDF's bytes and the resolution in dots per inch.
    Returns each page as (PNG bytes, width px, height px), in order; raises PirImageError (400) for a PDF that can't be read, has no pages, or trips a cap (the message names it).
    """
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise PirImageError(400, "Could not read the PDF") from exc
    try:
        count = len(document)
        if count == 0:
            raise PirImageError(400, "The PDF has no pages")
        if count > PIR_UPLOAD_MAX_PAGES:
            raise PirImageError(
                400, f"The PDF has {count} pages; a PIR can have at most {PIR_UPLOAD_MAX_PAGES} (PIR_UPLOAD_MAX_PAGES)"
            )
        zoom = dpi / PDF_POINTS_PER_INCH
        for index in range(count):
            width, height = document[index].get_size()
            _check_megapixels(width * zoom, height * zoom, f"Page {index + 1} at {dpi} DPI")
        pages = []
        for index in range(count):
            image = document[index].render(scale=zoom).to_pil()
            pages.append((_png(image), image.width, image.height))
        return pages
    finally:
        document.close()


def image_to_page(data: bytes) -> tuple[bytes, int, int]:
    """
    Turn an uploaded PNG or JPEG into the PIR's one page: a PNG is kept as it is, a JPEG converted to PNG; within the megapixel cap.
    Takes the image's bytes.
    Returns (PNG bytes, width px, height px); raises PirImageError (400) for an image that can't be read or is over the cap.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise PirImageError(400, "Could not read the image") from exc
    _check_megapixels(image.width, image.height, "The image")
    png = data if image.format == "PNG" else _png(image.convert("RGB") if image.mode not in ("RGB", "L") else image)
    return png, image.width, image.height


def request_pir_upload(pir_id: UUID, mime: str, size_bytes: Optional[int] = None) -> dict[str, Any]:
    """
    Start a PIR upload: sign a URL the client PUTs the file to, at a fresh staging path.
    Takes the PIR's id, the file's type and, when known, its size.
    Returns {upload_url, staging_path, expires_at, upload_path, expires_in}; raises PirImageError (400 for a type that isn't a PDF, PNG or JPEG, 413 for a size over PIR_IMAGE_MAX_BYTES) and PirImageStorageError if Storage can't sign one.
    """
    extension = UPLOAD_EXTENSIONS.get(mime)
    if extension is None:
        raise PirImageError(400, "Unsupported file type")
    if size_bytes is not None and size_bytes > PIR_IMAGE_MAX_BYTES:
        raise PirImageError(413, "File too large")
    path = f"{_staging_folder(pir_id)}{uuid.uuid4().hex}.{extension}"
    try:
        upload_url = create_signed_upload_url(path, PIR_IMAGE_BUCKET_NAME)
    except Exception as exc:
        logger.error("Storage could not sign a PIR upload for %s: %s", path, exc)
        raise PirImageStorageError("Could not create an upload link") from exc
    # Supabase fixes a signed upload URL's lifetime; it can't be shortened from here
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SUPABASE_UPLOAD_URL_LIFETIME_SECONDS)
    return {
        "upload_url": upload_url, "staging_path": path, "expires_at": expires_at,
        "upload_path": path, "expires_in": SUPABASE_UPLOAD_URL_LIFETIME_SECONDS,
    }


def _read_staged(pir_id: UUID, staging_path: str) -> bytes:
    """
    Read a raw upload back from Storage, once it is known to be this PIR's and to be there.
    Takes the PIR's id and the staging path.
    Returns its bytes; raises PirImageError (400) for a path that isn't one of the PIR's uploads or has no file, PirImageError (413) for one over PIR_IMAGE_MAX_BYTES, and PirImageStorageError when Storage fails.
    """
    folder = _staging_folder(pir_id)
    name = staging_path[len(folder):] if staging_path.startswith(folder) else ""
    if not name or "/" in name or ".." in name:
        raise PirImageError(400, "staging_path is not an upload of this PIR")
    try:
        if not object_exists(PIR_IMAGE_BUCKET_NAME, staging_path):
            raise PirImageError(400, "Upload the file before completing")
        data = download_file(staging_path, PIR_IMAGE_BUCKET_NAME)
    except PirImageError:
        raise
    except Exception as exc:
        logger.error("Storage could not read the PIR upload %s: %s", staging_path, exc)
        raise PirImageStorageError("Could not read the uploaded file") from exc
    if len(data) > PIR_IMAGE_MAX_BYTES:
        remove_storage_files([staging_path], PIR_IMAGE_BUCKET_NAME)
        raise PirImageError(413, "File too large")
    return data


def _render(data: bytes) -> list[tuple[bytes, int, int]]:
    """
    Turn an upload into its pages, by what its first bytes say it is.
    Takes the file's bytes.
    Returns each page as (PNG bytes, width px, height px); raises PirImageError (400) for a file that isn't a PDF, PNG or JPEG, or that can't be read or trips a cap.
    """
    kind = sniff_type(data)
    if kind is None:
        raise PirImageError(400, "Unsupported file type")
    return render_pdf_pages(data) if kind == "application/pdf" else [image_to_page(data)]


def complete_pir_upload(
    pir_id: UUID, staging_path: str, scale_px_per_ft: Optional[Decimal], user_uuid: UUID
) -> Optional[list[dict[str, Any]]]:
    """
    Finish a PIR upload: read the file, render its pages, store them in a new folder, and replace the PIR's pages with them (its live flags and pages soft-deleted in the same statement); then remove the raw upload, best-effort.
    Takes the PIR's id, the staging path, the scale for every page (None for none) and the uuid of the user uploading.
    Returns the new page dicts, or None if the statement failed; raises PirImageError (400 or 413, the upload removed) and PirImageStorageError when Storage fails.
    """
    data = _read_staged(pir_id, staging_path)
    try:
        rendered = _render(data)
    except PirImageError:
        remove_storage_files([staging_path], PIR_IMAGE_BUCKET_NAME)
        raise

    folder = f"pirs/{pir_id}/{uuid.uuid4().hex}"
    stored: list[str] = []
    pages: list[dict[str, Any]] = []
    try:
        for number, (png, width, height) in enumerate(rendered, start=1):
            path = f"{folder}/page-{number}.png"
            upload_file(PIR_IMAGE_BUCKET_NAME, path, png, PAGE_MIME)
            stored.append(path)
            pages.append({"page_number": number, "image_path": path, "image_mime": PAGE_MIME, "width_px": width,
                          "height_px": height, "scale_px_per_ft": scale_px_per_ft})
    except Exception as exc:
        logger.error("Storage could not store the pages of %s: %s", staging_path, exc)
        remove_storage_files(stored, PIR_IMAGE_BUCKET_NAME)
        raise PirImageStorageError("Could not store the pages") from exc

    try:
        rows = replace_pages(pir_id, pages, user_uuid)
    except Exception:
        remove_storage_files(stored, PIR_IMAGE_BUCKET_NAME)
        raise
    if rows is None:
        remove_storage_files(stored, PIR_IMAGE_BUCKET_NAME)
        return None

    remove_storage_files([staging_path], PIR_IMAGE_BUCKET_NAME)
    return rows


def remove_pir_pages(pir_id: UUID, user_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Take a PIR's pages away: its live flags and pages are soft-deleted in one statement. The files are kept, for the flags' history.
    Takes the PIR's id and the uuid of the user deleting them.
    Returns the pages deleted (empty when it had none), or None on failure.
    """
    return delete_pages_for_pir(pir_id, user_uuid)


def signed_page_url(path: str, download_name: str) -> dict[str, Any]:
    """
    Sign a URL to a page's PNG, good for PIR_IMAGE_URL_EXPIRY_SECONDS.
    Takes the page's Storage path and the name the browser should save it as.
    Returns {url, expires_at}; raises PirImageStorageError when Storage can't sign one.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PIR_IMAGE_URL_EXPIRY_SECONDS)
    try:
        url = create_signed_url(path, PIR_IMAGE_URL_EXPIRY_SECONDS, download_name, PIR_IMAGE_BUCKET_NAME)
    except Exception as exc:
        logger.error("Storage could not sign the PIR page %s: %s", path, exc)
        raise PirImageStorageError("Could not create a link to the image") from exc
    return {"url": url, "expires_at": expires_at}


def with_signed_url(page: dict[str, Any]) -> dict[str, Any]:
    """
    Give a page row its signed URL.
    Takes the page dict.
    Returns it with image_url and image_url_expires_at; raises PirImageStorageError when Storage can't sign one.
    """
    link = signed_page_url(page["image_path"], f"pir-{page['pir_id']}-page-{page['page_number']}.png")
    return {**page, "image_url": link["url"], "image_url_expires_at": link["expires_at"]}
