from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query

# A property as every function here returns it
_PROPERTY_COLUMNS = """
            p.id, p.project_id, p.bbl, p.address, p.property_type, p.notes,
            p.created_at, p.updated_at, p.created_by, p.deleted_at, p.deleted_by"""


def list_properties(project_id: str, include_deleted: bool = False) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a project's properties, the soft-deleted ones only when asked for.
    Takes the project id and whether to include soft-deleted properties.
    Returns the property dicts ordered by address then BBL, or None on failure.
    """
    sql = (
        "\n        SELECT"
        + _PROPERTY_COLUMNS
        + """
        FROM icid.properties p
        WHERE p.project_id = %s AND (%s OR p.deleted_at IS NULL)
        ORDER BY p.address, p.bbl;"""
    )
    return run_query(sql, (project_id, include_deleted))


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
    Takes the project id, the uuid of the admin adding it, and the values keyed by column (bbl, address, property_type, notes).
    Returns the new property dict, or None when the BBL is taken.
    """
    sql = (
        """
        INSERT INTO icid.properties AS p (project_id, bbl, address, property_type, notes, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING"""
        + _PROPERTY_COLUMNS
        + ";"
    )
    params = (project_id, data["bbl"], data["address"], data["property_type"], data.get("notes"), created_by_uuid)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_property(property_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Change some of a property; a column left out keeps its value. A live property is left alone when another live property of its project already has the BBL it would end up with.
    Takes the property's id and the new values keyed by column (bbl, address, property_type, notes; a notes of None clears them).
    Returns the updated property dict, or None when no property matches or the change would collide.
    """
    sql = (
        """
        UPDATE icid.properties p
        SET bbl = COALESCE(%s, p.bbl),
            address = COALESCE(%s, p.address),
            property_type = COALESCE(%s, p.property_type),
            notes = CASE WHEN %s THEN %s ELSE p.notes END,
            updated_at = now()
        WHERE p.id = %s
          AND (
              p.deleted_at IS NOT NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM icid.properties other
                  WHERE other.project_id = p.project_id
                    AND other.bbl = COALESCE(%s, p.bbl)
                    AND other.id <> p.id
                    AND other.deleted_at IS NULL
              )
          )
        RETURNING"""
        + _PROPERTY_COLUMNS
        + ";"
    )
    params = (
        data.get("bbl"), data.get("address"), data.get("property_type"), "notes" in data, data.get("notes"),
        property_id, data.get("bbl"),
    )
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
