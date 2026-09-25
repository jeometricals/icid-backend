from typing import Any, Optional
from uuid import UUID

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
