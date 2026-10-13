"""
Deleting a soft-deleted IDR for good: its row, which takes everything under it along, then its files in Storage.

The database part is one statement. Storage can't share a transaction with it, so the files go afterwards, each on a
best-effort basis: a file that can't be removed is left orphaned and logged, and the IDR is gone all the same.
"""

import logging
from uuid import UUID

from api.core.config import EXPORT_BUCKET_NAME, SIGNATURE_BUCKET_NAME
from api.queries.idrs import permanent_delete_idr
from api.queries.report_attachments import list_storage_paths_for_idr
from api.services.attachments import remove_storage_files
from api.storage.client import list_file_paths

logger = logging.getLogger(__name__)

# How many times a folder is listed while it is emptied; each listing gives up to 1000 files
MAX_FOLDER_ROUNDS = 20


def _empty_folder(bucket: str, folder: str) -> None:
    """
    Remove every file in one folder of a bucket, best-effort.
    Takes the bucket and the folder's path.
    Returns nothing; a folder that can't be listed, or a file that can't be removed, is logged at WARNING and left.
    """
    previous: list[str] = []
    for _ in range(MAX_FOLDER_ROUNDS):
        try:
            paths = list_file_paths(bucket, folder)
        except Exception as exc:
            logger.warning("Could not list %s in bucket %s; any files are left orphaned (%s)", folder, bucket, exc)
            return
        if not paths or paths == previous:
            return
        remove_storage_files(paths, bucket)
        previous = paths


def purge_idr(idr_id: UUID) -> bool:
    """
    Delete a soft-deleted IDR for good: the row (its reports, attachments, edits, audit rows and quantities go with it), then its attachment files, its signature copies and its exports from Storage, best-effort. An IDR that isn't soft-deleted is left alone, files included.
    Takes the IDR uuid.
    Returns whether the IDR was deleted.
    """
    # Read before the delete: the attachment rows that name the files go with the IDR
    attachments = list_storage_paths_for_idr(idr_id)

    if not permanent_delete_idr(idr_id):
        return False

    if attachments is None:
        logger.warning("Could not list attachment files for IDR %s; any files are left orphaned", idr_id)
    else:
        remove_storage_files(row["storage_path"] for row in attachments)
    _empty_folder(SIGNATURE_BUCKET_NAME, f"idrs/{idr_id}")
    _empty_folder(EXPORT_BUCKET_NAME, str(idr_id))
    return True
