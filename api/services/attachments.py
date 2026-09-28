import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from psycopg.errors import ForeignKeyViolation

from api.core.config import STORAGE_UPLOAD_URL_EXPIRY_SECONDS, STORAGE_URL_EXPIRY_SECONDS
from api.queries.report_attachments import (
    delete_attachment_row,
    insert_attachment,
    list_attachments_for_report,
    list_storage_paths_for_report_tree,
    mark_attachment_uploaded,
    update_attachment_metadata_row,
)
from api.storage.client import create_signed_upload_url, create_signed_url, remove_files

logger = logging.getLogger(__name__)

# Largest file accepted, matching the bucket limit and chk_report_attachments_size.
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

# Accepted Content-Type values. The declared type is trusted; the bucket's own
# image/* + application/pdf restriction is the fallback. SVG is left out on purpose:
# it is an image/* type that can carry script.
ALLOWED_FILE_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
    "image/heif",
    "application/pdf",
})


MAX_FILE_NAME_LENGTH = 200

# Limits matching chk_report_attachments_name and chk_report_attachments_description.
MAX_ATTACHMENT_NAME_LENGTH = 200
MAX_ATTACHMENT_DESCRIPTION_LENGTH = 2000

# Stands in for a file name with nothing usable left after sanitizing.
UNNAMED = "unnamed"

# Default name of the uploaded_by foreign key (migrations/008_report_attachments.sql).
UPLOADED_BY_FK = "report_attachments_uploaded_by_fkey"

UPLOADER_NOT_FOUND = "Uploader user not found."


class AttachmentError(Exception):
    """Base for attachment requests the service refuses; str(exc) is the client-facing message."""


class AttachmentNotAllowedOnAutoGeneralError(AttachmentError):
    """The target report is an auto-generated General, which is read-only."""


class EmptyFileError(AttachmentError):
    """The declared file size is zero or less."""


class FileTooLargeError(AttachmentError):
    """The declared file size is over MAX_FILE_SIZE_BYTES."""


class UnsupportedFileTypeError(AttachmentError):
    """The declared file type is not in ALLOWED_FILE_TYPES."""


class InvalidAttachmentMetadataError(AttachmentError):
    """attachment_name or attachment_description is blank or too long."""


class InvalidUserError(AttachmentError):
    """uploaded_by is not a user in icid.users."""


class AttachmentNotUploadedError(AttachmentError):
    """The attachment is still pending: upload-complete has not been called for it."""


class IdrNotDraftError(AttachmentError):
    """The attachment's IDR is not a draft, so its attachments can no longer be edited."""


class StorageUnavailableError(AttachmentError):
    """Supabase Storage refused or failed a signed-URL request."""


def sanitize_file_name(file_name: Optional[str]) -> str:
    """
    Make an uploaded file name safe to use in a Storage key.
    Takes the name as uploaded; drops any directory part, replaces every character outside A-Z a-z 0-9 . _ - with "_", trims dots around the stem, and caps it at 200 characters keeping the extension.
    Returns the safe name: "unnamed" if nothing usable is left (".." or "///"), "unnamed.jpg" for an extension-only name (".jpg").
    """
    base = re.split(r"[\\/]", file_name or "")[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base)

    stem, dot, ext = safe.rpartition(".")
    if not dot:
        stem, ext = safe, ""

    stem = stem.strip(".") or UNNAMED

    if ext and len(ext) <= 10:
        return stem[: MAX_FILE_NAME_LENGTH - len(ext) - 1] + "." + ext

    return (f"{stem}.{ext}" if ext else stem)[:MAX_FILE_NAME_LENGTH]


def build_storage_path(report_id: UUID, attachment_id: UUID, file_name: Optional[str]) -> str:
    """
    Build the Storage key for an attachment.
    Takes the report uuid, the attachment uuid and the uploaded file name.
    Returns "{report_id}/{attachment_id}_{sanitized file name}".
    """
    return f"{report_id}/{attachment_id}_{sanitize_file_name(file_name)}"


def normalize_file_type(content_type: Optional[str]) -> str:
    """
    Reduce a Content-Type header to its bare, lower-case MIME type.
    Takes the header value, e.g. "image/JPEG; charset=binary".
    Returns e.g. "image/jpeg", or "" if there was none.
    """
    return (content_type or "").split(";")[0].strip().lower()


