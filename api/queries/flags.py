from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query

# A flag as every function here returns it: its own columns, its PIR's property and project, what counts as its
# quantity, and whether its page, PIR and property are deleted (what the routes check before a write). Read from a
# row source named f (the table, or a CTE of rows just written).
_FLAG_COLUMNS = """
            f.id, f.pir_id, f.pir_page_id, pir.property_id, p.project_id, f.display_order, f.added_in_field, f.shape, f.dimensions,
            f.sketch_x, f.sketch_y, f.rotation, f.surveyed_mark, f.surveyed_is_assessable,
            f.surveyed_non_assessable_reason, f.surveyed_thickness, f.is_constructed, f.pay_item_id,
            f.computed_quantity, f.reported_quantity,
            COALESCE(f.reported_quantity, f.computed_quantity) AS quantity,
            (f.reported_quantity IS NOT NULL) AS is_overridden,
            f.quantity_delta_reason, f.created_at, f.updated_at, f.created_by, f.deleted_at, f.deleted_by,
            pg.deleted_at AS page_deleted_at, pir.deleted_at AS pir_deleted_at, p.deleted_at AS property_deleted_at"""
_FROM = """
        JOIN icid.pirs pir ON pir.id = f.pir_id
        JOIN icid.properties p ON p.id = pir.property_id
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id"""
# The columns a write sets from the request, in the order their values are passed, each with its type (so a NULL
# parameter is typed, not text)
_WRITABLE_TYPES = {
    "display_order": "int", "added_in_field": "boolean", "shape": "text", "dimensions": "jsonb", "sketch_x": "real",
    "sketch_y": "real", "rotation": "real", "surveyed_mark": "text", "surveyed_is_assessable": "boolean",
    "surveyed_non_assessable_reason": "text", "surveyed_thickness": "text", "pay_item_id": "uuid",
    "reported_quantity": "numeric", "quantity_delta_reason": "text",
}
_WRITABLE = tuple(_WRITABLE_TYPES)
_PLACEHOLDERS = [f"%s::{_WRITABLE_TYPES[column]}" for column in _WRITABLE]
# The pay item, if any, must be one of the PIR's project's: p is the PIR's property
_PAY_ITEM_OF_PROJECT = """(%s::uuid IS NULL OR EXISTS (
              SELECT 1 FROM icid.contract_items ci
              WHERE ci.contract_item_id = %s::uuid AND ci.project_id = p.project_id
          ))"""


def _values(data: dict[str, Any]) -> list[Any]:
    """
    Order a flag's writable values as _WRITABLE names them, the dimensions as JSON.
    Takes the values keyed by column (every writable column present).
    Returns the list of parameters.
    """
    return [Jsonb(data[column]) if column == "dimensions" else data.get(column) for column in _WRITABLE]


def list_flags(pir_id: UUID, include_deleted: bool = False) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a PIR's flags across its pages: the live ones on its live pages, or, when asked for, every one it has had.
    Takes the PIR's id and whether to include soft-deleted flags (and those of replaced pages).
    Returns the flag dicts ordered by display_order then creation, or None on failure.
    """
    sql = (
        "\n        SELECT"
        + _FLAG_COLUMNS
        + "\n        FROM icid.flags f"
        + _FROM
        + """
        WHERE f.pir_id = %s AND (%s OR (f.deleted_at IS NULL AND pg.deleted_at IS NULL))
        ORDER BY f.display_order, f.created_at;"""
    )
    return run_query(sql, (pir_id, include_deleted))


def list_flags_for_page(pir_page_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the live flags drawn on one page.
    Takes the page's id.
    Returns the flag dicts ordered by display_order then creation, or None on failure.
    """
    sql = (
        "\n        SELECT"
        + _FLAG_COLUMNS
        + "\n        FROM icid.flags f"
        + _FROM
        + """
        WHERE f.pir_page_id = %s AND f.deleted_at IS NULL
        ORDER BY f.display_order, f.created_at;"""
    )
    return run_query(sql, (pir_page_id,))


