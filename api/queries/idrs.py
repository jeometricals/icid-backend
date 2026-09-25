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


def get_idr_by_id(idr_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single IDR with its header fields.
    Takes the IDR uuid.
    Returns the IDR dict, or None if no IDR matches.
    """
    sql = f"""
        SELECT {IDR_COLUMNS}
        FROM icid.idrs
        WHERE idr_id = %s;
    """
    rows = run_query(sql, (idr_id,))
    return rows[0] if rows else None


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


def touch_idr(idr_id: UUID) -> None:
    """
    Set an IDR's updated_at to now.
    Takes the IDR uuid.
    Returns nothing.
    """
    sql = """
        UPDATE icid.idrs
        SET updated_at = now()
        WHERE idr_id = %s;
    """
    run_query(sql, (idr_id,))


HEADER_COLUMNS = (
    "work_start_time",
    "work_end_time",
    "inspector_start_time",
    "inspector_end_time",
    "temp_low",
    "temp_high",
    "weather_am",
    "weather_pm",
)


def update_idr_header(idr_id: UUID, fields: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """
    Set the given header columns on a draft IDR and stamp updated_at; columns not in fields are untouched.
    Takes the IDR uuid and a dict of header column -> value (None clears); keys must be in HEADER_COLUMNS.
    Returns a one-row list with the updated IDR, an empty list if the IDR is not a draft, or None on failure.
    """
    unknown = set(fields) - set(HEADER_COLUMNS)
    if unknown:
        raise ValueError(f"Not header columns: {sorted(unknown)}")

    columns = [column for column in HEADER_COLUMNS if column in fields]
    assignments = [f"{column} = %s" for column in columns] + ["updated_at = now()"]

    sql = f"""
        UPDATE icid.idrs
        SET {", ".join(assignments)}
        WHERE idr_id = %s AND status = 'draft'
        RETURNING {IDR_COLUMNS};
    """
    return run_query(sql, (*[fields[column] for column in columns], idr_id))


def list_idrs(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    reporter_uuid: Optional[UUID] = None,
) -> Optional[list[dict[str, Any]]]:
    """
    List IDRs, most recently edited first, each with its report count and whether it holds a General.
    Takes optional project id, status and reporter uuid filters; any left as None is not applied.
    Returns a list of IDR dicts with report_count and has_general columns (empty if none match), or None on failure.
    """
    conditions: list[str] = []
    params: list[Any] = []

    if project_id is not None:
        conditions.append("i.project_id = %s")
        params.append(project_id)

    if status is not None:
        conditions.append("i.status = %s")
        params.append(status)

    if reporter_uuid is not None:
        conditions.append("i.reporter_uuid = %s")
        params.append(reporter_uuid)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sql = f"""
        SELECT
            {IDR_COLUMNS},
            (
                SELECT COUNT(*)
                FROM icid.idr_reports r
                WHERE r.idr_id = i.idr_id
            ) AS report_count,
            EXISTS (
                SELECT 1
                FROM icid.idr_reports r
                WHERE r.idr_id = i.idr_id AND r.report_type = 'GEN' AND r.is_addendum = false
            ) AS has_general
        FROM icid.idrs i
        {where}
        ORDER BY i.updated_at DESC, i.created_at DESC, i.idr_id;
    """
    return run_query(sql, tuple(params))


def submit_idr(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Submit a draft IDR in one statement: lock it, number its reports, set total_pages, status, submitted_at and updated_at.
    Takes the IDR uuid. Pages run General's group first, then other main reports by creation, each followed by its addendums, then standalone addendums.
    Returns a one-row list with the submitted IDR, an empty list if it is not a draft or has no reports, or None on failure.
    """
    sql = f"""
        WITH target AS (
            SELECT idr_id
            FROM icid.idrs
            WHERE idr_id = %s AND status = 'draft'
            FOR UPDATE
        ),
        ordered AS (
            SELECT
                r.report_id,
                ROW_NUMBER() OVER (
                    ORDER BY
                        (r.is_addendum AND r.parent_report_id IS NULL),
                        (COALESCE(p.report_type, r.report_type) = 'GEN') DESC,
                        COALESCE(p.created_at, r.created_at),
                        COALESCE(p.report_id, r.report_id),
                        r.is_addendum,
                        r.created_at,
                        r.report_id
                ) AS page_number
            FROM icid.idr_reports r
            JOIN target t ON t.idr_id = r.idr_id
            LEFT JOIN icid.idr_reports p ON p.report_id = r.parent_report_id
        ),
        numbered AS (
            UPDATE icid.idr_reports r
            SET page_number = o.page_number, updated_at = now()
            FROM ordered o
            WHERE r.report_id = o.report_id
        )
        UPDATE icid.idrs
        SET status = 'submitted',
            submitted_at = now(),
            updated_at = now(),
            total_pages = (SELECT COUNT(*) FROM ordered)
        WHERE idr_id IN (SELECT idr_id FROM target)
            AND EXISTS (SELECT 1 FROM ordered)
        RETURNING {IDR_COLUMNS};
    """
    return run_query(sql, (idr_id,))
