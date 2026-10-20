from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# The columns a create or an edit writes (all of them, on an edit), each with its typed placeholder
_WRITTEN = (
    ("inspected_by", "%s"), ("inspection_date", "%s"), ("entity", "%s"), ("entity_if_other", "%s"),
    ("boro", "%s::icid.boro"), ("dot_corner_id", "%s"), ("corner_number", "%s"), ("north_south_street", "%s"),
    ("east_west_street", "%s"), ("traffic_control_ns", "%s"), ("traffic_control_ew", "%s"), ("corner_type", "%s"),
    ("corner_direction", "%s"), ("number_of_ramps", "%s"), ("t_intersection", "%s"), ("painted_roadway", "%s"),
    ("contractor_name", "%s"), ("permittee_id", "%s"), ("fms_id", "%s"), ("permit_number", "%s"),
    ("construction_completion_date", "%s"), ("contract_type", "%s"), ("curb_material", "%s"),
    ("sidewalk_material", "%s"), ("comments", "%s"),
)
WRITTEN_COLUMNS = tuple(column for column, _ in _WRITTEN)
# A corner as every function here returns it (c), its borough as text, with its live ramps
_CORNER_COLUMNS = (
    "\n            c.corner_id, c.project_id, "
    + ", ".join("c.boro::text AS boro" if column == "boro" else f"c.{column}" for column in WRITTEN_COLUMNS)
    + """,
            c.created_at, c.updated_at, c.created_by, c.deleted_at, c.deleted_by,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'property_id', p.id, 'intersection_street_1', p.intersection_street_1,
                    'intersection_street_2', p.intersection_street_2, 'quadrant', p.quadrant::text,
                    'facing_street', p.facing_street::text, 'ramp_type', p.ramp_type::text,
                    'ramp_id_label', a.ramp_id_label, 'has_attributes', a.property_id IS NOT NULL
                ) ORDER BY p.quadrant, p.ramp_type, p.created_at)
                FROM icid.properties p
                LEFT JOIN icid.ped_ramp_attributes a ON a.property_id = p.id
                WHERE p.corner_id = c.corner_id AND p.deleted_at IS NULL
            ), '[]'::jsonb) AS ramps"""
)


def _written_values(data: dict[str, Any]) -> tuple[Any, ...]:
    """
    Read the written columns' values off a corner, in _WRITTEN's order.
    Takes the values keyed by column; a column left out is None.
    Returns them as a tuple.
    """
    return tuple(data.get(column) for column in WRITTEN_COLUMNS)


def list_corners(project_id: str, include_deleted: bool = False) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a project's ped ramp corners, the soft-deleted ones only when asked for, each with its live ramps.
    Takes the project id and whether to include soft-deleted corners.
    Returns the corner dicts ordered by their streets, then corner number, or None on failure.
    """
    sql = (
        "\n        SELECT"
        + _CORNER_COLUMNS
        + """
        FROM icid.ped_ramp_corners c
        WHERE c.project_id = %s AND (%s OR c.deleted_at IS NULL)
        ORDER BY c.north_south_street, c.east_west_street, c.corner_number, c.created_at;"""
    )
    return run_query(sql, (project_id, include_deleted))


def get_corner(corner_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one corner, deleted or not, with its live ramps.
    Takes the corner's id.
    Returns the corner dict, or None if no corner matches.
    """
    sql = "\n        SELECT" + _CORNER_COLUMNS + "\n        FROM icid.ped_ramp_corners c\n        WHERE c.corner_id = %s;"
    rows = run_query(sql, (corner_id,))
    return rows[0] if rows else None


