from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query

# A property as every function here returns it, its enums as text
_PROPERTY_COLUMNS = """
            p.id, p.project_id, p.kind::text AS kind, p.boro::text AS boro, p.block, p.lot, p.address, p.property_type,
            p.violation_no, p.intersection_street_1, p.intersection_street_2, p.quadrant::text AS quadrant,
            p.facing_street::text AS facing_street, p.ramp_type::text AS ramp_type, p.corner_id, p.community_board, p.notes,
            p.created_at, p.updated_at, p.created_by, p.deleted_at, p.deleted_by,
            p.tax_map_frontage_ft, p.tax_map_source, p.tax_map_fetched_at, p.tax_map_depth_ft,
            p.tax_map_pdf_path, p.tax_map_pdf_source, p.tax_map_pdf_map_id, p.tax_map_pdf_effective_date,
            p.tax_map_pdf_fetched_at, p.tax_map_pdf_uploaded_by"""
# What properties are ordered by: a sidewalk's address, a ped ramp's intersection (then its quadrant)
PROPERTY_ORDER = (
    "COALESCE(p.address, p.intersection_street_1 || ' & ' || p.intersection_street_2), p.quadrant, p.boro, p.block, p.lot"
)
# Another live sidewalk of the project at the borough, block and lot (the three as placeholders, in that order)
_SAME_LOT = """
                  other.project_id = p.project_id
                  AND other.kind = 'sidewalk'
                  AND other.boro = %s::icid.boro AND other.block = %s AND other.lot = %s
                  AND other.id <> p.id
                  AND other.deleted_at IS NULL"""
# Property p is still at the lot new_lot names (SET reads p as it was): its MapPLUTO frontage still holds
_SAME_TAX_LOT = ("(p.boro = new_lot.boro AND p.block IS NOT DISTINCT FROM new_lot.block"
                 " AND p.lot IS NOT DISTINCT FROM new_lot.lot)")
# Property p is still on the block new_lot names (SET reads p as it was): its DOF tax-map PDF, a map of the block, holds
_SAME_TAX_BLOCK = "(p.boro = new_lot.boro AND p.block IS NOT DISTINCT FROM new_lot.block)"
# The tax-map PDF's columns (Slice Y1.5), all cleared together
_TAX_MAP_PDF_COLUMNS = ("tax_map_pdf_path", "tax_map_pdf_source", "tax_map_pdf_map_id", "tax_map_pdf_effective_date",
                        "tax_map_pdf_fetched_at", "tax_map_pdf_uploaded_by")
# The latest live task order property p is in, as latest: the one issued last (undated ones after the dated), then
# the newest
_LATEST_TASK_ORDER = """LEFT JOIN LATERAL (
            SELECT t.id AS task_order_id, t.task_order_number
            FROM icid.task_order_properties tp
            JOIN icid.task_orders t ON t.id = tp.task_order_id
            WHERE tp.property_id = p.id AND t.deleted_at IS NULL
            ORDER BY t.issued_date DESC NULLS LAST, t.created_at DESC
            LIMIT 1
        ) latest ON true"""
