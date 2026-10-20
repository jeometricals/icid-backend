from dataclasses import dataclass
from datetime import date
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# The day an approval is filed under: its RE signature's, in New York, as the forms date signatures
FORM_TIMEZONE = "America/New_York"


@dataclass
class InspectionFilters:
    """What a project's ped ramp inspection listing is narrowed by; None (or False) is no filter."""

    status: Optional[str] = None
    inspector_uuid: Optional[UUID] = None
    re_uuid: Optional[UUID] = None
    approved_from: Optional[date] = None
    approved_to: Optional[date] = None
    has_tif: Optional[bool] = None
    q: Optional[str] = None
    with_dot_pdf: bool = False


def _contains(text: str) -> str:
    """
    Build the ILIKE pattern for values that contain a text, its own % and _ matched as written.
    Takes the text.
    Returns the pattern.
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _conditions(filters: InspectionFilters) -> tuple[list[str], list[Any]]:
    """
    Turn the filters into WHERE conditions on the latest inspections (l) and their corners (c).
    Takes the filters.
    Returns the conditions and their parameters, in order.
    """
    conditions: list[str] = []
    params: list[Any] = []
    if filters.status is not None:
        conditions.append("l.status = %s")
        params.append(filters.status)
    if filters.inspector_uuid is not None:
        conditions.append("l.inspector_uuid = %s")
        params.append(filters.inspector_uuid)
    if filters.re_uuid is not None:
        conditions.append("l.re_uuid = %s")
        params.append(filters.re_uuid)
    if filters.approved_from is not None:
        conditions.append(f"(l.re_signed_at AT TIME ZONE '{FORM_TIMEZONE}')::date >= %s")
        params.append(filters.approved_from)
    if filters.approved_to is not None:
        conditions.append(f"(l.re_signed_at AT TIME ZONE '{FORM_TIMEZONE}')::date <= %s")
        params.append(filters.approved_to)
    if filters.has_tif is not None:
        conditions.append(("" if filters.has_tif else "NOT ")
                          + "EXISTS (SELECT 1 FROM icid.ped_ramp_tifs t WHERE t.inspection_id = l.id)")
    if filters.q:
        pattern = _contains(filters.q)
        conditions.append("""(c.north_south_street ILIKE %s OR c.east_west_street ILIKE %s OR EXISTS (
                SELECT 1 FROM icid.properties p
                JOIN icid.ped_ramp_attributes a ON a.property_id = p.id
                WHERE p.corner_id = c.corner_id AND p.deleted_at IS NULL AND a.ramp_id_label ILIKE %s))""")
        params.extend([pattern, pattern, pattern])
    if filters.with_dot_pdf:
        conditions.append("l.status = 'approved' AND l.dot_pdf_path IS NOT NULL")
    return conditions, params


def list_project_inspections(project_id: str, filters: InspectionFilters, limit: int,
                             offset: int) -> Optional[dict[str, Any]]:
    """
    List a project's live corners that have an inspection, one row each for the inspection get_corner_inspection picks (the live one, else the latest approved), filtered; approved newest first (the others after), then by streets and direction.
    Takes the project's id, the filters, how many rows to return and how many to skip.
    Returns {total, rows}: how many match in all and the page's rows (inspection_id, corner_id, corner_name, the streets, corner_direction, ramp_count, status, inspector and RE uuids and names, approved_at, tif_count, has_dot_pdf, dot_pdf_path); None if the statement failed.
    """
    conditions, params = _conditions(filters)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        WITH latest AS (
            SELECT DISTINCT ON (i.corner_id) i.*
            FROM icid.ped_ramp_inspections i
            JOIN icid.ped_ramp_corners pc ON pc.corner_id = i.corner_id
            WHERE pc.project_id = %s AND pc.deleted_at IS NULL
            ORDER BY i.corner_id, (i.status <> 'approved') DESC, i.created_at DESC
        ), matched AS (
            SELECT l.id AS inspection_id, l.corner_id,
                   c.north_south_street || ' & ' || c.east_west_street AS corner_name,
                   c.north_south_street, c.east_west_street, c.corner_direction,
                   (SELECT count(*)::int FROM icid.properties p
                    WHERE p.corner_id = c.corner_id AND p.kind = 'ped_ramp' AND p.deleted_at IS NULL) AS ramp_count,
                   l.status,
                   l.inspector_uuid, NULLIF(concat_ws(' ', iu.first_name, iu.last_name), '') AS inspector_name,
                   l.re_uuid, NULLIF(concat_ws(' ', ru.first_name, ru.last_name), '') AS re_name,
                   l.re_signed_at AS approved_at,
                   (SELECT count(*)::int FROM icid.ped_ramp_tifs t WHERE t.inspection_id = l.id) AS tif_count,
                   l.dot_pdf_path IS NOT NULL AS has_dot_pdf, l.dot_pdf_path
            FROM latest l
            JOIN icid.ped_ramp_corners c ON c.corner_id = l.corner_id
            LEFT JOIN icid.users iu ON iu.uuid = l.inspector_uuid
            LEFT JOIN icid.users ru ON ru.uuid = l.re_uuid
            {where}
        )
        SELECT counted.total, page.*
        FROM (SELECT count(*)::int AS total FROM matched) counted
        LEFT JOIN LATERAL (
            SELECT * FROM matched
            ORDER BY approved_at DESC NULLS LAST, north_south_street, east_west_street, corner_direction
            LIMIT %s OFFSET %s
        ) page ON true;"""
    rows = run_query(sql, (project_id, *params, limit, offset))
    if rows is None:
        return None
    total = rows[0]["total"] if rows else 0
    found = [{key: value for key, value in row.items() if key != "total"} for row in rows
             if row["inspection_id"] is not None]
    return {"total": total, "rows": found}
