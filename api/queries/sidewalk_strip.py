"""
SQL for a PIR page's sidewalk strip (Slice Y1.5), kept on its title block (icid.pir_annotations), and the strip's
auto-flip of the flags wholly outside it, with their flag_edits rows, in the same statement.
"""

from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query
from api.queries.flag_edits import flag_edit_rows_cte
from api.queries.flag_measurements import REASON_FLIP, remesh_ctes

# Why the auto-flip changed a flag, as its flag_edits rows say
FLIP_REASON = "flag entirely outside the sidewalk strip"
# The strip's columns as the routes read them, from a row source named a
_STRIP_COLUMNS = """a.pir_page_id, a.sidewalk_strip_points, a.sidewalk_strip_source, a.sidewalk_strip_set_at,
               a.sidewalk_strip_set_by"""


def set_sidewalk_strip(pir_page_id: UUID, points: list[dict[str, float]], set_by: UUID, flip_ids: list[UUID],
                       targets: Optional[dict[UUID, UUID]] = None,
                       pay_items: Optional[dict[UUID, UUID]] = None) -> Optional[dict[str, Any]]:
    """
    Draw a live page's sidewalk strip, in one statement: its title block takes the points (source manual, set now, by the user; a page without a title block gets one), and of the flags given, those still live, on the page, assessable and unclaimed are marked non-assessable (outside_property_line), one without a pay item taking the one given for it (its thickness's, pilot sweep #2), with their flag_edits rows (the system's: no editor), each one's measurement following it (remesh_ctes, Slice Y3b-1: into the bundle given for it, if still open to it, or out of a bundle it was in). Writes nothing on a page that doesn't exist or was replaced.
    Takes the page's id, the points ([{x, y}, ...]), the drawing user's uuid, the ids of the flags found wholly outside the strip, the bundle the remesh picked for each to join and the pay item each takes when it has none (both by flag id; none for one without).
    Returns {pir_page_id, sidewalk_strip_points, _source, _set_at, _set_by, flipped: [{id, display_order}]}, or None when nothing was written.
    """
    sql = f"""
        WITH strip AS (
            INSERT INTO icid.pir_annotations AS a (pir_page_id, sidewalk_strip_points, sidewalk_strip_source,
                                                   sidewalk_strip_set_at, sidewalk_strip_set_by)
            SELECT pg.pir_page_id, %s::jsonb, 'manual', now(), %s::uuid
            FROM icid.pir_pages pg
            WHERE pg.pir_page_id = %s AND pg.deleted_at IS NULL
            ON CONFLICT (pir_page_id) DO UPDATE
                SET sidewalk_strip_points = EXCLUDED.sidewalk_strip_points,
                    sidewalk_strip_source = EXCLUDED.sidewalk_strip_source,
                    sidewalk_strip_set_at = EXCLUDED.sidewalk_strip_set_at,
                    sidewalk_strip_set_by = EXCLUDED.sidewalk_strip_set_by,
                    updated_at = now()
            RETURNING {_STRIP_COLUMNS}
        ),
        intent AS (
            SELECT f.id AS flag_id, 'flip'::text AS event, t.target_id, f.surveyed_thickness AS new_thickness,
                   false AS new_assessable, COALESCE(f.pay_item_id, t.pay_item_id) AS new_pay_item,
                   f.computed_quantity AS new_computed,
                   f.shape AS new_shape, f.dimensions AS new_dims, '{REASON_FLIP}'::text AS reason
            FROM strip
            JOIN icid.flags f ON f.pir_page_id = strip.pir_page_id
            LEFT JOIN unnest(%s::uuid[], %s::uuid[], %s::uuid[]) AS t(flag_id, target_id, pay_item_id) ON t.flag_id = f.id
            WHERE f.id = ANY(%s::uuid[]) AND f.deleted_at IS NULL AND f.claimed_by_idr_id IS NULL
              AND f.surveyed_is_assessable
        ){remesh_ctes("(SELECT sidewalk_strip_set_by FROM strip)")},
        flipped AS (
            UPDATE icid.flags f
            SET surveyed_is_assessable = false, surveyed_non_assessable_reason = 'outside_property_line',
                pay_item_id = i.new_pay_item,
                measurement_id = rf.measurement_id, area_share_fraction = rf.share, updated_at = now()
            FROM strip, icid.flags old, rm_flags rf, intent i
            WHERE f.id = ANY(%s::uuid[]) AND old.id = f.id AND f.pir_page_id = strip.pir_page_id AND rf.flag_id = f.id
              AND i.flag_id = f.id
              AND f.deleted_at IS NULL AND f.claimed_by_idr_id IS NULL AND f.surveyed_is_assessable
            RETURNING f.id, f.display_order, f.surveyed_is_assessable, f.surveyed_non_assessable_reason,
                      old.surveyed_is_assessable AS old_assessable, old.surveyed_non_assessable_reason AS old_reason
        ),
        edits AS (
            {flag_edit_rows_cte("flipped")}
        )
        SELECT strip.*,
               COALESCE((SELECT jsonb_agg(jsonb_build_object('id', id, 'display_order', display_order)
                                          ORDER BY display_order) FROM flipped), '[]'::jsonb) AS flipped
        FROM strip
        CROSS JOIN (SELECT count(*) FROM edits) AS edits_written;
    """
    picked, defaults = targets or {}, pay_items or {}
    flip_ids = list(flip_ids)
    rows = run_query(sql, (Jsonb(points), set_by, pir_page_id, flip_ids, [picked.get(flag_id) for flag_id in flip_ids],
                           [defaults.get(flag_id) for flag_id in flip_ids], flip_ids, flip_ids, FLIP_REASON, None))
    return rows[0] if rows else None


def clear_sidewalk_strip(pir_page_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Take a live page's sidewalk strip away: its points, source, time and setter are cleared. Flags it marked non-assessable stay so.
    Takes the page's id.
    Returns [the title block's strip columns, now NULL] when it had a title block, [] when it had none or the page was replaced, or None on failure.
    """
    sql = f"""
        UPDATE icid.pir_annotations a
        SET sidewalk_strip_points = NULL, sidewalk_strip_source = NULL, sidewalk_strip_set_at = NULL,
            sidewalk_strip_set_by = NULL, updated_at = now()
        FROM icid.pir_pages pg
        WHERE a.pir_page_id = %s AND pg.pir_page_id = a.pir_page_id AND pg.deleted_at IS NULL
        RETURNING {_STRIP_COLUMNS};
    """
    return run_query(sql, (pir_page_id,))
