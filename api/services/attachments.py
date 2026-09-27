import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from psycopg.errors import ForeignKeyViolation

from api.core.config import STORAGE_URL_EXPIRY_SECONDS
from api.queries.report_attachments import (
    delete_attachment_row,
    insert_attachment,
    list_attachments_for_report,
    list_storage_paths_for_report_tree,
)
from api.storage.client import create_signed_url, remove_files, upload_file

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
    """The uploaded file has no bytes."""


class FileTooLargeError(AttachmentError):
    """The uploaded file is over MAX_FILE_SIZE_BYTES."""


class UnsupportedFileTypeError(AttachmentError):
    """The uploaded file's Content-Type is not in ALLOWED_FILE_TYPES."""


class InvalidUserError(AttachmentError):
    """uploaded_by is not a user in icid.users."""


class StorageUnavailableError(AttachmentError):
    """Supabase Storage refused or failed an upload or a signed-URL request."""


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
    Takes the target report row, the normalized MIME type and the file size in bytes.
    Returns nothing; raises AttachmentNotAllowedOnAutoGeneralError, EmptyFileError, FileTooLargeError or UnsupportedFileTypeError.
    """
    _check_not_auto_generated(report)

    if size == 0:
        raise EmptyFileError("File is empty")

    if size > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError("File is larger than the 10 MB limit")

    if file_type not in ALLOWED_FILE_TYPES:
        raise UnsupportedFileTypeError(
            "Unsupported file type. Allowed: JPEG, PNG, GIF, WebP, HEIC/HEIF images and PDF."
        )


def upload_attachment(
    report: dict[str, Any],
    file_name: Optional[str],
    content_type: Optional[str],
    content: bytes,
    uploaded_by: UUID,
) -> Optional[dict[str, Any]]:
    """
    Validate a file, store it in Storage, then record its metadata; the stored file is removed again if the record can't be written.
    Takes the target report row, the uploaded name and Content-Type, the file bytes and the uploader's uuid.
    Returns the new attachment row, None if the insert returned nothing; raises a validation AttachmentError, StorageUnavailableError if Storage refused the file, or InvalidUserError if uploaded_by is not a user.
    """
    file_type = normalize_file_type(content_type)
    validate_upload(report, file_type, len(content))

    attachment_id = uuid4()
    storage_path = build_storage_path(report["report_id"], attachment_id, file_name)

    try:
        upload_file(storage_path, content, file_type)
    except Exception as exc:
        logger.error("Storage upload failed for %s: %s", storage_path, exc)
        raise StorageUnavailableError("Could not store the file") from exc

    try:
        rows = insert_attachment(
            attachment_id,
            report["report_id"],
            file_name or UNNAMED,
            file_type,
            len(content),
            storage_path,
            uploaded_by,
        )
    except ForeignKeyViolation as exc:
        remove_storage_files([storage_path])
        if exc.diag.constraint_name == UPLOADED_BY_FK:
            raise InvalidUserError(UPLOADER_NOT_FOUND) from exc
        raise
    except Exception:
        remove_storage_files([storage_path])
        raise

    if not rows:
        remove_storage_files([storage_path])
        return None

    return rows[0]


def list_attachments(report_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List a report's attachments, oldest upload first.
    Takes the report uuid.
    Returns a list of attachment rows (empty if none), or None on failure.
    """
    return list_attachments_for_report(report_id)


def get_download_url(attachment: dict[str, Any]) -> dict[str, Any]:
    """
    Generate a fresh signed URL for an attachment; nothing is cached.
    Takes the attachment row; the URL downloads the file under its original file_name and lives STORAGE_URL_EXPIRY_SECONDS.
    Returns {download_url, expires_at}; raises StorageUnavailableError if Storage can't sign it.
    """
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
    Remove an attachment: its Storage file first (best-effort), then its metadata row regardless.
    Takes the report uuid and the attachment row.
    Returns the delete query's result: a one-row list, an empty list if the row was already gone, or None on failure.
    """
    remove_storage_files([attachment["storage_path"]])
    return delete_attachment_row(report_id, attachment["attachment_id"])


def delete_all_storage_files_for_report(report_id: UUID) -> None:
    """
    Remove from Storage every file attached to a report and to the addendums below it, ahead of deleting that report.
    Takes the report uuid.
    Returns nothing; failures are logged as warnings and never raised.
    """
    # Storage is not part of the database transaction. The files go first and the
    # caller deletes the report row afterwards; its ON DELETE CASCADE removes the
    # attachment rows. If a file can't be removed it is left orphaned in the bucket
    # (the row pointing at it is deleted anyway). If the report delete fails after
    # this runs, its attachment rows survive but their files may already be gone.
    # Both trade-offs are accepted: Storage and Postgres can't share a transaction.
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
