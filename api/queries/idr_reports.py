from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query

IDR_REPORT_COLUMNS = """
    report_id,
    idr_id,
    report_type,
    is_addendum,
    parent_report_id,
    page_number,
    report_data,
    created_at,
    updated_at
"""


def list_reports_for_idr(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List every report in an IDR, in page order once submitted and creation order before.
    Takes the IDR uuid.
    Returns a list of report dicts (report_data as a dict), empty if the IDR has none, or None on failure.
    """
    sql = f"""
        SELECT {IDR_REPORT_COLUMNS}
        FROM icid.idr_reports
        WHERE idr_id = %s
        ORDER BY page_number NULLS LAST, created_at;
    """
    return run_query(sql, (idr_id,))


def get_idr_report(idr_id: UUID, report_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single report, only if it belongs to the given IDR.
    Takes the IDR uuid and the report uuid.
    Returns the report dict, or None if no report with that id exists in that IDR.
    """
    sql = f"""
        SELECT {IDR_REPORT_COLUMNS}
        FROM icid.idr_reports
        WHERE idr_id = %s AND report_id = %s;
    """
    rows = run_query(sql, (idr_id, report_id))
    return rows[0] if rows else None


def create_idr_report(
    idr_id: UUID, report_type: str, is_addendum: bool, parent_report_id: Optional[UUID]
) -> Optional[list[dict[str, Any]]]:
    """
    Insert a new report into an IDR with empty report_data, unless it would be the IDR's second General.
    Takes the IDR uuid, the report type, the addendum flag and the parent report uuid (or None).
    Returns a one-row list with the new report, an empty list if the IDR already has a General, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.idr_reports (idr_id, report_type, is_addendum, parent_report_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (idr_id) WHERE report_type = 'GEN' AND is_addendum = false DO NOTHING
        RETURNING {IDR_REPORT_COLUMNS};
    """
    return run_query(sql, (idr_id, report_type, is_addendum, parent_report_id))


def get_general_report_id(idr_id: UUID) -> Optional[UUID]:
    """
    Look up the IDR's General (non-addendum GEN) report.
    Takes the IDR uuid.
    Returns that report's uuid, or None if the IDR has no General.
    """
    sql = """
        SELECT report_id
        FROM icid.idr_reports
        WHERE idr_id = %s AND report_type = 'GEN' AND is_addendum = false;
    """
    rows = run_query(sql, (idr_id,))
    return rows[0]["report_id"] if rows else None


def save_report_data(
    idr_id: UUID, report_id: UUID, report_data: dict[str, Any]
) -> Optional[list[dict[str, Any]]]:
    """
    Replace a report's report_data and stamp updated_at on both the report and its IDR, in one statement.
    Takes the IDR uuid, the report uuid and the data dict (stored as JSONB); only a report in that IDR, while the IDR is a draft, is changed.
    Returns a one-row list with the saved report, an empty list if no such report in a draft IDR, or None on failure.
    """
    sql = f"""
        WITH saved AS (
            UPDATE icid.idr_reports r
            SET report_data = %s, updated_at = now()
            FROM icid.idrs i
            WHERE r.idr_id = %s AND r.report_id = %s
                AND i.idr_id = r.idr_id AND i.status = 'draft'
            RETURNING r.*
        ),
        touched AS (
            UPDATE icid.idrs
            SET updated_at = now()
            WHERE idr_id IN (SELECT idr_id FROM saved)
        )
        SELECT {IDR_REPORT_COLUMNS}
        FROM saved;
    """
    return run_query(sql, (Jsonb(report_data), idr_id, report_id))
