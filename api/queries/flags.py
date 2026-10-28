from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query
from api.queries._shared import PAY_ITEM_OF_PROJECT, placeholders, write_values
from api.queries.flag_edits import flag_edit_rows_cte
from api.queries.flag_measurements import (
    REASON_DELETE,
    REASON_EDIT,
    REASON_UNDELETE,
    remesh_ctes,
    snapshot_intent,
)

# A flag's net area (Slice Y3a: through its measurement, fm): its part of the measurement's outer area less the
# measurement's deductions (ded), to the cent, then, on a solo measurement only, less its own non-assessable areas and
# the features subtracted from it, never below 0. A solo measurement's outer area is the flag's own computed_quantity,
# its share 1 and its deductions none, so this is the flag's shape's area less those, as before Y3a. A bundle's
# deductions are its members' covers (Slice Y3b), so its members' own aren't taken off again.
_NET = ("GREATEST(round((fm.outer_area - ded.quantity) * f.area_share_fraction, 2)"
        " - CASE WHEN fm.surveyed_is_assessable IS NULL THEN naa.quantity + sub.quantity ELSE 0 END, 0)")
# What counts as a flag's quantity, in square feet: its override, or else its net area. Needs QUANTITY_LATERALS.
FLAG_QUANTITY = f"COALESCE(f.reported_quantity, {_NET})"


def quantity_laterals(measurements: str = "icid.flag_measurements") -> str:
    """
    The joins a flag's quantity reads, for a row source named f: its measurement (fm) and the measurement's deductions (ded), its areas (its live ones, and, once it is deleted, those deleted with it, at the same moment) and the live features subtracted from it.
    Takes where its measurement is read from: the table, or the name of a CTE of measurements the same statement writes (a statement can't read its own writes from the table).
    Returns the SQL, to follow the FROM clause that defines f.
    """
    return f"""
        JOIN {measurements} fm ON fm.measurement_id = f.measurement_id
        CROSS JOIN LATERAL (
            SELECT COALESCE(sum((deduction ->> 'area_sf')::numeric), 0) AS quantity
            FROM jsonb_array_elements(fm.deductions) deduction
        ) ded
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


# quantity_laterals over the table, for every statement that only reads flags
QUANTITY_LATERALS = quantity_laterals()


def solo_measurements_insert(flags: str) -> str:
    """
    The INSERT that gives flags just added their solo measurements (Slice Y3a): each its own, of the flag's shape, dimensions and area (computed_quantity), under the measurement_id the flag's INSERT gave it, created by whoever added the flag.
    Takes the name of a CTE earlier in the statement holding the new flags (measurement_id, pir_id, shape, dimensions, computed_quantity, created_by); written here, never from a request.
    Returns the INSERT, its RETURNING every column (a measurement row source for quantity_laterals); no parameters.
    """
    return f"""INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, outer_area,
                                                created_by)
            SELECT measurement_id, pir_id, shape, dimensions, computed_quantity, created_by FROM {flags}
            RETURNING *"""
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


def _from(measurements: str = "icid.flag_measurements") -> str:
    """
    A flag's PIR, property and page, and what its quantity reads (quantity_laterals), for a row source named f.
    Takes where its measurement is read from (see quantity_laterals).
    Returns the SQL, to follow "FROM <f>".
    """
    return """
        JOIN icid.pirs pir ON pir.id = f.pir_id
        JOIN icid.properties p ON p.id = pir.property_id
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id""" + quantity_laterals(measurements)


# _from over the measurements table, for every statement that doesn't write one
_FROM = _from()
# _from over the measurements as a statement carrying the remesh leaves them (rm_m, remesh_ctes)
_FROM_REMESHED = _from("rm_m")
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


def list_pir_flag_nos(pir_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the PIR flag numbers (Slice Y3a) its live flags on its live pages have, for a Smart PIR import to check against.
    Takes the PIR's id.
    Returns [{page_number, pir_flag_no}], or None on failure.
    """
    sql = """
        SELECT pg.page_number, f.pir_flag_no
        FROM icid.flags f
        JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
        WHERE f.pir_id = %s AND f.deleted_at IS NULL AND pg.deleted_at IS NULL AND f.pir_flag_no IS NOT NULL;
    """
    return run_query(sql, (pir_id,))


