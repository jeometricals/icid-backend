from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query

# A property as every function here returns it, its enums as text
_PROPERTY_COLUMNS = """
            p.id, p.project_id, p.kind::text AS kind, p.boro::text AS boro, p.bbl, p.address, p.property_type,
            p.violation_no, p.intersection_street_1, p.intersection_street_2, p.quadrant::text AS quadrant,
            p.facing_street::text AS facing_street, p.ramp_type::text AS ramp_type, p.notes,
            p.created_at, p.updated_at, p.created_by, p.deleted_at, p.deleted_by"""
# What properties are ordered by: a sidewalk's address, a ped ramp's intersection (then its quadrant)
PROPERTY_ORDER = (
    "COALESCE(p.address, p.intersection_street_1 || ' & ' || p.intersection_street_2), p.quadrant, p.bbl"
)
# The columns a create or an edit writes (all of them, on an edit), each with its typed placeholder
_WRITTEN = (
    ("boro", "%s::icid.boro"), ("bbl", "%s"), ("address", "%s"), ("property_type", "%s"), ("violation_no", "%s"),
    ("intersection_street_1", "%s"), ("intersection_street_2", "%s"), ("quadrant", "%s::icid.quadrant"),
    ("facing_street", "%s::icid.facing_street"), ("ramp_type", "%s::icid.ramp_type"), ("notes", "%s"),
)


def _written_values(data: dict[str, Any]) -> tuple[Any, ...]:
    """
    Read the written columns' values off a property, in _WRITTEN's order.
    Takes the values keyed by column; a column left out is None.
    Returns them as a tuple.
    """
    return tuple(data.get(column) for column, _ in _WRITTEN)


