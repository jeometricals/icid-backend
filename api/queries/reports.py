from datetime import date
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

REPORT_COLUMNS = """
    report_id,
    reporter_uuid,
    project_id,
    report_date,
    status,
    created_at,
    updated_at
"""


def create_report(
    project_id: str, reporter_uuid: UUID, report_date: date
) -> Optional[dict[str, Any]]:
    """
    Insert a new report; the database generates report_id and defaults status to draft.
    Takes the project id, the reporter's user uuid and the report date.
    Returns the new report dict, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.reports (project_id, reporter_uuid, report_date)
        VALUES (%s, %s, %s)
        RETURNING {REPORT_COLUMNS};
    """
    rows = run_query(sql, (project_id, reporter_uuid, report_date))
    return rows[0] if rows else None


def get_report_by_id(report_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single report.
    Takes the report uuid.
    Returns the report dict, or None if no report matches.
    """
    sql = f"""
        SELECT {REPORT_COLUMNS}
        FROM icid.reports
        WHERE report_id = %s;
    """
    rows = run_query(sql, (report_id,))
    return rows[0] if rows else None


def touch_report(report_id: UUID) -> None:
    """
    Set a report's updated_at to now.
    Takes the report uuid.
    Returns nothing.
    """
    sql = """
        UPDATE icid.reports
        SET updated_at = now()
        WHERE report_id = %s;
    """
    run_query(sql, (report_id,))
