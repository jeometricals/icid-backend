"""
Removing a demo user when they sign out: their Storage files, then every row of theirs.
"""

import logging
from uuid import UUID

from api.queries.report_attachments import list_storage_paths_for_demo_user
from api.queries.users import delete_demo_user_rows
from api.services.attachments import remove_storage_files

logger = logging.getLogger(__name__)


def delete_demo_user_cascade(user_uuid: UUID) -> bool:
    """
    Delete a demo user and everything of theirs: attachment files from Storage (best-effort), then attachments, reports, IDRs, project assignments and the user row, in one statement. A user who isn't a demo user is left alone.
    Takes the user's uuid.
    Returns whether a demo user was deleted.
    """
    # Storage is not part of the database transaction, so the files go first, as for a report delete: a file that
    # can't be removed is left orphaned in the bucket once its row is gone.
    paths = list_storage_paths_for_demo_user(user_uuid)
    if paths is None:
        logger.warning("Could not list attachment files for demo user %s; any files are left orphaned", user_uuid)
    else:
        remove_storage_files(row["storage_path"] for row in paths)
    return bool(delete_demo_user_rows(user_uuid))