def create_corner(project_id: str, created_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a ped ramp corner to a project.
    Takes the project id, the uuid of the user adding it, and the values keyed by column (one left out is None).
    Returns the new corner dict (with no ramps yet), or None on failure.
    """
    sql = (
        """
        WITH c AS (
            INSERT INTO icid.ped_ramp_corners (project_id, """
        + ", ".join(WRITTEN_COLUMNS)
        + """, created_by)
            VALUES (%s, """
        + ", ".join(placeholder for _, placeholder in _WRITTEN)
        + """, %s)
            RETURNING *
        )
        SELECT"""
        + _CORNER_COLUMNS
        + "\n        FROM c;"
    )
    rows = run_query(sql, (project_id, *_written_values(data), created_by_uuid))
    return rows[0] if rows else None


def update_corner(corner_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Write a corner's record whole, as an edit leaves it, and its borough and streets onto its live ramps in the same statement.
    Takes the corner's id and every written column's value keyed by column (one left out is written as None).
    Returns the updated corner dict with its live ramps as they were before the statement, or None when no corner matches.
    """
    sql = (
        """
        WITH c AS (
            UPDATE icid.ped_ramp_corners
            SET """
        + ",\n                ".join(f"{column} = {placeholder}" for column, placeholder in _WRITTEN)
        + """,
                updated_at = now()
            WHERE corner_id = %s
            RETURNING *
        ),
        ramps AS (
            UPDATE icid.properties p
            SET boro = c.boro, intersection_street_1 = c.north_south_street,
                intersection_street_2 = c.east_west_street, updated_at = now()
            FROM c
            WHERE p.corner_id = c.corner_id AND p.deleted_at IS NULL
              AND (p.boro, p.intersection_street_1, p.intersection_street_2)
                  IS DISTINCT FROM (c.boro, c.north_south_street, c.east_west_street)
        )
        SELECT"""
        + _CORNER_COLUMNS
        + "\n        FROM c;"
    )
    rows = run_query(sql, (*_written_values(data), corner_id))
    return rows[0] if rows else None


def soft_delete_corner(corner_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a corner deleted, with when and by whom. Its ramps keep it as their corner.
    Takes the corner's id and the uuid of the admin deleting it.
    Returns the updated corner dict, or None if no corner matches or it is already deleted.
    """
    sql = (
        """
        WITH c AS (
            UPDATE icid.ped_ramp_corners
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE corner_id = %s AND deleted_at IS NULL
            RETURNING *
        )
        SELECT"""
        + _CORNER_COLUMNS
        + "\n        FROM c;"
    )
    rows = run_query(sql, (deleted_by_uuid, corner_id))
    return rows[0] if rows else None


def get_live_corner_of_project(corner_id: UUID, project_id: str) -> Optional[dict[str, Any]]:
    """
    Fetch a corner only when it is live and of the given project: the one a ped ramp may be placed at.
    Takes the corner's id and the project id.
    Returns {corner_id}, or None when it isn't.
    """
    sql = """
        SELECT corner_id FROM icid.ped_ramp_corners
        WHERE corner_id = %s AND project_id = %s AND deleted_at IS NULL;"""
    rows = run_query(sql, (corner_id, project_id))
    return rows[0] if rows else None


def create_pedramp_site(project_id: str, created_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a ped ramp site in one statement: a live corner of the project at the same borough, streets (any case) and direction is reused, else one is added; then the ramp, a ped ramp property at it (its borough and streets the corner's), and an attributes row holding its label when one is given.
    Takes the project id, the uuid of the user adding it, and the corner's and the ramp's values keyed by column.
    Returns {corner_id, property_id, corner_reused}, or None on failure.
    """
    sql = (
        """
        WITH found AS (
            SELECT corner_id, boro, north_south_street, east_west_street
            FROM icid.ped_ramp_corners
            WHERE project_id = %s AND deleted_at IS NULL AND boro = %s::icid.boro
              AND lower(north_south_street) = lower(%s) AND lower(east_west_street) = lower(%s)
              AND corner_direction = %s
            ORDER BY created_at
            LIMIT 1
        ),
        made AS (
            INSERT INTO icid.ped_ramp_corners (project_id, """
        + ", ".join(WRITTEN_COLUMNS)
        + """, created_by)
            SELECT %s, """
        + ", ".join(placeholder for _, placeholder in _WRITTEN)
        + """, %s
            WHERE NOT EXISTS (SELECT 1 FROM found)
            RETURNING corner_id, boro, north_south_street, east_west_street
        ),
        corner AS (
            SELECT found.*, true AS reused FROM found
            UNION ALL
            SELECT made.*, false AS reused FROM made
        ),
        ramp AS (
            INSERT INTO icid.properties (project_id, kind, boro, intersection_street_1, intersection_street_2,
                                         quadrant, facing_street, ramp_type, corner_id, notes, created_by)
            SELECT %s, 'ped_ramp', corner.boro, corner.north_south_street, corner.east_west_street,
                   %s::icid.quadrant, %s::icid.facing_street, %s::icid.ramp_type, corner.corner_id, %s, %s
            FROM corner
            RETURNING id, corner_id
        ),
        label AS (
            INSERT INTO icid.ped_ramp_attributes (property_id, ramp_id_label)
            SELECT ramp.id, %s FROM ramp WHERE %s::text IS NOT NULL
        )
        SELECT ramp.corner_id, ramp.id AS property_id, corner.reused AS corner_reused
        FROM ramp
        JOIN corner ON corner.corner_id = ramp.corner_id;"""
    )
    params = (
        project_id, data["boro"], data["north_south_street"], data["east_west_street"], data["corner_direction"],
        project_id, *_written_values(data), created_by_uuid,
        project_id, data["quadrant"], data["facing_street"], data["ramp_type"], data.get("notes"), created_by_uuid,
        data.get("ramp_id_label"), data.get("ramp_id_label"),
    )
    rows = run_query(sql, params)
    return rows[0] if rows else None