def get_flag(flag_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one flag, deleted or not.
    Takes the flag's id.
    Returns the flag dict, or None if no flag matches.
    """
    sql = "\n        SELECT" + _FLAG_COLUMNS + "\n        FROM icid.flags f" + _FROM + "\n        WHERE f.id = %s;"
    rows = run_query(sql, (flag_id,))
    return rows[0] if rows else None


def create_flag(
    pir_page_id: UUID, created_by_uuid: UUID, data: dict[str, Any], computed_area: Decimal
) -> Optional[dict[str, Any]]:
    """
    Add a flag to a page of a PIR (and so to the PIR). Adds nothing when the page doesn't exist or was replaced, or the pay item isn't one of its project's.
    Takes the page's id, the uuid of the user adding it, the values keyed by column (every writable column) and the area computed from its shape.
    Returns the new flag dict, or None when nothing was added.
    """
    sql = (
        """
        WITH f AS (
            INSERT INTO icid.flags ("""
        + ", ".join(_WRITABLE)
        + """, computed_quantity, pir_id, pir_page_id, created_by)
            SELECT """
        + ", ".join(_PLACEHOLDERS)
        + """, %s::numeric, pir.id, page.pir_page_id, %s::uuid
            FROM icid.pir_pages page
            JOIN icid.pirs pir ON pir.id = page.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE page.pir_page_id = %s AND page.deleted_at IS NULL
              AND """
        + _PAY_ITEM_OF_PROJECT
        + """
            RETURNING *
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _FROM
        + ";"
    )
    params = (
        *_values(data), computed_area, created_by_uuid, pir_page_id, data.get("pay_item_id"), data.get("pay_item_id"),
    )
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_flag(flag_id: UUID, data: dict[str, Any], computed_area: Decimal) -> Optional[dict[str, Any]]:
    """
    Rewrite a flag's writable columns with its merged record, and its computed quantity. Changes nothing when the pay item isn't one of its PIR's project's.
    Takes the flag's id, the values keyed by column (every writable column) and the area computed from its shape.
    Returns the updated flag dict, or None when no flag matches or the pay item is another project's.
    """
    assignments = ",\n                ".join(
        f"{column} = {placeholder}" for column, placeholder in zip(_WRITABLE, _PLACEHOLDERS)
    )
    sql = (
        """
        WITH f AS (
            UPDATE icid.flags flag
            SET """
        + assignments
        + """,
                computed_quantity = %s::numeric,
                updated_at = now()
            FROM icid.pirs pir
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE flag.id = %s AND pir.id = flag.pir_id
              AND """
        + _PAY_ITEM_OF_PROJECT
        + """
            RETURNING flag.*
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _FROM
        + ";"
    )
    params = (*_values(data), computed_area, flag_id, data.get("pay_item_id"), data.get("pay_item_id"))
    rows = run_query(sql, params)
    return rows[0] if rows else None


def soft_delete_flag(flag_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a flag deleted, with when and by whom.
    Takes the flag's id and the uuid of the user deleting it.
    Returns the updated flag dict, or None if no flag matches or it is already deleted.
    """
    sql = (
        """
        WITH f AS (
            UPDATE icid.flags
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE id = %s AND deleted_at IS NULL
            RETURNING *
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _FROM
        + ";"
    )
    rows = run_query(sql, (deleted_by_uuid, flag_id))
    return rows[0] if rows else None


def undelete_flag(flag_id: UUID) -> Optional[dict[str, Any]]:
    """
    Bring a deleted flag back: clear deleted_at and deleted_by. Only a flag on a page that is still live comes back.
    Takes the flag's id.
    Returns the updated flag dict, or None if no flag matches, it isn't deleted, it has no page or its page was replaced.
    """
    sql = (
        """
        WITH f AS (
            UPDATE icid.flags flag
            SET deleted_at = NULL, deleted_by = NULL, updated_at = now()
            FROM icid.pir_pages page
            WHERE flag.id = %s AND flag.deleted_at IS NOT NULL
              AND page.pir_page_id = flag.pir_page_id AND page.deleted_at IS NULL
            RETURNING flag.*
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _FROM
        + ";"
    )
    rows = run_query(sql, (flag_id,))
    return rows[0] if rows else None
