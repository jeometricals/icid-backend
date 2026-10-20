"""
SQL for which flags an IDR covers: its attribution (icid.idr_flag_attributions, the overrides) and its claims
(flags.claimed_by_idr_id, set when it is approved).

An IDR's flags by default are the live flags on the sites its reports imported (icid.idr_report_sites, Slice U; none
without one); an override excludes one of those or includes one from elsewhere in its project. Approval claims them: the statement
that approves the IDR marks them constructed, and the one that unlocks or deletes it releases them, as data-modifying
CTEs built here (there are no multi-statement transactions).
"""

import json
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries.flags import FLAG_AMOUNT, FLAG_QUANTITY, FLAG_UNIT, QUANTITY_LATERALS

# A flag as an IDR lists it: where it is (its property, and its page), what it is mapped to, how much it comes to
# in square feet and in its pay item's unit, and whose it is (state: attributed, excluded, claimed by this IDR, or
# claimed by another)
_FLAG_COLUMNS = f"""
            f.id AS flag_id, f.pir_id, f.pir_page_id, pg.page_number, f.display_order, f.shape, f.surveyed_thickness,
            p.id AS property_id, p.kind::text AS property_kind, p.boro::text AS boro, p.block, p.lot, p.address,
            p.intersection_street_1, p.intersection_street_2, p.quadrant::text AS quadrant,
            p.facing_street::text AS facing_street,
            f.pay_item_id, ci.item_no AS pay_item_ref, ci.budget_code, ci.description AS pay_item_description,
            CASE WHEN ci.contract_item_id IS NULL THEN NULL ELSE {FLAG_UNIT} END AS unit,
            {FLAG_QUANTITY} AS quantity_sf,
            CASE WHEN ci.contract_item_id IS NULL THEN NULL ELSE {FLAG_AMOUNT} END AS amount,
            a.action AS override, (rs.property_id IS NOT NULL) AS on_imported_site,
            f.claimed_by_idr_id, claimer.idr_number AS claimed_by_idr_number, f.is_constructed, f.constructed_on,
            f.deleted_at,
            CASE
                WHEN f.claimed_by_idr_id = i.idr_id THEN 'claimed'
                WHEN f.claimed_by_idr_id IS NOT NULL THEN 'claimed_elsewhere'
                WHEN a.action = 'exclude' THEN 'excluded'
                ELSE 'attributed'
            END AS state"""
# From an IDR (i) to every flag of its project, with what an IDR's list reads of each
_FROM = f"""
        FROM icid.idrs i
        JOIN icid.properties p ON p.project_id = i.project_id
        JOIN icid.pirs pir ON pir.property_id = p.id
        JOIN icid.flags f ON f.pir_id = pir.id
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
        LEFT JOIN icid.idr_flag_attributions a ON a.idr_id = i.idr_id AND a.flag_id = f.id
        LEFT JOIN icid.idr_report_sites rs ON rs.idr_id = i.idr_id AND rs.property_id = p.id
        LEFT JOIN icid.contract_items ci ON ci.contract_item_id = f.pay_item_id
        LEFT JOIN icid.idrs claimer ON claimer.idr_id = f.claimed_by_idr_id{QUANTITY_LATERALS}"""
# A flag drawn now: it, its page, its PIR and its property are live
_LIVE = "f.deleted_at IS NULL AND pg.deleted_at IS NULL AND pir.deleted_at IS NULL AND p.deleted_at IS NULL"
# Which of an IDR's project's flags it lists: before approval, the live ones on the sites its reports imported
# (icid.idr_report_sites, Slice U) or included; once approved, those it claimed (whatever has happened to them since)
_IN_SCOPE = f"({_LIVE} AND (rs.property_id IS NOT NULL OR a.action = 'include'))"
_CLAIMED = "f.claimed_by_idr_id = i.idr_id"
# How an IDR's flags are listed: by site, then page, then the order they were drawn in
_ORDER = """
        ORDER BY COALESCE(p.address, p.intersection_street_1 || ' & ' || p.intersection_street_2), p.quadrant,
                 pg.page_number, f.display_order, f.created_at"""
