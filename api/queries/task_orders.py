from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query
from api.queries.properties import PROPERTY_ORDER

# A task order's own columns
_TASK_ORDER_COLUMNS = """
            t.id, t.project_id, t.task_order_number, t.issued_date, t.estimate_notes,
            t.created_at, t.updated_at, t.created_by, t.deleted_at, t.deleted_by"""
# How many of its bundled properties aren't deleted
_PROPERTY_COUNT = """,
            (SELECT count(*)::int
             FROM icid.task_order_properties tp
             JOIN icid.properties p ON p.id = tp.property_id
             WHERE tp.task_order_id = t.id AND p.deleted_at IS NULL) AS property_count"""
# Its bundled properties that aren't deleted, each as a row of icid.properties, ordered as a project's list is
_BUNDLED_PROPERTIES = """,
            COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY """ + PROPERTY_ORDER + """)
                      FROM icid.task_order_properties tp
                      JOIN icid.properties p ON p.id = tp.property_id
                      WHERE tp.task_order_id = t.id AND p.deleted_at IS NULL), '[]'::jsonb) AS properties"""
# A task order as every single-row function here returns it
_DETAIL_COLUMNS = _TASK_ORDER_COLUMNS + _BUNDLED_PROPERTIES


def list_task_orders(project_id: str, include_deleted: bool = False) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a project's task orders, the soft-deleted ones only when asked for, each with its count of live bundled properties.
    Takes the project id and whether to include soft-deleted task orders.
    Returns the task order dicts (with property_count) ordered by number, or None on failure.
    """
    sql = (
        "\n        SELECT"
        + _TASK_ORDER_COLUMNS
        + _PROPERTY_COUNT
        + """
        FROM icid.task_orders t
        WHERE t.project_id = %s AND (%s OR t.deleted_at IS NULL)
        ORDER BY t.task_order_number;"""
    )
    return run_query(sql, (project_id, include_deleted))


def get_task_order(task_order_id: UUID, include_properties: bool = True) -> Optional[dict[str, Any]]:
    """
    Fetch one task order, deleted or not.
    Takes the task order's id and whether to include its live bundled properties.
    Returns the task order dict (with properties, a list of property dicts, when asked for), or None if none matches.
    """
    columns = _DETAIL_COLUMNS if include_properties else _TASK_ORDER_COLUMNS
    sql = "\n        SELECT" + columns + "\n        FROM icid.task_orders t\n        WHERE t.id = %s;"
    rows = run_query(sql, (task_order_id,))
    return rows[0] if rows else None


def get_task_order_by_number(project_id: str, task_order_number: str) -> Optional[dict[str, Any]]:
    """
    Fetch the task order of a project that holds a number: the one that isn't deleted.
    Takes the project id and the number, matched exactly.
    Returns the task order dict (without properties), or None if no live task order of the project has it.
    """
    sql = (
        "\n        SELECT"
        + _TASK_ORDER_COLUMNS
        + """
        FROM icid.task_orders t
        WHERE t.project_id = %s AND t.task_order_number = %s AND t.deleted_at IS NULL;"""
    )
    rows = run_query(sql, (project_id, task_order_number))
    return rows[0] if rows else None


def create_task_order(project_id: str, created_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a task order to a project. Adds nothing when a live task order of the project already has the number.
    Takes the project id, the uuid of the admin adding it, and the values keyed by column (task_order_number, issued_date, estimate_notes).
    Returns the new task order dict (with its properties, none yet), or None when the number is taken.
    """
    sql = (
        """
        INSERT INTO icid.task_orders AS t (project_id, task_order_number, issued_date, estimate_notes, created_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING"""
        + _DETAIL_COLUMNS
        + ";"
    )
    params = (
        project_id, data["task_order_number"], data.get("issued_date"), data.get("estimate_notes"), created_by_uuid,
    )
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_task_order(task_order_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Change some of a task order; a column left out keeps its value. A live task order is left alone when another live task order of its project already has the number it would end up with.
    Takes the task order's id and the new values keyed by column (task_order_number; issued_date and estimate_notes, where None clears).
    Returns the updated task order dict (with its properties), or None when none matches or the change would collide.
    """
    sql = (
        """
        UPDATE icid.task_orders t
        SET task_order_number = COALESCE(%s, t.task_order_number),
            issued_date = CASE WHEN %s THEN %s::date ELSE t.issued_date END,
            estimate_notes = CASE WHEN %s THEN %s ELSE t.estimate_notes END,
            updated_at = now()
        WHERE t.id = %s
          AND (
              t.deleted_at IS NOT NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM icid.task_orders other
                  WHERE other.project_id = t.project_id
                    AND other.task_order_number = COALESCE(%s, t.task_order_number)
                    AND other.id <> t.id
                    AND other.deleted_at IS NULL
              )
          )
        RETURNING"""
        + _DETAIL_COLUMNS
        + ";"
    )
    params = (
        data.get("task_order_number"), "issued_date" in data, data.get("issued_date"),
        "estimate_notes" in data, data.get("estimate_notes"), task_order_id, data.get("task_order_number"),
    )
    try:
        rows = run_query(sql, params)
    except UniqueViolation:
        # Another task order took the number between the statement's own check and its write
        return None
    return rows[0] if rows else None


def soft_delete_task_order(task_order_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a task order deleted, with when and by whom; it frees its number and keeps its bundle.
    Takes the task order's id and the uuid of the admin deleting it.
    Returns the updated task order dict (with its properties), or None if none matches or it is already deleted.
    """
    sql = (
        """
        UPDATE icid.task_orders t
        SET deleted_at = now(),
            deleted_by = %s,
            updated_at = now()
        WHERE t.id = %s AND t.deleted_at IS NULL
        RETURNING"""
        + _DETAIL_COLUMNS
        + ";"
    )
    rows = run_query(sql, (deleted_by_uuid, task_order_id))
    return rows[0] if rows else None


