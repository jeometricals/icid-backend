"""
SQL for the homeowner's assessment clipped at the property line (Slice Y2), icid.assessment_clip_audits: per flag and
IDR, the part of a claimed flag outside its page's sidewalk strip. An IDR's set is written in the statements that claim
its flags (approval, un-delete), removed in those that release them (unlock, delete), and rewritten per property by an
admin's re-clip. Only the homeowner's reports read it; contractor billing never does.
"""

from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query
from api.queries.flags import FLAG_QUANTITY, QUANTITY_LATERALS

CLIP_REASON = "outside_property_line"

# A flag as the clip is worked out from it: its shape and place, whether surveyed assessable, its quantity (the
# override, or else its net area), its page's scale and that page's sidewalk strip. Read from a row source named f.
_CLIP_INPUT = f"""
        SELECT f.id, f.claimed_by_idr_id, f.shape, f.dimensions, f.sketch_x, f.sketch_y, f.rotation,
               f.surveyed_is_assessable, {FLAG_QUANTITY} AS quantity,
               pg.scale_px_per_ft, a.sidewalk_strip_points
        FROM icid.flags f
        JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
        LEFT JOIN icid.pir_annotations a ON a.pir_page_id = pg.pir_page_id{QUANTITY_LATERALS}"""


def list_clip_inputs(flag_ids: list[UUID]) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the flags an approval or an un-delete is about to claim, as their clips are worked out.
    Takes the flags' uuids.
    Returns their rows (id, claimed_by_idr_id, shape, dimensions, sketch_x, sketch_y, rotation, surveyed_is_assessable, quantity, scale_px_per_ft, sidewalk_strip_points); [] for none; None on failure.
    """
    if not flag_ids:
        return []
    return run_query(_CLIP_INPUT + "\n        WHERE f.id = ANY(%s::uuid[]);", (list(flag_ids),))


def list_property_clip_inputs(property_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every flag of a property claimed by an approved IDR that isn't deleted, as a re-clip works their clips out.
    Takes the property's uuid.
    Returns their rows, as list_clip_inputs, claimed_by_idr_id the IDR; [] for none; None on failure.
    """
    sql = (_CLIP_INPUT + """
        JOIN icid.pirs pir ON pir.id = f.pir_id
        JOIN icid.idrs i ON i.idr_id = f.claimed_by_idr_id AND i.status = 'approved' AND i.deleted_at IS NULL
        WHERE pir.property_id = %s;""")
    return run_query(sql, (property_id,))


def clips_json(clips: list[dict[str, Any]]) -> Jsonb:
    """
    Put clips in the form the statements read them in: a JSON array of {flag_id, pre_clip_sf, clipped_sf} (and idr_id
    for a re-clip), amounts as text so they stay exact.
    Takes the clips (property_line.flag_clip's, with flag_id and maybe idr_id).
    Returns the JSON parameter.
    """
    return Jsonb([{key: str(value) for key, value in clip.items()} for clip in clips])


def replace_clips_ctes(idrs: str) -> str:
    """
    Write the CTEs that give the IDR a statement approves or brings back its clips (Slice Y2): those of the clips given
    whose flag the statement claimed are written (or rewritten), and any other clip the IDR had is removed. Reads the
    statement's claimed_flags CTE. Takes one parameter: the clips as JSON (clips_json).
    Takes the name of a CTE defined earlier in the statement holding the IDR (idr_id).
    Returns the CTEs (new_clips, written_clips, stale_clips), to append to the statement's WITH list.
    """
    return f"""
        new_clips AS (
            SELECT x.flag_id, i.idr_id, x.pre_clip_sf, x.clipped_sf
            FROM jsonb_to_recordset(%s::jsonb) AS x(flag_id uuid, pre_clip_sf numeric, clipped_sf numeric)
            JOIN claimed_flags c ON c.id = x.flag_id
            CROSS JOIN {idrs} i
        ),
        written_clips AS (
            INSERT INTO icid.assessment_clip_audits (flag_id, idr_id, pre_clip_sf, clipped_sf, clip_reason)
            SELECT flag_id, idr_id, pre_clip_sf, clipped_sf, '{CLIP_REASON}' FROM new_clips
            ON CONFLICT (flag_id, idr_id) DO UPDATE
                SET pre_clip_sf = EXCLUDED.pre_clip_sf, clipped_sf = EXCLUDED.clipped_sf,
                    clip_reason = EXCLUDED.clip_reason, at = now()
            RETURNING flag_id
        ),
        stale_clips AS (
            DELETE FROM icid.assessment_clip_audits c
            USING {idrs} i
            WHERE c.idr_id = i.idr_id AND c.flag_id NOT IN (SELECT flag_id FROM new_clips)
            RETURNING c.flag_id
        )"""


