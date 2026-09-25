from datetime import date
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

IDR_COLUMNS = """
    idr_id,
    project_id,
    reporter_uuid,
    report_date,
    work_start_time,
    work_end_time,
    inspector_start_time,
    inspector_end_time,
    temp_low,
    temp_high,
    weather_am,
    weather_pm,
    total_pages,
    status,
    submitted_at,
    created_at,
    updated_at
"""


def create_idr(
    project_id: str, reporter_uuid: UUID, report_date: date
) -> Optional[list[dict[str, Any]]]:
    """
    Insert a new draft IDR unless one already exists for this project, reporter and date.
    Takes the project id, the reporter's user uuid and the report date.
    Returns a one-row list with the new IDR, an empty list if that day's IDR already exists, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.idrs (project_id, reporter_uuid, report_date)
        VALUES (%s, %s, %s)
        ON CONFLICT (project_id, reporter_uuid, report_date) DO NOTHING
        RETURNING {IDR_COLUMNS};
    """
    return run_query(sql, (project_id, reporter_uuid, report_date))


def get_idr_id_for_day(
    project_id: str, reporter_uuid: UUID, report_date: date
) -> Optional[UUID]:
    """
    Look up the IDR a reporter already has on a project for a given date.
    Takes the project id, the reporter's user uuid and the report date.
    Returns that IDR's uuid, or None if there is none.
    """
    sql = """
        SELECT idr_id
        FROM icid.idrs
        WHERE project_id = %s AND reporter_uuid = %s AND report_date = %s;
    """
    rows = run_query(sql, (project_id, reporter_uuid, report_date))
    return rows[0]["idr_id"] if rows else None