def _check_not_auto_generated(report: dict[str, Any]) -> None:
    """
    Refuse attachments on an auto-generated General, which is read-only.
    Takes the target report row.
    Returns nothing; raises AttachmentNotAllowedOnAutoGeneralError if report is_auto_generated.
    """
    if report["is_auto_generated"]:
        raise AttachmentNotAllowedOnAutoGeneralError(
            "Attachments cannot be added to auto-generated General reports; edit child reports instead."
        )


def validate_upload(report: dict[str, Any], file_type: str, size: int) -> None:
    """
    Check that a file may be attached to a report.
    Takes the target report row, the normalized MIME type and the declared file size in bytes.
    Returns nothing; raises AttachmentNotAllowedOnAutoGeneralError, EmptyFileError, FileTooLargeError or UnsupportedFileTypeError.
    """
    _check_not_auto_generated(report)

    if size <= 0:
        raise EmptyFileError("File is empty")

    if size > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError("File is larger than the 10 MB limit")

    if file_type not in ALLOWED_FILE_TYPES:
        raise UnsupportedFileTypeError(
            "Unsupported file type. Allowed: JPEG, PNG, GIF, WebP, HEIC/HEIF images and PDF."
        )


def validate_attachment_metadata(attachment_name: str, attachment_description: str) -> tuple[str, str]:
    """
    Check an attachment's name and description against the table's CHECK constraints.
    Takes the name and description as sent; surrounding whitespace is trimmed.
    Returns the trimmed (name, description); raises InvalidAttachmentMetadataError if either is blank or too long.
    """
    name = attachment_name.strip()
    description = attachment_description.strip()

    if not name:
        raise InvalidAttachmentMetadataError("attachment_name must not be blank")

    if len(name) > MAX_ATTACHMENT_NAME_LENGTH:
        raise InvalidAttachmentMetadataError("attachment_name must be at most 200 characters")

    if not description:
        raise InvalidAttachmentMetadataError("attachment_description must not be blank")

    if len(description) > MAX_ATTACHMENT_DESCRIPTION_LENGTH:
        raise InvalidAttachmentMetadataError("attachment_description must be at most 2000 characters")

    return name, description


def upload_request(
    report: dict[str, Any],
    uploaded_by: UUID,
    file_name: str,
    file_type: str,
    file_size_bytes: int,
    attachment_name: str,
    attachment_description: str,
) -> Optional[dict[str, Any]]:
    """
    Start a two-step upload: validate the file details, sign an upload URL for a new Storage path, and record the attachment as pending.
    Takes the target report row, the uploader's uuid, the file's name, MIME type and size, and the attachment's name and description.
    Returns the pending attachment row plus upload_url, upload_url_expires_at and upload_headers (the Content-Type the upload must send), None if the insert returned nothing; raises a validation AttachmentError, StorageUnavailableError or InvalidUserError.
    """
    file_type = normalize_file_type(file_type)
    validate_upload(report, file_type, file_size_bytes)
    attachment_name, attachment_description = validate_attachment_metadata(attachment_name, attachment_description)

    attachment_id = uuid4()
    storage_path = build_storage_path(report["report_id"], attachment_id, file_name)

    # Supabase's signed upload URL is fixed at ~2 hours by Storage; the client library has no
    # option to shorten it. The expires_at we report is the window the client is asked to
    # complete the upload in — Supabase itself would accept the URL for longer. Client-side
    # upload timeout logic should honor upload_url_expires_at.
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=STORAGE_UPLOAD_URL_EXPIRY_SECONDS)

    try:
        upload_url = create_signed_upload_url(storage_path)
    except Exception as exc:
        logger.error("Storage could not sign an upload for %s: %s", storage_path, exc)
        raise StorageUnavailableError("Could not create an upload link") from exc

    try:
        rows = insert_attachment(
            attachment_id,
            report["report_id"],
            file_name or UNNAMED,
            file_type,
            file_size_bytes,
            storage_path,
            uploaded_by,
            attachment_name,
            attachment_description,
        )
    except ForeignKeyViolation as exc:
        if exc.diag.constraint_name == UPLOADED_BY_FK:
            raise InvalidUserError(UPLOADER_NOT_FOUND) from exc
        raise

    if not rows:
        return None

    return {
        **rows[0],
        "upload_url": upload_url,
        "upload_url_expires_at": expires_at,
        "upload_headers": {"Content-Type": file_type},
    }


