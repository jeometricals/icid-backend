from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries._shared import PARENT_COLUMNS, PAY_ITEM_OF_PROJECT, page_parents, placeholders, write_values
from api.queries.flag_measurements import REASON_COVERS, remesh_ctes, snapshot_intent

# A feature as every function here returns it: its own columns, what counts as its quantity, and its page's PIR,
# property, project and deleted states (what the routes check before a write). Read from a row source named ft.
_FEATURE_COLUMNS = """
            ft.feature_id, ft.pir_page_id, ft.flag_id, ft.feature_type, ft.area_impact, ft.sketch_x, ft.sketch_y,
            ft.rotation, ft.shape, ft.dimensions, ft.label, ft.pay_item_id, ft.computed_quantity, ft.reported_quantity,
            COALESCE(ft.reported_quantity, ft.computed_quantity) AS quantity,
            ft.created_at, ft.updated_at, ft.created_by, ft.deleted_at, ft.deleted_by,""" + PARENT_COLUMNS
_FROM = page_parents("ft")
# The columns a write sets from the request, in the order their values are passed, each with its type
_WRITABLE_TYPES = {
    "feature_type": "text", "area_impact": "text", "sketch_x": "real", "sketch_y": "real", "rotation": "real",
    "shape": "text", "dimensions": "jsonb", "label": "text", "pay_item_id": "uuid", "flag_id": "uuid",
    "reported_quantity": "numeric",
}
# What the PIR import writes, as this module does
WRITABLE_TYPES = _WRITABLE_TYPES
_WRITABLE = tuple(_WRITABLE_TYPES)
_PLACEHOLDERS = placeholders(_WRITABLE_TYPES)
# The flag, if any, must be a live flag on the same page: page is the feature's page. Takes the flag's id twice.
_FLAG_ON_PAGE = """(%s::uuid IS NULL OR EXISTS (
              SELECT 1 FROM icid.flags fl
              WHERE fl.id = %s::uuid AND fl.pir_page_id = page.pir_page_id AND fl.deleted_at IS NULL
          ))"""


def _select(where: str) -> str:
    """
    A read of features from the table.
    Takes the WHERE clause (and ORDER BY) after the joins.
    Returns the SQL.
    """
    return "\n        SELECT" + _FEATURE_COLUMNS + "\n        FROM icid.features ft" + _FROM + "\n        " + where


# The features a statement writes (ft) as the remesh reads covers, and the measurements of the flags they were and are
# subtracted from resynced with them (Slice Y3b-1: a bundle's deductions are its members' covers)
_TOUCHED_FLAGS = """SELECT flag_id FROM ft WHERE flag_id IS NOT NULL
                    UNION SELECT was.flag_id FROM icid.features was
                    WHERE was.feature_id IN (SELECT feature_id FROM ft) AND was.flag_id IS NOT NULL"""
_RESYNC = (
    """,
        intent AS (
            """
    + snapshot_intent("covers", REASON_COVERS, _TOUCHED_FLAGS)
    + """
        ),
        rm_written AS (
            SELECT flag_id, 'subtract_feature'::text AS kind, feature_id AS source_id,
                   COALESCE(reported_quantity, computed_quantity) AS area_sf,
                   deleted_at IS NULL AND area_impact = 'subtract' AND flag_id IS NOT NULL AS live
            FROM ft
        )"""
    + remesh_ctes(written="rm_written")
)


def _written(cte: str) -> str:
    """
    A statement that writes features in a CTE named ft and returns them as every function here does; the measurements
    of the flags they touch are resynced in it (_RESYNC).
    Takes the CTE's body.
    Returns the SQL.
    """
    return ("\n        WITH ft AS (" + cte + "\n        )" + _RESYNC + "\n        SELECT" + _FEATURE_COLUMNS
            + "\n        FROM ft" + _FROM + ";")


def _checks(data: dict[str, Any]) -> tuple[Any, ...]:
    """
    The parameters of the pay-item and flag checks, in their order.
    Takes the values keyed by column.
    Returns the pay item's id twice, then the flag's id twice.
    """
    return (data.get("pay_item_id"), data.get("pay_item_id"), data.get("flag_id"), data.get("flag_id"))