def delete_clips_cte(idrs: str) -> str:
    """
    Write the CTE that removes the clips of the IDR a statement unlocks or deletes (Slice Y2): it no longer claims the
    flags, so they assess nothing. No parameters.
    Takes the name of a CTE defined earlier in the statement holding the IDR (idr_id).
    Returns the CTE (deleted_clips), to append to the statement's WITH list.
    """
    return f"""
        deleted_clips AS (
            DELETE FROM icid.assessment_clip_audits c
            USING {idrs} i
            WHERE c.idr_id = i.idr_id
            RETURNING c.flag_id
        )"""


def replace_property_clips(property_id: UUID, clips: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    Rewrite the clips of every flag of a property claimed by an approved IDR that isn't deleted, in one statement (an admin's re-clip, Slice Y2): the clips given are written (or rewritten) where the flag is still claimed by that IDR, and any other clip of those flags is removed. The IDRs' clips on other properties are left alone.
    Takes the property's uuid and the clips ({flag_id, idr_id, pre_clip_sf, clipped_sf}).
    Returns {idrs_reclipped, rows_before, rows_after}, or None on failure.
    """
    sql = f"""
        WITH claimed AS (
            SELECT f.id AS flag_id, f.claimed_by_idr_id AS idr_id
            FROM icid.flags f
            JOIN icid.pirs pir ON pir.id = f.pir_id
            JOIN icid.idrs i ON i.idr_id = f.claimed_by_idr_id AND i.status = 'approved' AND i.deleted_at IS NULL
            WHERE pir.property_id = %s
        ),
        before AS (
            SELECT count(*) AS n FROM icid.assessment_clip_audits c JOIN claimed USING (flag_id, idr_id)
        ),
        new_clips AS (
            SELECT x.flag_id, x.idr_id, x.pre_clip_sf, x.clipped_sf
            FROM jsonb_to_recordset(%s::jsonb) AS x(flag_id uuid, idr_id uuid, pre_clip_sf numeric, clipped_sf numeric)
            JOIN claimed USING (flag_id, idr_id)
        ),
        written AS (
            INSERT INTO icid.assessment_clip_audits (flag_id, idr_id, pre_clip_sf, clipped_sf, clip_reason)
            SELECT flag_id, idr_id, pre_clip_sf, clipped_sf, '{CLIP_REASON}' FROM new_clips
            ON CONFLICT (flag_id, idr_id) DO UPDATE
                SET pre_clip_sf = EXCLUDED.pre_clip_sf, clipped_sf = EXCLUDED.clipped_sf,
                    clip_reason = EXCLUDED.clip_reason, at = now()
            RETURNING flag_id
        ),
        stale AS (
            DELETE FROM icid.assessment_clip_audits c
            USING claimed
            WHERE c.flag_id = claimed.flag_id AND c.idr_id = claimed.idr_id
              AND (c.flag_id, c.idr_id) NOT IN (SELECT flag_id, idr_id FROM new_clips)
            RETURNING c.flag_id
        )
        SELECT (SELECT count(DISTINCT idr_id) FROM claimed) AS idrs_reclipped,
               (SELECT n FROM before) AS rows_before,
               (SELECT count(*) FROM written) AS rows_after,
               (SELECT count(*) FROM stale) AS rows_removed;
    """
    rows = run_query(sql, (property_id, clips_json(clips)))
    return rows[0] if rows else None