def undelete_task_order(task_order_id: UUID) -> Optional[dict[str, Any]]:
    """
    Bring a deleted task order back, with the bundle it had: clear deleted_at and deleted_by. Changes nothing when a live task order of its project now has its number.
    Takes the task order's id.
    Returns the updated task order dict (with its properties), or None if none matches, it isn't deleted, or its number is taken.
    """
    sql = (
        """
        UPDATE icid.task_orders t
        SET deleted_at = NULL,
            deleted_by = NULL,
            updated_at = now()
        WHERE t.id = %s
          AND t.deleted_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM icid.task_orders other
              WHERE other.project_id = t.project_id
                AND other.task_order_number = t.task_order_number
                AND other.id <> t.id
                AND other.deleted_at IS NULL
          )
        RETURNING"""
        + _DETAIL_COLUMNS
        + ";"
    )
    try:
        rows = run_query(sql, (task_order_id,))
    except UniqueViolation:
        # Another task order took the number between the statement's own check and its write
        return None
    return rows[0] if rows else None


def bundle_property(task_order_id: UUID, property_id: UUID, added_by: UUID) -> Optional[dict[str, Any]]:
    """
    Put a property into a task order. Adds nothing unless both exist, neither is deleted and they share a project, or when the property is already in it.
    Takes the task order's id, the property's id and the uuid of the admin adding it.
    Returns the join row (task_order_id, property_id, added_at, added_by), or None when nothing was added.
    """
    sql = """
        INSERT INTO icid.task_order_properties (task_order_id, property_id, added_by)
        SELECT t.id, p.id, %s
        FROM icid.task_orders t
        JOIN icid.properties p ON p.project_id = t.project_id
        WHERE t.id = %s AND p.id = %s AND t.deleted_at IS NULL AND p.deleted_at IS NULL
        ON CONFLICT DO NOTHING
        RETURNING task_order_id, property_id, added_at, added_by;
    """
    rows = run_query(sql, (added_by, task_order_id, property_id))
    return rows[0] if rows else None


def is_property_bundled(task_order_id: UUID, property_id: UUID) -> bool:
    """
    Check whether a property is in a task order, whether or not either is deleted.
    Takes the task order's id and the property's id.
    Returns True if the join row exists.
    """
    sql = """
        SELECT 1
        FROM icid.task_order_properties
        WHERE task_order_id = %s AND property_id = %s;
    """
    return bool(run_query(sql, (task_order_id, property_id)))


def unbundle_property(task_order_id: UUID, property_id: UUID) -> bool:
    """
    Take a property out of a task order, whether or not either is deleted.
    Takes the task order's id and the property's id.
    Returns True when the join row was deleted, False when the property wasn't in the task order.
    """
    sql = """
        DELETE FROM icid.task_order_properties
        WHERE task_order_id = %s AND property_id = %s
        RETURNING task_order_id;
    """
    return bool(run_query(sql, (task_order_id, property_id)))
