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
    Write a corner's record whole, as an edit leaves it.
    Takes the corner's id and every written column's value keyed by column (one left out is written as None).
    Returns the updated corner dict with its live ramps, or None when no corner matches.
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
