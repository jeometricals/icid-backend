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
    uploaded_at,
    attachment_name,
    attachment_description,
    is_uploaded
"""


def insert_attachment(
    attachment_id: UUID,
    report_id: UUID,
    file_name: str,
    file_type: str,
    file_size_bytes: int,
    storage_path: str,
    uploaded_by: UUID,
    attachment_name: str,
    attachment_description: str,
) -> Optional[list[dict[str, Any]]]:
    """
    Insert one attachment's metadata row as pending (is_uploaded false), before its file reaches Storage.
    Takes the pre-generated attachment uuid (it is part of storage_path), the report uuid, the file details, the uploader's uuid and the name and description.
    Returns a one-row list with the inserted attachment, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.report_attachments
            (attachment_id, report_id, file_name, file_type, file_size_bytes, storage_path, uploaded_by,
             attachment_name, attachment_description, is_uploaded)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)
        RETURNING {ATTACHMENT_COLUMNS};
    """
    return run_query(
        sql,
        (
            attachment_id,
            report_id,
            file_name,
            file_type,
            file_size_bytes,
            storage_path,
            uploaded_by,
            attachment_name,
            attachment_description,
        ),
    )


def list_attachments_for_report(report_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List a report's uploaded attachments, oldest upload first; pending rows (is_uploaded false) are left out.
    Takes the report uuid.
    Returns a list of attachment rows (empty if none), or None on failure.
    """
    sql = f"""
        SELECT {ATTACHMENT_COLUMNS}
        FROM icid.report_attachments
        WHERE report_id = %s AND is_uploaded
        ORDER BY uploaded_at, attachment_id;
    """
    return run_query(sql, (report_id,))


def list_uploaded_attachments_for_reports(report_ids: list[UUID]) -> Optional[list[dict[str, Any]]]:
    """
    List the uploaded attachments of several reports at once (pending rows left out), for the IDR export.
    Takes the report uuids.
    Returns attachment dicts ordered by report, then oldest upload first; empty when none, or None on failure.
    """
    sql = f"""
        SELECT {ATTACHMENT_COLUMNS}
        FROM icid.report_attachments
        WHERE report_id = ANY(%s) AND is_uploaded
        ORDER BY report_id, uploaded_at, attachment_id;
    """
    return run_query(sql, (list(report_ids),))


def get_attachment(report_id: UUID, attachment_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one attachment, pending or uploaded, only if it belongs to the given report.
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


def mark_attachment_uploaded(report_id: UUID, attachment_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Set is_uploaded on one attachment, only if it belongs to the given report; an already-uploaded row stays as it is.
    Takes the report uuid and the attachment uuid.
    Returns a one-row list with the updated attachment, an empty list if there was none, or None on failure.
    """
    sql = f"""
        UPDATE icid.report_attachments
        SET is_uploaded = true
        WHERE report_id = %s AND attachment_id = %s
        RETURNING {ATTACHMENT_COLUMNS};
    """
    return run_query(sql, (report_id, attachment_id))


def update_attachment_metadata_row(
    report_id: UUID,
    attachment_id: UUID,
    attachment_name: str,
    attachment_description: str,
) -> Optional[list[dict[str, Any]]]:
    """
    Replace one attachment's name and description, only if it belongs to the given report.
    Takes the report uuid, the attachment uuid and the new name and description.
    Returns a one-row list with the updated attachment, an empty list if there was none, or None on failure.
    """
    sql = f"""
        UPDATE icid.report_attachments
        SET attachment_name = %s, attachment_description = %s
        WHERE report_id = %s AND attachment_id = %s
        RETURNING {ATTACHMENT_COLUMNS};
    """
    return run_query(sql, (attachment_name, attachment_description, report_id, attachment_id))


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
    List the storage_path of every attachment, pending or uploaded, on a report and on all addendums below it (the rows its delete cascades to).
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
