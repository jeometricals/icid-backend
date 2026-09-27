from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

ATTACHMENT_COLUMNS = """
    attachment_id,
    report_id,
    file_name,
    file_type,
    file_size_bytes,
    storage_path,
    uploaded_by,
    uploaded_at
"""


def insert_attachment(
    attachment_id: UUID,
    report_id: UUID,
    file_name: str,
    file_type: str,
    file_size_bytes: int,
    storage_path: str,
    uploaded_by: UUID,
) -> Optional[list[dict[str, Any]]]:
    """
    Insert one attachment's metadata row.
    Takes the pre-generated attachment uuid (it is part of storage_path), the report uuid, the file details and the uploader's uuid.
    Returns a one-row list with the inserted attachment, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.report_attachments
            (attachment_id, report_id, file_name, file_type, file_size_bytes, storage_path, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING {ATTACHMENT_COLUMNS};
    """
    return run_query(
        sql, (attachment_id, report_id, file_name, file_type, file_size_bytes, storage_path, uploaded_by)
    )


def list_attachments_for_report(report_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List a report's attachments, oldest upload first.
    Takes the report uuid.
    Returns a list of attachment rows (empty if none), or None on failure.
    """
    sql = f"""
        SELECT {ATTACHMENT_COLUMNS}
        FROM icid.report_attachments
        WHERE report_id = %s
        ORDER BY uploaded_at, attachment_id;
    """
    return run_query(sql, (report_id,))


def get_attachment(report_id: UUID, attachment_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one attachment, only if it belongs to the given report.
    Takes the report uuid and the attachment uuid.
    Returns the attachment row, or None if there is no such attachment on that report.
    """
    sql = f"""
        SELECT {ATTACHMENT_COLUMNS}
        FROM icid.report_attachments
        WHERE report_id = %s AND attachment_id = %s;
    """
    rows = run_query(sql, (report_id, attachment_id))
    return rows[0] if rows else None


def delete_attachment_row(report_id: UUID, attachment_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Delete one attachment's metadata row, only if it belongs to the given report.
    Takes the report uuid and the attachment uuid.
    Returns a one-row list with the deleted attachment, an empty list if there was none, or None on failure.
    """
    sql = f"""
        DELETE FROM icid.report_attachments
        WHERE report_id = %s AND attachment_id = %s
        RETURNING {ATTACHMENT_COLUMNS};
    """
    return run_query(sql, (report_id, attachment_id))


def list_storage_paths_for_report_tree(report_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List the storage_path of every attachment on a report and on all addendums below it (the rows its delete cascades to).
    Takes the report uuid.
    Returns a list of {storage_path} rows (empty if none), or None on failure.
    """
    sql = """
        WITH RECURSIVE tree AS (
            SELECT report_id FROM icid.idr_reports WHERE report_id = %s
            UNION
            SELECT r.report_id
            FROM icid.idr_reports r
            JOIN tree t ON r.parent_report_id = t.report_id
        )
        SELECT a.storage_path
        FROM icid.report_attachments a
        JOIN tree t ON t.report_id = a.report_id
        ORDER BY a.storage_path;
    """
    return run_query(sql, (report_id,))