def upload_complete(report_id: UUID, attachment_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Finish a two-step upload by marking the attachment uploaded, so it is listed and downloadable; the client's word is trusted and repeating it is harmless.
    Takes the report uuid and the attachment uuid.
    Returns a one-row list with the attachment, an empty list if the report has no such attachment, or None on failure.
    """
    return mark_attachment_uploaded(report_id, attachment_id)


def update_attachment_metadata(
    idr: dict[str, Any],
    report_id: UUID,
    attachment_id: UUID,
    attachment_name: str,
    attachment_description: str,
) -> Optional[list[dict[str, Any]]]:
    """
    Replace an attachment's name and description; the file and its details are left alone.
    Takes the IDR row, the report and attachment uuids, and the new name and description.
    Returns a one-row list with the attachment, an empty list if there was none, or None on failure; raises IdrNotDraftError or InvalidAttachmentMetadataError.
    """
    if idr["status"] != "draft":
        raise IdrNotDraftError("Only draft IDRs can be edited")

    attachment_name, attachment_description = validate_attachment_metadata(attachment_name, attachment_description)
    return update_attachment_metadata_row(report_id, attachment_id, attachment_name, attachment_description)


def list_attachments(report_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List a report's uploaded attachments, oldest upload first; pending ones are left out.
    Takes the report uuid.
    Returns a list of attachment rows (empty if none), or None on failure.
    """
    return list_attachments_for_report(report_id)


def get_download_url(attachment: dict[str, Any]) -> dict[str, Any]:
    """
    Generate a fresh signed URL for an uploaded attachment; nothing is cached.
    Takes the attachment row; the URL downloads the file under its original file_name and lives STORAGE_URL_EXPIRY_SECONDS.
    Returns {download_url, expires_at}; raises AttachmentNotUploadedError if it is still pending, StorageUnavailableError if Storage can't sign it.
    """
    if not attachment["is_uploaded"]:
        raise AttachmentNotUploadedError("Attachment upload has not been completed")

    # Stamped before the request, so the reported expiry is never later than the real one.
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=STORAGE_URL_EXPIRY_SECONDS)

    try:
        url = create_signed_url(attachment["storage_path"], STORAGE_URL_EXPIRY_SECONDS, attachment["file_name"])
    except Exception as exc:
        logger.error("Storage could not sign %s: %s", attachment["storage_path"], exc)
        raise StorageUnavailableError("Could not create a download link") from exc

    return {"download_url": url, "expires_at": expires_at}


def delete_attachment(report_id: UUID, attachment: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """
    Remove an attachment, pending or uploaded: its Storage file first (best-effort), then its metadata row regardless.
    Takes the report uuid and the attachment row.
    Returns the delete query's result: a one-row list, an empty list if the row was already gone, or None on failure.
    """
    remove_storage_files([attachment["storage_path"]])
    return delete_attachment_row(report_id, attachment["attachment_id"])


def delete_all_storage_files_for_report(report_id: UUID) -> None:
    """
    Remove from Storage every file attached to a report and to the addendums below it, pending ones included, ahead of deleting that report.
    Takes the report uuid.
    Returns nothing; failures are logged as warnings and never raised.
    """
    # Storage is not part of the database transaction. The files go first and the
    # caller deletes the report row afterwards; its ON DELETE CASCADE removes the
    # attachment rows. If a file can't be removed it is left orphaned in the bucket
    # (the row pointing at it is deleted anyway). If the report delete fails after
    # this runs, its attachment rows survive but their files may already be gone.
    # Both trade-offs are accepted: Storage and Postgres can't share a transaction.
    # Pending rows are included: the client may have uploaded without calling upload-complete.
    rows = list_storage_paths_for_report_tree(report_id)

    if rows is None:
        logger.warning("Could not list attachment files for report %s; any files are left orphaned", report_id)
        return

    remove_storage_files(row["storage_path"] for row in rows)


def remove_storage_files(paths: Iterable[str]) -> None:
    """
    Delete files from Storage on a best-effort basis, one request per file so one failure doesn't strand the rest.
    Takes the Storage paths to remove.
    Returns nothing; each failure is logged at WARNING with the path left orphaned, never raised.
    """
    for path in paths:
        try:
            remove_files([path])
        except Exception as exc:
            logger.warning("Could not remove attachment file from Storage, left orphaned: %s (%s)", path, exc)
