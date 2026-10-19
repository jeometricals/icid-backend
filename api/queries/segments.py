from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries._shared import PARENT_COLUMNS, PAY_ITEM_OF_PROJECT, page_parents, placeholders, write_values


def segment_length_sql(points: str, scale: str) -> str:
    """
    The SQL for a segment's length in feet: the sum of the straight runs between its consecutive points, over the page's scale, to 2 decimal places (halves up); NULL without a scale.
    Takes the SQL expressions for the points (a JSONB array of {x, y} in page pixels) and the scale (pixels per foot).
    Returns the SQL expression.
    """
    return f"""(
            SELECT round(sum(sqrt(power((b.pt->>'x')::numeric - (a.pt->>'x')::numeric, 2)
                                  + power((b.pt->>'y')::numeric - (a.pt->>'y')::numeric, 2))) / {scale}, 2)
            FROM jsonb_array_elements({points}) WITH ORDINALITY AS a(pt, n)
            JOIN jsonb_array_elements({points}) WITH ORDINALITY AS b(pt, n) ON b.n = a.n + 1
        )"""


# A segment as every function here returns it: its own columns (its claim among them), and its page's PIR, property, project and deleted
# states (what the routes check before a write). Read from a row source named s.
_SEGMENT_COLUMNS = """
            s.segment_id, s.pir_page_id, s.segment_type, s.points, s.length_ft, s.pay_item_id, s.note,
            s.claimed_by_idr_id, s.constructed_on, s.created_at, s.updated_at, s.created_by, s.deleted_at, s.deleted_by,""" + PARENT_COLUMNS
_FROM = page_parents("s")
# The columns a write sets from the request, in the order their values are passed, each with its type
_WRITABLE_TYPES = {"segment_type": "text", "points": "jsonb", "pay_item_id": "uuid", "note": "text"}
# What the PIR import writes, as this module does
WRITABLE_TYPES = _WRITABLE_TYPES
_WRITABLE = tuple(_WRITABLE_TYPES)
_PLACEHOLDERS = placeholders(_WRITABLE_TYPES)


def _select(where: str) -> str:
    """
    A read of segments from the table.
    Takes the WHERE clause (and ORDER BY) after the joins.
    Returns the SQL.
    """
    return "\n        SELECT" + _SEGMENT_COLUMNS + "\n        FROM icid.segments s" + _FROM + "\n        " + where


def _written(cte: str) -> str:
    """
    A statement that writes segments in a CTE named s and returns them as every function here does.
    Takes the CTE's body.
    Returns the SQL.
    """
    return "\n        WITH s AS (" + cte + "\n        )\n        SELECT" + _SEGMENT_COLUMNS + "\n        FROM s" + _FROM + ";"


def list_segments_for_page(pir_page_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the live segments drawn on one page.
    Takes the page's id.
    Returns the segment dicts in the order they were drawn, or None on failure.
    """
    return run_query(_select("WHERE s.pir_page_id = %s AND s.deleted_at IS NULL\n        ORDER BY s.created_at;"),
                     (pir_page_id,))


def list_segments_for_pir(pir_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a PIR's live segments on its live pages.
    Takes the PIR's id.
    Returns the segment dicts by page number, then in the order they were drawn, or None on failure.
    """
    return run_query(
        _select("WHERE pg.pir_id = %s AND s.deleted_at IS NULL AND pg.deleted_at IS NULL\n"
                "        ORDER BY pg.page_number, s.created_at;"),
        (pir_id,),
    )


def get_segment(segment_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one segment, deleted or not.
    Takes the segment's id.
    Returns the segment dict, or None if no segment matches.
    """
    rows = run_query(_select("WHERE s.segment_id = %s;"), (segment_id,))
    return rows[0] if rows else None


def create_segment(pir_page_id: UUID, created_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a segment to a live page, its length worked out from its points and the page's scale. Adds nothing when the page doesn't exist or was replaced, or the pay item isn't one of its project's.
    Takes the page's id, the uuid of the user adding it and the values keyed by column (every writable column).
    Returns the new segment dict, or None when nothing was added.
    """
    sql = _written(
        """
            INSERT INTO icid.segments (""" + ", ".join(_WRITABLE) + """, length_ft, pir_page_id, created_by)
            SELECT x.*, """ + segment_length_sql("x.points", "page.scale_px_per_ft") + """, page.pir_page_id, %s::uuid
            FROM (SELECT """ + ", ".join(f"{ph} AS {col}" for col, ph in zip(_WRITABLE, _PLACEHOLDERS)) + """) x
            JOIN icid.pir_pages page ON page.pir_page_id = %s AND page.deleted_at IS NULL
            JOIN icid.pirs pir ON pir.id = page.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE """ + PAY_ITEM_OF_PROJECT + """
            RETURNING *"""
    )
    params = (created_by_uuid, *write_values(_WRITABLE_TYPES, data), pir_page_id,
              data.get("pay_item_id"), data.get("pay_item_id"))
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_segment(segment_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Rewrite a segment's writable columns with its merged record, and its length from the new points and its page's scale. Changes nothing when the pay item isn't one of its project's.
    Takes the segment's id and the values keyed by column (every writable column).
    Returns the updated segment dict, or None when no segment matches or the pay item is another project's.
    """
    assignments = ",\n                ".join(f"{col} = {ph}" for col, ph in zip(_WRITABLE, _PLACEHOLDERS))
    sql = _written(
        """
            UPDATE icid.segments seg
            SET """ + assignments + """,
                length_ft = """ + segment_length_sql("%s::jsonb", "page.scale_px_per_ft") + """,
                updated_at = now()
            FROM icid.pir_pages page
            JOIN icid.pirs pir ON pir.id = page.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE seg.segment_id = %s AND page.pir_page_id = seg.pir_page_id
              AND """ + PAY_ITEM_OF_PROJECT + """
            RETURNING seg.*"""
    )
    values = write_values(_WRITABLE_TYPES, data)
    points = values[_WRITABLE.index("points")]
    params = (*values, points, points, segment_id, data.get("pay_item_id"), data.get("pay_item_id"))
    rows = run_query(sql, params)
    return rows[0] if rows else None


def soft_delete_segment(segment_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a segment deleted, with when and by whom.
    Takes the segment's id and the uuid of the user deleting it.
    Returns the updated segment dict, or None if no segment matches or it is already deleted.
    """
    sql = _written(
        """
            UPDATE icid.segments
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE segment_id = %s AND deleted_at IS NULL
            RETURNING *"""
    )
    rows = run_query(sql, (deleted_by_uuid, segment_id))
    return rows[0] if rows else None