def get_flag_placement(flag_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch where a flag sits on its page, for checking an area against it: its shape, dimensions and place, and its page's scale.
    Takes the flag's id.
    Returns [{shape, dimensions, sketch_x, sketch_y, rotation, scale_px_per_ft}] ([] for no such flag), or None on failure.
    """
    sql = """
        SELECT f.shape, f.dimensions, f.sketch_x, f.sketch_y, f.rotation, pg.scale_px_per_ft
        FROM icid.flags f
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
        WHERE f.id = %s;
    """
    return run_query(sql, (flag_id,))


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
    Add a flag to a page of a PIR (and so to the PIR), with its solo measurement (Slice Y3a: its shape, dimensions and area), in one statement. Adds nothing when the page doesn't exist or was replaced, or the pay item isn't one of its project's.
    Takes the page's id, the uuid of the user adding it, the values keyed by column (every writable column) and the area computed from its shape.
    Returns the new flag dict, or None when nothing was added.
    """
    sql = (
        """
        WITH f AS (
            INSERT INTO icid.flags ("""
        + ", ".join(_WRITABLE)
        + """, computed_quantity, pir_id, pir_page_id, created_by, measurement_id)
            SELECT """
        + ", ".join(_PLACEHOLDERS)
        + """, %s::numeric, pir.id, page.pir_page_id, %s::uuid, gen_random_uuid()
            FROM icid.pir_pages page
            JOIN icid.pirs pir ON pir.id = page.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE page.pir_page_id = %s AND page.deleted_at IS NULL
              AND """
        + _PAY_ITEM_OF_PROJECT
        + """
            RETURNING *
        ),
        m AS (
            """
        + solo_measurements_insert("f")
        + """
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _from("m")
        + ";"
    )
    params = (
        *_values(data), computed_area, created_by_uuid, pir_page_id, data.get("pay_item_id"), data.get("pay_item_id"),
    )
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_flag(flag_id: UUID, data: dict[str, Any], computed_area: Decimal, editor_uuid: UUID,
                target_id: Optional[UUID] = None) -> Optional[dict[str, Any]]:
    """
    Rewrite a flag's writable columns with its merged record, and its computed quantity, its measurement following it in the same statement (remesh_ctes, Slice Y3b-1): on a solo measurement its shape, dimensions and area (stamped updated by the editor only when one of them changed, as in Y3a); a change of classification moves it into the bundle given, if still open to it, or out of a bundle it was in. Changes nothing when the pay item isn't one of its PIR's project's. A change of surveyed_is_assessable or surveyed_non_assessable_reason adds its flag_edits rows in the same statement (Slice Y1.5).
    Takes the flag's id, the values keyed by column (every writable column), the area computed from its shape, the editing user's uuid and the bundle the remesh picked for it to join (None for none).
    Returns the updated flag dict, or None when no flag matches or the pay item is another project's.
    """
    assignments = ",\n                ".join(
        f"{column} = {placeholder}" for column, placeholder in zip(_WRITABLE, _PLACEHOLDERS)
    )
    # intent names the flag by the UPDATE's own conditions, so the remesh and the write see the same flag
    # old is the flag as it was: an UPDATE's FROM reads the rows before the statement changes them
    sql = (
        f"""
        WITH intent AS (
            SELECT flag.id AS flag_id, 'edit'::text AS event, %s::uuid AS target_id, %s::text AS new_thickness,
                   %s::boolean AS new_assessable, %s::uuid AS new_pay_item, %s::numeric AS new_computed,
                   %s::text AS new_shape, %s::jsonb AS new_dims, '{REASON_EDIT}'::text AS reason
            FROM icid.flags flag
            JOIN icid.pirs pir ON pir.id = flag.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE flag.id = %s AND {_PAY_ITEM_OF_PROJECT}
        )"""
        + remesh_ctes("%s::uuid")
        + """,
        f AS (
            UPDATE icid.flags flag
            SET """
        + assignments
        + """,
                computed_quantity = %s::numeric,
                measurement_id = rf.measurement_id,
                area_share_fraction = rf.share,
                updated_at = now()
            FROM icid.pirs pir
            JOIN icid.properties p ON p.id = pir.property_id
            JOIN icid.flags old ON true
            JOIN rm_flags rf ON true
            WHERE flag.id = %s AND pir.id = flag.pir_id AND old.id = flag.id AND rf.flag_id = flag.id
              AND """
        + _PAY_ITEM_OF_PROJECT
        + """
            RETURNING flag.*, old.surveyed_is_assessable AS old_assessable, old.surveyed_non_assessable_reason AS old_reason
        ),
        edits AS (
            """
        + flag_edit_rows_cte("f")
        + """
        )
        SELECT"""
        + _FLAG_COLUMNS
        + "\n        FROM f"
        + _FROM_REMESHED
        + "\n        CROSS JOIN (SELECT count(*) FROM edits) AS edits_written;"
    )
    dimensions = data.get("dimensions")
    intent = (target_id, data.get("surveyed_thickness"), data.get("surveyed_is_assessable"), data.get("pay_item_id"),
              computed_area, data.get("shape"), None if dimensions is None else Jsonb(dimensions), flag_id,
              data.get("pay_item_id"), data.get("pay_item_id"))
    params = (*intent, editor_uuid, *_values(data), computed_area, flag_id, data.get("pay_item_id"),
              data.get("pay_item_id"), None, editor_uuid)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def soft_delete_flag(flag_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a flag deleted, with when and by whom, and its live non-assessable areas with it (the same moment, so an un-delete brings back those). An unclaimed flag leaves a bundle it is in for a measurement of its own, in the same statement (remesh_ctes, Slice Y3b-1).
    Takes the flag's id and the uuid of the user deleting it.
    Returns the updated flag dict, or None if no flag matches or it is already deleted.
    """
    sql = (
        """
        WITH intent AS (
            """
        + snapshot_intent("delete", REASON_DELETE, "SELECT id FROM icid.flags WHERE id = %s AND deleted_at IS NULL")
        + """
        )"""
        + remesh_ctes("%s::uuid")
        + """,
        f AS (
            UPDATE icid.flags flag
            SET deleted_at = now(), deleted_by = %s, updated_at = now(), measurement_id = rf.measurement_id,
                area_share_fraction = rf.share
            FROM rm_flags rf
            WHERE flag.id = %s AND flag.deleted_at IS NULL AND rf.flag_id = flag.id
            RETURNING flag.*
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
        + _FROM_REMESHED
        + "\n        CROSS JOIN (SELECT count(*) FROM areas) AS areas_retired;"
    )
    rows = run_query(sql, (flag_id, deleted_by_uuid, deleted_by_uuid, flag_id, deleted_by_uuid))
    return rows[0] if rows else None


def undelete_flag(flag_id: UUID) -> Optional[dict[str, Any]]:
    """
    Bring a deleted flag back: clear deleted_at and deleted_by, on it and on the non-assessable areas deleted with it. Only a flag on a page that is still live comes back. A flag still in a bundle comes back with a measurement of its own (remesh_ctes, Slice Y3b-1), not into the bundle, which may have moved on.
    Takes the flag's id.
    Returns the updated flag dict as the statement wrote it (its areas not yet counted: read it again for them), or None if no flag matches, it isn't deleted, it has no page or its page was replaced.
    """
    restorable = """SELECT flag.id FROM icid.flags flag
                    JOIN icid.pir_pages page ON page.pir_page_id = flag.pir_page_id AND page.deleted_at IS NULL
                    WHERE flag.id = %s AND flag.deleted_at IS NOT NULL"""
    sql = (
        """
        WITH before AS (
            SELECT id, deleted_at FROM icid.flags WHERE id = %s
        ),
        intent AS (
            """
        + snapshot_intent("undelete", REASON_UNDELETE, restorable)
        + """
        )"""
        + remesh_ctes()
        + """,
        f AS (
            UPDATE icid.flags flag
            SET deleted_at = NULL, deleted_by = NULL, updated_at = now(), measurement_id = rf.measurement_id,
                area_share_fraction = rf.share
            FROM icid.pir_pages page, rm_flags rf
            WHERE flag.id = %s AND flag.deleted_at IS NOT NULL
              AND page.pir_page_id = flag.pir_page_id AND page.deleted_at IS NULL AND rf.flag_id = flag.id
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
        + _FROM_REMESHED
        + "\n        CROSS JOIN (SELECT count(*) FROM areas) AS areas_restored;"
    )
    rows = run_query(sql, (flag_id, flag_id, flag_id))
    return rows[0] if rows else None