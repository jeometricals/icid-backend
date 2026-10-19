"""
SQL for match lines (R1h.1a): the lettered lines that join a PIR's sheets, each drawn on one page as two endpoints in
its native pixels. A label is live on at most one row per page and on at most two pages of a PIR; the writes here
check both in their own statement, so they can't be raced past.
"""

from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries._shared import PARENT_COLUMNS, page_parents

# A match line as every function here returns it: its own columns, its page's number, and its page's PIR, property,
# project and deleted states (what the routes check before a write). Read from a row source named ml.
_MATCH_LINE_COLUMNS = """
            ml.match_line_id, ml.pir_page_id, pg.page_number, ml.label, ml.x1, ml.y1, ml.x2, ml.y2,
            ml.created_at, ml.updated_at, ml.created_by, ml.deleted_at, ml.deleted_by,""" + PARENT_COLUMNS
_FROM = page_parents("ml")
# The label may go on this page: no other live line of the page has it, and it is live on no more than one other page
# of the page's PIR. Takes the label twice; reads the page as page and the line being written (None for a new one) as
# its match_line_id parameter, twice.
_LABEL_FREE = """NOT EXISTS (
                SELECT 1 FROM icid.pir_match_lines same
                WHERE same.pir_page_id = page.pir_page_id AND same.label = %s AND same.deleted_at IS NULL
                  AND same.match_line_id IS DISTINCT FROM %s::uuid
            )
            AND (
                SELECT count(DISTINCT other.pir_page_id)
                FROM icid.pir_match_lines other
                JOIN icid.pir_pages other_page ON other_page.pir_page_id = other.pir_page_id
                WHERE other_page.pir_id = page.pir_id AND other.label = %s AND other.deleted_at IS NULL
                  AND other.pir_page_id <> page.pir_page_id AND other.match_line_id IS DISTINCT FROM %s::uuid
            ) < 2"""


def _select(where: str) -> str:
    """
    A read of match lines from the table.
    Takes the WHERE clause (and ORDER BY) after the joins.
    Returns the SQL.
    """
    return "\n        SELECT" + _MATCH_LINE_COLUMNS + "\n        FROM icid.pir_match_lines ml" + _FROM + "\n        " + where


def _written(cte: str) -> str:
    """
    A statement that writes match lines in a CTE named ml and returns them as every function here does.
    Takes the CTE's body.
    Returns the SQL.
    """
    return ("\n        WITH ml AS (" + cte + "\n        )\n        SELECT" + _MATCH_LINE_COLUMNS
            + "\n        FROM ml" + _FROM + ";")


def list_match_lines_for_page(pir_page_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the live match lines drawn on one page.
    Takes the page's id.
    Returns the match line dicts by label, or None on failure.
    """
    return run_query(_select("WHERE ml.pir_page_id = %s AND ml.deleted_at IS NULL\n        ORDER BY ml.label;"),
                     (pir_page_id,))


def list_match_lines_for_pir(pir_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a PIR's live match lines on its live pages.
    Takes the PIR's id.
    Returns the match line dicts by page number, then label, or None on failure.
    """
    return run_query(
        _select("WHERE pg.pir_id = %s AND ml.deleted_at IS NULL AND pg.deleted_at IS NULL\n"
                "        ORDER BY pg.page_number, ml.label;"),
        (pir_id,),
    )


def get_match_line(match_line_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one match line, deleted or not.
    Takes the line's id.
    Returns the match line dict, or None if none matches.
    """
    rows = run_query(_select("WHERE ml.match_line_id = %s;"), (match_line_id,))
    return rows[0] if rows else None


def create_match_line(pir_page_id: UUID, created_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a match line to a live page. Adds nothing when the page doesn't exist or was replaced, the page already has the label, or the label is already on two other pages of its PIR.
    Takes the page's id, the uuid of the user adding it and the values (label, x1, y1, x2, y2).
    Returns the new match line dict, or None when nothing was added.
    """
    sql = _written(
        """
            INSERT INTO icid.pir_match_lines (pir_page_id, label, x1, y1, x2, y2, created_by)
            SELECT page.pir_page_id, %s, %s, %s, %s, %s, %s
            FROM icid.pir_pages page
            WHERE page.pir_page_id = %s AND page.deleted_at IS NULL
              AND """ + _LABEL_FREE + """
            RETURNING *"""
    )
    label = data["label"]
    params = (label, data["x1"], data["y1"], data["x2"], data["y2"], created_by_uuid, pir_page_id,
              label, None, label, None)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_match_line(match_line_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Rewrite a match line's label and endpoints with its merged record. Changes nothing when its new label is taken on its page or already on two other pages of its PIR.
    Takes the line's id and the values (label, x1, y1, x2, y2).
    Returns the updated match line dict, or None when no line matches or the label isn't free.
    """
    sql = _written(
        """
            UPDATE icid.pir_match_lines line
            SET label = %s, x1 = %s, y1 = %s, x2 = %s, y2 = %s, updated_at = now()
            FROM icid.pir_pages page
            WHERE line.match_line_id = %s AND page.pir_page_id = line.pir_page_id
              AND """ + _LABEL_FREE + """
            RETURNING line.*"""
    )
    label = data["label"]
    params = (label, data["x1"], data["y1"], data["x2"], data["y2"], match_line_id,
              label, match_line_id, label, match_line_id)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def soft_delete_match_line(match_line_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a match line deleted, with when and by whom.
    Takes the line's id and the uuid of the user deleting it.
    Returns the updated match line dict, or None if none matches or it is already deleted.
    """
    sql = _written(
        """
            UPDATE icid.pir_match_lines
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE match_line_id = %s AND deleted_at IS NULL
            RETURNING *"""
    )
    rows = run_query(sql, (deleted_by_uuid, match_line_id))
    return rows[0] if rows else None