# The columns a create or an edit writes (all of them, on an edit), each with its typed placeholder
_WRITTEN = (
    ("boro", "%s::icid.boro"), ("block", "%s"), ("lot", "%s"), ("address", "%s"), ("property_type", "%s"), ("violation_no", "%s"),
    ("intersection_street_1", "%s"), ("intersection_street_2", "%s"), ("quadrant", "%s::icid.quadrant"),
    ("facing_street", "%s::icid.facing_street"), ("ramp_type", "%s::icid.ramp_type"), ("corner_id", "%s"), ("community_board", "%s"),
    ("notes", "%s"),
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
    # At most one live PIR per property (uq_pirs_property), so the join never repeats a property
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
            latest.task_order_id,
            latest.task_order_number AS task_order_no
        FROM icid.properties p
        LEFT JOIN icid.pirs pir ON pir.property_id = p.id AND pir.deleted_at IS NULL
        """
        + _LATEST_TASK_ORDER
        + """
        WHERE p.project_id = %s AND (%s OR p.deleted_at IS NULL)
          AND (%s::icid.property_kind IS NULL OR p.kind = %s::icid.property_kind)
        ORDER BY """
        + PROPERTY_ORDER
        + ";"
    )
    return run_query(sql, (timezone, project_id, include_deleted, kind, kind))


def get_property(property_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one property, deleted or not, with the latest live task order it is in.
    Takes the property's id.
    Returns the property dict with task_order_id and task_order_no (None when it is in none), or None if no property matches.
    """
    sql = (
        "\n        SELECT"
        + _PROPERTY_COLUMNS
        + ",\n            latest.task_order_id, latest.task_order_number AS task_order_no\n        FROM icid.properties p\n        "
        + _LATEST_TASK_ORDER
        + "\n        WHERE p.id = %s;"
    )
    rows = run_query(sql, (property_id,))
    return rows[0] if rows else None


def get_sidewalk_at(project_id: str, boro: str, block: str, lot: str) -> Optional[dict[str, Any]]:
    """
    Fetch the sidewalk of a project at a borough, block and lot: the one that isn't deleted.
    Takes the project id, the borough, and the block and lot as stored (no leading zeros).
    Returns the property dict, or None if no live sidewalk of the project is there.
    """
    sql = (
        "\n        SELECT"
        + _PROPERTY_COLUMNS
        + """
        FROM icid.properties p
        WHERE p.project_id = %s AND p.kind = 'sidewalk'
          AND p.boro = %s::icid.boro AND p.block = %s AND p.lot = %s
          AND p.deleted_at IS NULL;"""
    )
    rows = run_query(sql, (project_id, boro, block, lot))
    return rows[0] if rows else None


def create_property(project_id: str, created_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a property to a project. Adds nothing when a live sidewalk of the project is already at its borough, block and lot.
    Takes the project id, the uuid of the user adding it, and the values keyed by column: kind, boro, a sidewalk's block, lot, address, property_type and violation_no or a ped ramp's intersection_street_1 and _2, quadrant, facing_street, ramp_type and corner_id, and community_board and notes (one left out is None).
    Returns the new property dict, or None when the borough, block and lot are taken.
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
    Write a property's record whole, as an edit leaves it (its kind is never written). A live property is left alone when another live sidewalk of its project is already at the borough, block and lot it would end up with. A change of borough, block or lot clears its MapPLUTO frontage (Slice Y1), which was the old lot's; a change of borough or block clears its DOF tax-map PDF too (Slice Y1.5), the old block's map (its file stays in Storage).
    Takes the property's id and every written column's value keyed by column (one left out is written as None).
    Returns the updated property dict, or None when no property matches or the change would collide.
    """
    sql = (
        """
        UPDATE icid.properties p
        SET """
        + ",\n            ".join(f"{column} = {placeholder}" for column, placeholder in _WRITTEN)
        + """,
            tax_map_frontage_ft = CASE WHEN """ + _SAME_TAX_LOT + """ THEN p.tax_map_frontage_ft END,
            tax_map_source = CASE WHEN """ + _SAME_TAX_LOT + """ THEN p.tax_map_source END,
            tax_map_fetched_at = CASE WHEN """ + _SAME_TAX_LOT + """ THEN p.tax_map_fetched_at END,
            tax_map_depth_ft = CASE WHEN """ + _SAME_TAX_LOT + """ THEN p.tax_map_depth_ft END,
            """ + "".join(f"{column} = CASE WHEN {_SAME_TAX_BLOCK} THEN p.{column} END,\n            "
                          for column in _TAX_MAP_PDF_COLUMNS) + """updated_at = now()
        FROM (SELECT %s::icid.boro AS boro, %s::text AS block, %s::text AS lot) AS new_lot
        WHERE p.id = %s
          AND (
              p.deleted_at IS NOT NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM icid.properties other
                  WHERE""" + _SAME_LOT + """
              )
          )
        RETURNING"""
        + _PROPERTY_COLUMNS
        + ";"
    )
    lot = (data.get("boro"), data.get("block"), data.get("lot"))
    params = (*_written_values(data), *lot, property_id, *lot)
    try:
        rows = run_query(sql, params)
    except UniqueViolation:
        # Another sidewalk took the lot between the statement's own check and its write
        return None
    return rows[0] if rows else None


def soft_delete_property(property_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a property deleted, with when and by whom; a sidewalk frees its borough, block and lot.
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
    Bring a deleted property back: clear deleted_at and deleted_by. Changes nothing when a live sidewalk of its project is now at its borough, block and lot.
    Takes the property's id.
    Returns the updated property dict, or None if no property matches, it isn't deleted, or its lot is taken.
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
                AND other.kind = 'sidewalk'
                AND other.boro = p.boro AND other.block = p.block AND other.lot = p.lot
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
        # Another sidewalk took the lot between the statement's own check and its write
        return None
    return rows[0] if rows else None


def set_property_tax_map(
    property_id: UUID, boro: str, block: str, lot: str, frontage_ft: Decimal, source: str,
    depth_ft: Optional[Decimal] = None,
) -> Optional[list[dict[str, Any]]]:
    """
    Cache a sidewalk's MapPLUTO frontage (Slice Y1) and depth (pilot sweep #2), fetched now, while it is still at the lot they were fetched for.
    Takes the property's id, the borough, block and lot they were fetched for, the frontage in feet, its source label and the depth in feet (None for none).
    Returns [{id}] when it was written, [] when the property is gone, not a sidewalk or now at another lot, or None on failure.
    """
    sql = """
        UPDATE icid.properties p
        SET tax_map_frontage_ft = %s, tax_map_source = %s, tax_map_fetched_at = now(), tax_map_depth_ft = %s
        WHERE p.id = %s AND p.kind = 'sidewalk' AND p.boro = %s::icid.boro AND p.block = %s AND p.lot = %s
        RETURNING p.id;
    """
    return run_query(sql, (frontage_ft, source, depth_ft, property_id, boro, block, lot))


def set_property_tax_map_depth(
    property_id: UUID, boro: str, block: str, lot: str, depth_ft: Decimal
) -> Optional[list[dict[str, Any]]]:
    """
    Fill in a sidewalk's MapPLUTO depth alone (pilot sweep #2's backfill), beside the frontage it has, while it is still at the lot it was fetched for; the frontage, its source and its title blocks are left as they are.
    Takes the property's id, the borough, block and lot the depth was fetched for, and the depth in feet.
    Returns [{id}] when it was written, [] when the property is gone, not a sidewalk, without a frontage or now at another lot, or None on failure.
    """
    sql = """
        UPDATE icid.properties p
        SET tax_map_depth_ft = %s
        WHERE p.id = %s AND p.kind = 'sidewalk' AND p.tax_map_frontage_ft IS NOT NULL
          AND p.boro = %s::icid.boro AND p.block = %s AND p.lot = %s
        RETURNING p.id;
    """
    return run_query(sql, (depth_ft, property_id, boro, block, lot))


def list_sidewalks_without_tax_map() -> Optional[list[dict[str, Any]]]:
    """
    Fetch every live sidewalk, of every project, that has no MapPLUTO frontage yet (Slice Y1's backfill), with the width its live PIR's page 1 holds now.
    Takes nothing.
    Returns [{id, project_id, address, boro, block, lot, page_1_width_ft, page_1_source}] by project then address (the page's two None without one), or None on failure.
    """
    sql = """
        SELECT p.id, p.project_id, p.address, p.boro::text AS boro, p.block, p.lot,
               a.tax_map_width_ft AS page_1_width_ft, a.tax_map_source AS page_1_source
        FROM icid.properties p
        LEFT JOIN icid.pirs pir ON pir.property_id = p.id AND pir.deleted_at IS NULL
        LEFT JOIN icid.pir_pages pg ON pg.pir_id = pir.id AND pg.page_number = 1 AND pg.deleted_at IS NULL
        LEFT JOIN icid.pir_annotations a ON a.pir_page_id = pg.pir_page_id
        WHERE p.kind = 'sidewalk' AND p.deleted_at IS NULL AND p.tax_map_frontage_ft IS NULL
        ORDER BY p.project_id, p.address;
    """
    return run_query(sql)


def list_sidewalks_without_tax_map_depth() -> Optional[list[dict[str, Any]]]:
    """
    Fetch every live sidewalk, of every project, that has a MapPLUTO frontage but no depth (pilot sweep #2's backfill).
    Takes nothing.
    Returns [{id, project_id, address, boro, block, lot, frontage_ft}] by project then address, or None on failure.
    """
    sql = """
        SELECT p.id, p.project_id, p.address, p.boro::text AS boro, p.block, p.lot, p.tax_map_frontage_ft AS frontage_ft
        FROM icid.properties p
        WHERE p.kind = 'sidewalk' AND p.deleted_at IS NULL
          AND p.tax_map_frontage_ft IS NOT NULL AND p.tax_map_depth_ft IS NULL
        ORDER BY p.project_id, p.address;
    """
    return run_query(sql)


def list_sidewalks_without_tax_map_pdf() -> Optional[list[dict[str, Any]]]:
    """
    Fetch every live sidewalk, of every project, that has no DOF tax-map PDF yet (Slice Y1.5's backfill).
    Takes nothing.
    Returns [{id, project_id, address, boro, block, lot}] by borough, block, project and address, or None on failure.
    """
    sql = """
        SELECT p.id, p.project_id, p.address, p.boro::text AS boro, p.block, p.lot
        FROM icid.properties p
        WHERE p.kind = 'sidewalk' AND p.deleted_at IS NULL AND p.tax_map_pdf_path IS NULL
        ORDER BY p.boro, p.block::int, p.project_id, p.address;
    """
    return run_query(sql)


def refresh_property_tax_map(
    property_id: UUID, boro: str, block: str, lot: str, frontage_ft: Decimal, source: str,
    depth_ft: Optional[Decimal] = None,
) -> Optional[dict[str, Any]]:
    """
    Write a sidewalk's freshly fetched MapPLUTO frontage (Slice Y1) in one statement: onto the property, and onto the title block of every live page of its live PIR whose width wasn't typed by hand (source manual), page 1 getting a title block if it has none.
    Takes the property's id, the borough, block and lot the frontage was fetched for, the frontage in feet, its source label and the depth in feet (None for none; onto the property only, a title block having no depth).
    Returns {tax_map_frontage_ft, tax_map_source, tax_map_fetched_at, tax_map_depth_ft, old_frontage_ft, pages_updated}, or None when the property is gone, not a sidewalk or now at another lot (nothing written).
    """
    sql = """
        WITH prop AS (
            UPDATE icid.properties p
            SET tax_map_frontage_ft = %s, tax_map_source = %s, tax_map_fetched_at = now(), tax_map_depth_ft = %s
            FROM icid.properties old
            WHERE p.id = %s AND old.id = p.id
              AND p.kind = 'sidewalk' AND p.boro = %s::icid.boro AND p.block = %s AND p.lot = %s
            RETURNING p.id, p.tax_map_frontage_ft, p.tax_map_source, p.tax_map_fetched_at, p.tax_map_depth_ft,
                      old.tax_map_frontage_ft AS old_frontage_ft
        ),
        pages AS (
            SELECT pg.pir_page_id, pg.page_number
            FROM prop
            JOIN icid.pirs pir ON pir.property_id = prop.id AND pir.deleted_at IS NULL
            JOIN icid.pir_pages pg ON pg.pir_id = pir.id AND pg.deleted_at IS NULL
        ),
        written AS (
            INSERT INTO icid.pir_annotations AS a (pir_page_id, tax_map_width_ft, tax_map_source, tax_map_fetched_at)
            SELECT pages.pir_page_id, prop.tax_map_frontage_ft, prop.tax_map_source, prop.tax_map_fetched_at
            FROM pages CROSS JOIN prop
            WHERE pages.page_number = 1
               OR EXISTS (SELECT 1 FROM icid.pir_annotations o WHERE o.pir_page_id = pages.pir_page_id)
            ON CONFLICT (pir_page_id) DO UPDATE
                SET tax_map_width_ft = EXCLUDED.tax_map_width_ft, tax_map_source = EXCLUDED.tax_map_source,
                    tax_map_fetched_at = EXCLUDED.tax_map_fetched_at, updated_at = now()
                WHERE a.tax_map_source IS DISTINCT FROM 'manual'
            RETURNING a.pir_page_id
        )
        SELECT prop.tax_map_frontage_ft, prop.tax_map_source, prop.tax_map_fetched_at, prop.tax_map_depth_ft,
               prop.old_frontage_ft,
               (SELECT count(*) FROM written) AS pages_updated
        FROM prop;
    """
    rows = run_query(sql, (frontage_ft, source, depth_ft, property_id, boro, block, lot))
    return rows[0] if rows else None


def set_property_tax_map_pdf(property_id: UUID, boro: str, block: str, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Record a sidewalk's DOF tax-map PDF (Slice Y1.5), replacing any it had, while it is still on the block the map is of.
    Takes the property's id, the borough and block the map is of, and the PDF's columns: tax_map_pdf_path, _source ('dof_map_library' or 'upload'), _map_id and _effective_date (a fetch's) and _uploaded_by (an upload's); its fetched_at is now.
    Returns the property dict with old_tax_map_pdf_path (the file it replaced, or None), or None when the property is gone, isn't a sidewalk or is now on another block.
    """
    sql = (
        """
        WITH updated AS (
            UPDATE icid.properties p
            SET tax_map_pdf_path = %s, tax_map_pdf_source = %s, tax_map_pdf_map_id = %s,
                tax_map_pdf_effective_date = %s::date, tax_map_pdf_fetched_at = now(), tax_map_pdf_uploaded_by = %s::uuid
            FROM icid.properties old
            WHERE p.id = %s AND old.id = p.id AND p.kind = 'sidewalk' AND p.boro = %s::icid.boro AND p.block = %s
            RETURNING p.*, old.tax_map_pdf_path AS old_tax_map_pdf_path
        )
        SELECT"""
        + _PROPERTY_COLUMNS
        + """, p.old_tax_map_pdf_path
        FROM updated p;"""
    )
    params = (fields["tax_map_pdf_path"], fields["tax_map_pdf_source"], fields.get("tax_map_pdf_map_id"),
              fields.get("tax_map_pdf_effective_date"), fields.get("tax_map_pdf_uploaded_by"), property_id, boro, block)
    rows = run_query(sql, params)
    return rows[0] if rows else None
