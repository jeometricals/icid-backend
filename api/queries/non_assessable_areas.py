from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries._shared import placeholders, write_values
from api.queries.flag_measurements import REASON_COVERS, remesh_ctes, snapshot_intent

# A non-assessable area as every function here returns it: its own columns, and its flag's page, PIR, property,
# project and deleted states (what the routes check before a write). Read from a row source named na.
_AREA_COLUMNS = """
            na.non_assessable_area_id, na.flag_id, na.display_order, na.shape, na.dimensions, na.sketch_x, na.sketch_y,
            na.rotation, na.reason, na.computed_quantity, na.created_at, na.updated_at, na.created_by, na.deleted_at,
            na.deleted_by,
            f.pir_page_id, f.pir_id, pir.property_id, p.project_id, f.deleted_at AS flag_deleted_at,
            pg.deleted_at AS page_deleted_at, pir.deleted_at AS pir_deleted_at, p.deleted_at AS property_deleted_at"""
_FROM = """
        JOIN icid.flags f ON f.id = na.flag_id
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
        JOIN icid.pirs pir ON pir.id = f.pir_id
        JOIN icid.properties p ON p.id = pir.property_id"""
# The columns a write sets from the request, in the order their values are passed, each with its type
_WRITABLE_TYPES = {
    "display_order": "int", "shape": "text", "dimensions": "jsonb", "sketch_x": "real", "sketch_y": "real",
    "rotation": "real", "reason": "text",
}
# What the PIR import writes, as this module does
WRITABLE_TYPES = _WRITABLE_TYPES
_WRITABLE = tuple(_WRITABLE_TYPES)
_PLACEHOLDERS = placeholders(_WRITABLE_TYPES)
# The areas a statement writes (na) as the remesh reads covers, and their flags' measurements resynced with them (Slice
# Y3b-1: a bundle's deductions are its members' covers)
_RESYNC = (
    """,
        intent AS (
            """
    + snapshot_intent("covers", REASON_COVERS, "SELECT flag_id FROM na")
    + """
        ),
        rm_written AS (
            SELECT flag_id, 'non_ass_area'::text AS kind, non_assessable_area_id AS source_id,
                   computed_quantity AS area_sf, deleted_at IS NULL AS live
            FROM na
        )"""
    + remesh_ctes(written="rm_written")
)


def _written(cte: str) -> str:
    """
    A statement that writes areas in a CTE named na and returns them as every function here does; their flags'
    measurements are resynced in it (_RESYNC).
    Takes the CTE's body.
    Returns the SQL.
    """
    return ("\n        WITH na AS (" + cte + "\n        )" + _RESYNC + "\n        SELECT" + _AREA_COLUMNS + "\n        FROM na"
            + _FROM + ";")


def list_areas_for_flag(flag_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a flag's live non-assessable areas.
    Takes the flag's id.
    Returns the area dicts by display_order, or None on failure.
    """
    sql = (
        "\n        SELECT" + _AREA_COLUMNS + "\n        FROM icid.non_assessable_areas na" + _FROM
        + "\n        WHERE na.flag_id = %s AND na.deleted_at IS NULL\n        ORDER BY na.display_order;"
    )
    return run_query(sql, (flag_id,))


def get_area(non_assessable_area_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one non-assessable area, deleted or not.
    Takes the area's id.
    Returns the area dict, or None if no area matches.
    """
    sql = (
        "\n        SELECT" + _AREA_COLUMNS + "\n        FROM icid.non_assessable_areas na" + _FROM
        + "\n        WHERE na.non_assessable_area_id = %s;"
    )
    rows = run_query(sql, (non_assessable_area_id,))
    return rows[0] if rows else None


def create_area(
    flag_id: UUID, created_by_uuid: UUID, data: dict[str, Any], computed_area: Decimal
) -> Optional[dict[str, Any]]:
    """
    Add a non-assessable area to a live flag on a live page. Adds nothing when the flag doesn't exist or is deleted, its page was replaced, or a live area of the flag has the display_order.
    Takes the flag's id, the uuid of the user adding it, the values keyed by column (every writable column) and the area of its shape.
    Returns the new area dict, or None when nothing was added.
    """
    sql = _written(
        """
            INSERT INTO icid.non_assessable_areas (""" + ", ".join(_WRITABLE) + """, computed_quantity, flag_id, created_by)
            SELECT """ + ", ".join(_PLACEHOLDERS) + """, %s::numeric, flag.id, %s::uuid
            FROM icid.flags flag
            JOIN icid.pir_pages page ON page.pir_page_id = flag.pir_page_id AND page.deleted_at IS NULL
            WHERE flag.id = %s AND flag.deleted_at IS NULL
            ON CONFLICT DO NOTHING
            RETURNING *"""
    )
    params = (*write_values(_WRITABLE_TYPES, data), computed_area, created_by_uuid, flag_id)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_area(non_assessable_area_id: UUID, data: dict[str, Any], computed_area: Decimal) -> Optional[dict[str, Any]]:
    """
    Rewrite a non-assessable area's writable columns with its merged record, and its computed area. Changes nothing when another live area of its flag has the display_order.
    Takes the area's id, the values keyed by column (every writable column) and the area of its shape.
    Returns the updated area dict, or None when no area matches or the display_order is taken.
    """
    assignments = ",\n                ".join(f"{col} = {ph}" for col, ph in zip(_WRITABLE, _PLACEHOLDERS))
    sql = _written(
        """
            UPDATE icid.non_assessable_areas area
            SET """ + assignments + """,
                computed_quantity = %s::numeric,
                updated_at = now()
            WHERE area.non_assessable_area_id = %s
              AND NOT EXISTS (
                  SELECT 1 FROM icid.non_assessable_areas other
                  WHERE other.flag_id = area.flag_id AND other.non_assessable_area_id <> area.non_assessable_area_id
                    AND other.deleted_at IS NULL AND area.deleted_at IS NULL AND other.display_order = %s::int
              )
            RETURNING area.*"""
    )
    params = (*write_values(_WRITABLE_TYPES, data), computed_area, non_assessable_area_id, data["display_order"])
    rows = run_query(sql, params)
    return rows[0] if rows else None


def soft_delete_area(non_assessable_area_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a non-assessable area deleted, with when and by whom.
    Takes the area's id and the uuid of the user deleting it.
    Returns the updated area dict, or None if no area matches or it is already deleted.
    """
    sql = _written(
        """
            UPDATE icid.non_assessable_areas
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE non_assessable_area_id = %s AND deleted_at IS NULL
            RETURNING *"""
    )
    rows = run_query(sql, (deleted_by_uuid, non_assessable_area_id))
    return rows[0] if rows else None