def list_features_for_page(pir_page_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the live features drawn on one page.
    Takes the page's id.
    Returns the feature dicts in the order they were drawn, or None on failure.
    """
    return run_query(_select("WHERE ft.pir_page_id = %s AND ft.deleted_at IS NULL\n        ORDER BY ft.created_at;"),
                     (pir_page_id,))


def list_features_for_pir(pir_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a PIR's live features on its live pages.
    Takes the PIR's id.
    Returns the feature dicts by page number, then in the order they were drawn, or None on failure.
    """
    return run_query(
        _select("WHERE pg.pir_id = %s AND ft.deleted_at IS NULL AND pg.deleted_at IS NULL\n"
                "        ORDER BY pg.page_number, ft.created_at;"),
        (pir_id,),
    )


def get_feature(feature_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one feature, deleted or not.
    Takes the feature's id.
    Returns the feature dict, or None if no feature matches.
    """
    rows = run_query(_select("WHERE ft.feature_id = %s;"), (feature_id,))
    return rows[0] if rows else None


def create_feature(
    pir_page_id: UUID, created_by_uuid: UUID, data: dict[str, Any], computed_area: Optional[Decimal]
) -> Optional[dict[str, Any]]:
    """
    Add a feature to a live page. Adds nothing when the page doesn't exist or was replaced, the pay item isn't one of its project's, or the flag isn't a live flag on the page.
    Takes the page's id, the uuid of the user adding it, the values keyed by column (every writable column) and the area of its shape (None without one).
    Returns the new feature dict, or None when nothing was added.
    """
    sql = _written(
        """
            INSERT INTO icid.features (""" + ", ".join(_WRITABLE) + """, computed_quantity, pir_page_id, created_by)
            SELECT """ + ", ".join(_PLACEHOLDERS) + """, %s::numeric, page.pir_page_id, %s::uuid
            FROM icid.pir_pages page
            JOIN icid.pirs pir ON pir.id = page.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE page.pir_page_id = %s AND page.deleted_at IS NULL
              AND """ + PAY_ITEM_OF_PROJECT + """
              AND """ + _FLAG_ON_PAGE + """
            RETURNING *"""
    )
    params = (*write_values(_WRITABLE_TYPES, data), computed_area, created_by_uuid, pir_page_id, *_checks(data))
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_feature(feature_id: UUID, data: dict[str, Any], computed_area: Optional[Decimal]) -> Optional[dict[str, Any]]:
    """
    Rewrite a feature's writable columns with its merged record, and its computed area. Changes nothing when the pay item isn't one of its project's or the flag isn't a live flag on its page.
    Takes the feature's id, the values keyed by column (every writable column) and the area of its shape (None without one).
    Returns the updated feature dict, or None when no feature matches or a check fails.
    """
    assignments = ",\n                ".join(f"{col} = {ph}" for col, ph in zip(_WRITABLE, _PLACEHOLDERS))
    sql = _written(
        """
            UPDATE icid.features feat
            SET """ + assignments + """,
                computed_quantity = %s::numeric,
                updated_at = now()
            FROM icid.pir_pages page
            JOIN icid.pirs pir ON pir.id = page.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE feat.feature_id = %s AND page.pir_page_id = feat.pir_page_id
              AND """ + PAY_ITEM_OF_PROJECT + """
              AND """ + _FLAG_ON_PAGE + """
            RETURNING feat.*"""
    )
    params = (*write_values(_WRITABLE_TYPES, data), computed_area, feature_id, *_checks(data))
    rows = run_query(sql, params)
    return rows[0] if rows else None


def soft_delete_feature(feature_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a feature deleted, with when and by whom.
    Takes the feature's id and the uuid of the user deleting it.
    Returns the updated feature dict, or None if no feature matches or it is already deleted.
    """
    sql = _written(
        """
            UPDATE icid.features
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE feature_id = %s AND deleted_at IS NULL
            RETURNING *"""
    )
    rows = run_query(sql, (deleted_by_uuid, feature_id))
    return rows[0] if rows else None