# A flag drawn now, for a statement elsewhere reading f, pg, pir and p as this module does
LIVE_FLAG = _LIVE
# The flags of a project an open IDR (a draft, or one submitted or in review) has attributed to it and none has
# claimed, each once: in its scope and not excluded. A subquery, its one parameter the project id (the progress
# rollup's in flight).
IN_FLIGHT_FLAG_IDS = (
    "\n        SELECT DISTINCT f.id" + _FROM
    + "\n        WHERE i.project_id = %s AND i.deleted_at IS NULL"
    + " AND i.status IN ('draft', 'submitted', 'stage1_review', 'stage2_review')"
    + f"\n          AND {_IN_SCOPE} AND a.action IS DISTINCT FROM 'exclude' AND f.claimed_by_idr_id IS NULL"
)


def list_idr_flags(idr_id: UUID, claimed_only: bool) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the flags an IDR lists: before approval, the live flags in its scope (on the sites its reports imported, or included by an override), each with its state; once approved, the flags it claimed.
    Takes the IDR's uuid and whether to list only the flags it claimed (an approved IDR).
    Returns the flag dicts (see _FLAG_COLUMNS) by site, page and drawing order; [] for an unknown IDR; None on failure.
    """
    sql = (
        "\n        SELECT" + _FLAG_COLUMNS + _FROM
        + f"\n        WHERE i.idr_id = %s AND {_CLAIMED if claimed_only else _IN_SCOPE}"
        + _ORDER + ";"
    )
    return run_query(sql, (idr_id,))


def in_default_scope(idr_id: UUID, flag_id: UUID) -> Optional[bool]:
    """
    Whether a flag is on one of the sites an IDR's reports imported, so the IDR holds it without an override.
    Takes the IDR's and the flag's uuids.
    Returns True or False, or None on failure.
    """
    sql = """
        SELECT EXISTS (
            SELECT 1
            FROM icid.idr_report_sites rs
            JOIN icid.pirs pir ON pir.property_id = rs.property_id
            JOIN icid.flags f ON f.pir_id = pir.id
            WHERE rs.idr_id = %s AND f.id = %s
        ) AS in_scope;
    """
    rows = run_query(sql, (idr_id, flag_id))
    return rows[0]["in_scope"] if rows else None


def set_attribution(idr_id: UUID, flag_id: UUID, action: str, set_by: UUID) -> Optional[dict[str, Any]]:
    """
    Record an override of an IDR's attribution of a flag, replacing any it had.
    Takes the IDR's and the flag's uuids, the action ('exclude' or 'include') and the uuid of the user setting it.
    Returns the override row, or None on failure.
    """
    sql = """
        INSERT INTO icid.idr_flag_attributions (idr_id, flag_id, action, set_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (idr_id, flag_id) DO UPDATE
        SET action = EXCLUDED.action, set_by = EXCLUDED.set_by, set_at = now()
        RETURNING idr_id, flag_id, action, set_by, set_at;
    """
    rows = run_query(sql, (idr_id, flag_id, action, set_by))
    return rows[0] if rows else None


def clear_attribution(idr_id: UUID, flag_id: UUID) -> Optional[int]:
    """
    Drop an IDR's override of a flag, so its default holds again.
    Takes the IDR's and the flag's uuids.
    Returns how many overrides were dropped (0 or 1), or None on failure.
    """
    sql = """
        WITH dropped AS (
            DELETE FROM icid.idr_flag_attributions
            WHERE idr_id = %s AND flag_id = %s
            RETURNING 1
        )
        SELECT count(*) AS dropped FROM dropped;
    """
    rows = run_query(sql, (idr_id, flag_id))
    return rows[0]["dropped"] if rows else None


def claimed_flag_ids(idr_id: UUID) -> Optional[list[UUID]]:
    """
    List the flags an IDR has claimed.
    Takes the IDR's uuid.
    Returns their uuids (empty when it has none), or None on failure.
    """
    rows = run_query("SELECT id FROM icid.flags WHERE claimed_by_idr_id = %s ORDER BY id;", (idr_id,))
    return None if rows is None else [row["id"] for row in rows]


def released_claims(idr_id: UUID) -> Optional[dict[str, Any]]:
    """
    Read what a deleted IDR gave up when it was deleted: the status it had and the flags whose claims its delete released, from its latest admin_delete audit row (the note: {"released_flag_ids": [...]}), and which of those flags another IDR has claimed since.
    Takes the IDR's uuid.
    Returns {status_before, released (uuids), taken: [{flag_id, claimed_by_idr_id, claimed_by_idr_number}]}; status_before None and the lists empty when there is no record; None on failure.
    """
    sql = f"""
        WITH record AS (
            SELECT a.from_status, a.note
            FROM icid.idr_audit a
            WHERE a.idr_id = %s AND a.action = 'admin_delete' AND a.from_status <> 'deleted'
            ORDER BY a.created_at DESC
            LIMIT 1
        ),
        released AS (
            {_RELEASED_IDS.format(note="(SELECT note FROM record)")} AS flag_id
        )
        SELECT
            (SELECT from_status FROM record) AS status_before,
            ARRAY(SELECT flag_id FROM released) AS released,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'flag_id', f.id, 'claimed_by_idr_id', f.claimed_by_idr_id, 'claimed_by_idr_number', c.idr_number))
                FROM released r
                JOIN icid.flags f ON f.id = r.flag_id
                JOIN icid.idrs c ON c.idr_id = f.claimed_by_idr_id
                WHERE f.claimed_by_idr_id <> %s
            ), '[]'::jsonb) AS taken;
    """
    rows = run_query(sql, (idr_id, idr_id))
    return rows[0] if rows else None


# The flag uuids an admin_delete note lists, from a SQL expression holding the note (text, or NULL)
_RELEASED_IDS = "SELECT jsonb_array_elements_text(COALESCE({note}, '{{}}')::jsonb -> 'released_flag_ids')::uuid"


def released_note(flag_ids: list[UUID], segment_ids: Optional[list[UUID]] = None) -> str:
    """
    Write the admin_delete audit note that records which flags and curb segments a delete released, for an un-delete to claim again.
    Takes the flags' uuids and the segments' (none when None).
    Returns the note, as JSON text: {"released_flag_ids": [...]}, with "released_segment_ids" when there are any.
    """
    note: dict[str, list[str]] = {"released_flag_ids": [str(flag_id) for flag_id in flag_ids]}
    if segment_ids:
        note["released_segment_ids"] = [str(segment_id) for segment_id in segment_ids]
    return json.dumps(note)


def claim_flags_cte(idrs: str, flag_ids: Optional[str] = None) -> str:
    """
    Write the CTE that claims flags for the IDR a statement approves: each still unclaimed is marked constructed, on the IDR's report_date. A flag another IDR claimed meanwhile is left alone, so one flag is only ever claimed once.
    Takes the name of a CTE defined earlier in the statement holding the IDR (idr_id, report_date; written here, never from a request), and the SQL that lists the flags' uuids (written here too; None takes them as one uuid[] parameter).
    Returns the CTE (claimed_flags, the uuids it claimed), to append to the statement's WITH list.
    """
    ids = "SELECT unnest(%s::uuid[])" if flag_ids is None else flag_ids
    return f"""
        claimed_flags AS (
            UPDATE icid.flags f
            SET claimed_by_idr_id = i.idr_id, is_constructed = true, constructed_on = i.report_date
            FROM {idrs} i
            WHERE f.id IN ({ids}) AND f.claimed_by_idr_id IS NULL
            RETURNING f.id
        )"""


def release_flags_cte(idrs: str) -> str:
    """
    Write the CTE that releases every flag claimed by the IDR a statement unlocks or deletes: they are no longer constructed, and any IDR can be attributed them again.
    Takes the name of a CTE defined earlier in the statement holding the IDR (idr_id).
    Returns the CTE (released_flags, the uuids it released), to append to the statement's WITH list; no parameters.
    """
    return f"""
        released_flags AS (
            UPDATE icid.flags f
            SET claimed_by_idr_id = NULL, is_constructed = false, constructed_on = NULL
            FROM {idrs} i
            WHERE f.claimed_by_idr_id = i.idr_id
            RETURNING f.id
        )"""


def reclaim_ids_sql(target: str) -> str:
    """
    The SQL listing the flags an un-deleted IDR claims again: those its delete released, as its audit row recorded them.
    Takes the name of the statement's CTE holding the deleted IDR's audit note (column note_before).
    Returns the SELECT, for claim_flags_cte.
    """
    return _RELEASED_IDS.format(note=f"(SELECT note_before FROM {target})")
