from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries._shared import PAY_ITEM_OF_PROJECT, placeholders, write_values

# A flag's net area: its shape's area less its non-assessable areas and the features subtracted from it, never below 0
_NET = "GREATEST(f.computed_quantity - naa.quantity - sub.quantity, 0)"
# What counts as a flag's quantity, in square feet: its override, or else its net area. Needs QUANTITY_LATERALS.
FLAG_QUANTITY = f"COALESCE(f.reported_quantity, {_NET})"
# A flag's areas are its live ones, and, once it is deleted, those deleted with it (at the same moment); the features
# subtracted from it are the live ones. Read for a row source named f.
QUANTITY_LATERALS = """
        CROSS JOIN LATERAL (
            SELECT count(*) AS area_count, COALESCE(sum(na.computed_quantity), 0) AS quantity
            FROM icid.non_assessable_areas na
            WHERE na.flag_id = f.id AND (na.deleted_at IS NULL OR na.deleted_at = f.deleted_at)
        ) naa
        CROSS JOIN LATERAL (
            SELECT COALESCE(sum(COALESCE(ft.reported_quantity, ft.computed_quantity)), 0) AS quantity
            FROM icid.features ft
            WHERE ft.flag_id = f.id AND ft.area_impact = 'subtract' AND ft.deleted_at IS NULL
        ) sub"""
# A flag's pay item's unit (ci, its contract item) in short form, as the units a flag measures are spelled: SF, SY,
# CY, EA
_UNIT_LETTERS = "upper(regexp_replace(ci.unit, '[^A-Za-z]', '', 'g'))"
FLAG_UNIT = f"(CASE {_UNIT_LETTERS} WHEN 'EACH' THEN 'EA' ELSE {_UNIT_LETTERS} END)"
# A flag's quantity in its pay item's unit: its square feet (SF), those over 9 (SY), its volume at its thickness, 4 in
# (4in, 7in_as_4in) or 7 in (7in), in cubic yards (CY), or 1 (EA); NULL for any other unit. Needs QUANTITY_LATERALS
# and ci.
FLAG_AMOUNT = f"""(CASE {FLAG_UNIT}
                WHEN 'SF' THEN {FLAG_QUANTITY}
                WHEN 'SY' THEN {FLAG_QUANTITY} / 9
                WHEN 'CY' THEN {FLAG_QUANTITY} * (CASE f.surveyed_thickness WHEN '7in' THEN 7 ELSE 4 END) / 324
                WHEN 'EA' THEN 1
            END)"""