def list_properties(
    project_id: str, timezone: str, include_deleted: bool = False, kind: Optional[str] = None
) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a project's properties, of one kind or both, the soft-deleted ones only when asked for, each with how far its live PIR has got, its status and its latest live task order.
    Takes the project id, the time zone a signing's day is read in, whether to include soft-deleted properties and the kind (None for both).
    Returns the property dicts, each with pir_status, status ({state, date}) and task_order_no, ordered by address (a ped ramp's intersection), or None on failure.
    """
    # At most one live PIR per property (uq_pirs_property), so the join never repeats a property. The latest task
    # order is the one issued last (undated ones after the dated), then the newest.
    sql = (
        "\n        SELECT"
        + _PROPERTY_COLUMNS
        + """,
            CASE
                WHEN pir.id IS NULL THEN 'none'
                WHEN pir.as_built_signed_at IS NOT NULL THEN 'as_built_signed'
                ELSE 'dot_signed'
            END AS pir_status,
            jsonb_build_object(
                'state', CASE
                    WHEN pir.as_built_signed_at IS NOT NULL THEN 'constructed_on'
                    WHEN latest.task_order_number IS NOT NULL THEN 'task_ordered'
                    ELSE 'none'
                END,
                'date', (pir.as_built_signed_at AT TIME ZONE %s)::date
            ) AS status,
            latest.task_order_number AS task_order_no
        FROM icid.properties p
        LEFT JOIN icid.pirs pir ON pir.property_id = p.id AND pir.deleted_at IS NULL
        LEFT JOIN LATERAL (
            SELECT t.task_order_number
            FROM icid.task_order_properties tp
            JOIN icid.task_orders t ON t.id = tp.task_order_id
            WHERE tp.property_id = p.id AND t.deleted_at IS NULL
            ORDER BY t.issued_date DESC NULLS LAST, t.created_at DESC
            LIMIT 1
        ) latest ON true
        WHERE p.project_id = %s AND (%s OR p.deleted_at IS NULL)
          AND (%s::icid.property_kind IS NULL OR p.kind = %s::icid.property_kind)
        ORDER BY """
        + PROPERTY_ORDER
        + ";"
    )
    return run_query(sql, (timezone, project_id, include_deleted, kind, kind))


def get_property(property_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one property, deleted or not.
    Takes the property's id.
    Returns the property dict, or None if no property matches.
    """
    sql = "\n        SELECT" + _PROPERTY_COLUMNS + "\n        FROM icid.properties p\n        WHERE p.id = %s;"
    rows = run_query(sql, (property_id,))
    return rows[0] if rows else None


def get_property_by_bbl(project_id: str, bbl: str) -> Optional[dict[str, Any]]:
    """
    Fetch the property of a project that holds a BBL: the one that isn't deleted.
    Takes the project id and the BBL, matched exactly.
    Returns the property dict, or None if no live property of the project has it.
    """
    sql = (
        "\n        SELECT"
        + _PROPERTY_COLUMNS
        + """
        FROM icid.properties p
        WHERE p.project_id = %s AND p.bbl = %s AND p.deleted_at IS NULL;"""
    )
    rows = run_query(sql, (project_id, bbl))
    return rows[0] if rows else None


def create_property(project_id: str, created_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a property to a project. Adds nothing when a live property of the project already has the BBL.
    Takes the project id, the uuid of the user adding it, and the values keyed by column: kind, boro, a sidewalk's bbl, address, property_type and violation_no or a ped ramp's intersection_street_1 and _2, quadrant, facing_street and ramp_type, and notes (one left out is None).
    Returns the new property dict, or None when the BBL is taken.
    """
    sql = (
        """
        INSERT INTO icid.properties AS p (project_id, kind, """
        + ", ".join(column for column, _ in _WRITTEN)
        + """, created_by)
        VALUES (%s, %s::icid.property_kind, """
        + ", ".join(placeholder for _, placeholder in _WRITTEN)
        + """, %s)
        ON CONFLICT DO NOTHING
        RETURNING"""
        + _PROPERTY_COLUMNS
        + ";"
    )
    params = (project_id, data["kind"], *_written_values(data), created_by_uuid)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_property(property_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Write a property's record whole, as an edit leaves it (its kind is never written). A live property is left alone when another live property of its project already has the BBL it would end up with.
    Takes the property's id and every written column's value keyed by column (one left out is written as None).
    Returns the updated property dict, or None when no property matches or the change would collide.
    """
    sql = (
        """
        UPDATE icid.properties p
        SET """
        + ",\n            ".join(f"{column} = {placeholder}" for column, placeholder in _WRITTEN)
        + """,
            updated_at = now()
        WHERE p.id = %s
          AND (
              p.deleted_at IS NOT NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM icid.properties other
                  WHERE other.project_id = p.project_id
                    AND other.bbl = %s
                    AND other.id <> p.id
                    AND other.deleted_at IS NULL
              )
          )
        RETURNING"""
        + _PROPERTY_COLUMNS
        + ";"
    )
    params = (*_written_values(data), property_id, data.get("bbl"))
    try:
        rows = run_query(sql, params)
    except UniqueViolation:
        # Another property took the BBL between the statement's own check and its write
        return None
    return rows[0] if rows else None


def soft_delete_property(property_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a property deleted, with when and by whom; it frees its BBL.
    Takes the property's id and the uuid of the admin deleting it.
    Returns the updated property dict, or None if no property matches or it is already deleted.
    """
    sql = (
        """
        UPDATE icid.properties p
        SET deleted_at = now(),
            deleted_by = %s,
            updated_at = now()
        WHERE p.id = %s AND p.deleted_at IS NULL
        RETURNING"""
        + _PROPERTY_COLUMNS
        + ";"
    )
    rows = run_query(sql, (deleted_by_uuid, property_id))
    return rows[0] if rows else None


def undelete_property(property_id: UUID) -> Optional[dict[str, Any]]:
    """
    Bring a deleted property back: clear deleted_at and deleted_by. Changes nothing when a live property of its project now has its BBL.
    Takes the property's id.
    Returns the updated property dict, or None if no property matches, it isn't deleted, or its BBL is taken.
    """
    sql = (
        """
        UPDATE icid.properties p
        SET deleted_at = NULL,
            deleted_by = NULL,
            updated_at = now()
        WHERE p.id = %s
          AND p.deleted_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM icid.properties other
              WHERE other.project_id = p.project_id
                AND other.bbl = p.bbl
                AND other.id <> p.id
                AND other.deleted_at IS NULL
          )
        RETURNING"""
        + _PROPERTY_COLUMNS
        + ";"
    )
    try:
        rows = run_query(sql, (property_id,))
    except UniqueViolation:
        # Another property took the BBL between the statement's own check and its write
        return None
    return rows[0] if rows else None
