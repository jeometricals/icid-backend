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


def list_reports(
    project_id: str,
    form_template_id: str,
    reporter_uuid: Optional[UUID] = None,
    status: Optional[str] = None,
) -> Optional[list[dict[str, Any]]]:
    """
    List a project's reports, newest edit first, optionally filtered by reporter and status.
    Takes the project id, the form template whose description is previewed, and optional reporter uuid and status.
    Returns a list of report dicts with a description_preview column (None if that form was never saved).
    """
    conditions = ["r.project_id = %s"]
    params: list[Any] = [form_template_id, project_id]

    if reporter_uuid is not None:
        conditions.append("r.reporter_uuid = %s")
        params.append(reporter_uuid)

    if status is not None:
        conditions.append("r.status = %s")
        params.append(status)

    sql = f"""
        SELECT
            r.report_id,
            r.reporter_uuid,
            r.project_id,
            r.report_date,
            r.status,
            r.created_at,
            r.updated_at,
            NULLIF(LEFT(cf.form_data->>'description', 80), '') AS description_preview
        FROM icid.reports r
        LEFT JOIN icid.completed_forms cf
            ON cf.report_id = r.report_id AND cf.form_template_id = %s
        WHERE {" AND ".join(conditions)}
        ORDER BY r.updated_at DESC;
    """
    return run_query(sql, tuple(params))