# A flag as every function here returns it: its own columns, its PIR's property and project, its non-assessable areas
# and subtracted features, what counts as its quantity, and whether its page, PIR and property are deleted (what the
# routes check before a write). Read from a row source named f (the table, or a CTE of rows just written).
_FLAG_COLUMNS = (
    """
            f.id, f.pir_id, f.pir_page_id, pir.property_id, p.project_id, f.display_order, f.added_in_field, f.shape, f.dimensions,
            f.sketch_x, f.sketch_y, f.rotation, f.surveyed_mark, f.surveyed_is_assessable,
            f.surveyed_non_assessable_reason, f.surveyed_thickness, f.is_constructed, f.pay_item_id, f.defect_codes,
            f.computed_quantity, f.reported_quantity,
            naa.area_count AS non_assessable_areas_count, naa.quantity AS non_assessable_quantity,
            sub.quantity AS subtracted_quantity, """
    + _NET
    + """ AS net_quantity,
            COALESCE(f.reported_quantity, """
    + _NET
    + """) AS quantity,
            (f.reported_quantity IS NOT NULL) AS is_overridden,
            f.quantity_delta_reason, f.created_at, f.updated_at, f.created_by, f.deleted_at, f.deleted_by,
            f.claimed_by_idr_id, f.constructed_on,
            (SELECT claimer.idr_number FROM icid.idrs claimer WHERE claimer.idr_id = f.claimed_by_idr_id)
                AS claimed_by_idr_number,
            pg.deleted_at AS page_deleted_at, pir.deleted_at AS pir_deleted_at, p.deleted_at AS property_deleted_at"""
)
# A flag's PIR, property and page, and its areas and subtracted features (QUANTITY_LATERALS)
_FROM = """
        JOIN icid.pirs pir ON pir.id = f.pir_id
        JOIN icid.properties p ON p.id = pir.property_id
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id""" + QUANTITY_LATERALS
# The columns a write sets from the request, in the order their values are passed, each with its type (so a NULL
# parameter is typed, not text)
_WRITABLE_TYPES = {
    "display_order": "int", "added_in_field": "boolean", "shape": "text", "dimensions": "jsonb", "sketch_x": "real",
    "sketch_y": "real", "rotation": "real", "surveyed_mark": "text", "surveyed_is_assessable": "boolean",
    "surveyed_non_assessable_reason": "text", "surveyed_thickness": "text", "pay_item_id": "uuid",
    "reported_quantity": "numeric", "quantity_delta_reason": "text", "defect_codes": "text[]",
}
# What the PIR import writes, as this module does
WRITABLE_TYPES = _WRITABLE_TYPES
_WRITABLE = tuple(_WRITABLE_TYPES)
_PLACEHOLDERS = placeholders(_WRITABLE_TYPES)
# The pay item, if any, must be one of the PIR's project's: p is the PIR's property
_PAY_ITEM_OF_PROJECT = PAY_ITEM_OF_PROJECT


def _values(data: dict[str, Any]) -> list[Any]:
    """
    Order a flag's writable values as _WRITABLE names them, the dimensions as JSON.
    Takes the values keyed by column (every writable column present).
    Returns the list of parameters.
    """
    return write_values(_WRITABLE_TYPES, data)


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
    Mark a flag deleted, with when and by whom, and its live non-assessable areas with it (the same moment, so an un-delete brings back those).
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
        ),
        areas AS (
            UPDATE icid.non_assessable_areas
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE flag_id IN (SELECT id FROM f) AND deleted_at IS NULL
            RETURNING non_assessable_area_id
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _FROM
        + "\n        CROSS JOIN (SELECT count(*) FROM areas) AS areas_retired;"
    )
    rows = run_query(sql, (deleted_by_uuid, flag_id, deleted_by_uuid))
    return rows[0] if rows else None


def undelete_flag(flag_id: UUID) -> Optional[dict[str, Any]]:
    """
    Bring a deleted flag back: clear deleted_at and deleted_by, on it and on the non-assessable areas deleted with it. Only a flag on a page that is still live comes back.
    Takes the flag's id.
    Returns the updated flag dict as the statement wrote it (its areas not yet counted: read it again for them), or None if no flag matches, it isn't deleted, it has no page or its page was replaced.
    """
    sql = (
        """
        WITH before AS (
            SELECT id, deleted_at FROM icid.flags WHERE id = %s
        ),
        f AS (
            UPDATE icid.flags flag
            SET deleted_at = NULL, deleted_by = NULL, updated_at = now()
            FROM icid.pir_pages page
            WHERE flag.id = %s AND flag.deleted_at IS NOT NULL
              AND page.pir_page_id = flag.pir_page_id AND page.deleted_at IS NULL
            RETURNING flag.*
        ),
        areas AS (
            UPDATE icid.non_assessable_areas na
            SET deleted_at = NULL, deleted_by = NULL, updated_at = now()
            FROM before
            WHERE before.id IN (SELECT id FROM f) AND na.flag_id = before.id AND na.deleted_at = before.deleted_at
            RETURNING na.non_assessable_area_id
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _FROM
        + "\n        CROSS JOIN (SELECT count(*) FROM areas) AS areas_restored;"
    )
    rows = run_query(sql, (flag_id, flag_id))
    return rows[0] if rows else None
