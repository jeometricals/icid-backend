"""
SQL for which curb segments an IDR covers (curb billing): its attribution (icid.idr_segment_attributions, the
overrides) and its claims (segments.claimed_by_idr_id, set when it is approved), as idr_flags is for flags.

An IDR's segments by default are the live segments on the sites its reports imported (icid.idr_report_sites; none
without one); an override excludes one of those or includes one from elsewhere in its project. Approval claims them: the statement that
approves the IDR marks them constructed, and the one that unlocks or deletes it releases them, as data-modifying CTEs
built here.
"""

from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries.flags import FLAG_UNIT
from api.queries.idr_report_site_pay_items import overridden_sql
from api.queries.segments import EFFECTIVE_LENGTH

# A segment whose site overrides its pay item in the IDR (Slice X.1), as idr_flags has it for flags
_OVERRIDDEN = overridden_sql("i.idr_id", "p.id", "s.pay_item_id")

# A segment's quantity in its pay item's unit (ci, its contract item): for a pay item in LF, its reported length
# when it has one (Slice X.2), else its drawn one; NULL for any other unit, or with neither
SEGMENT_AMOUNT = f"(CASE WHEN {FLAG_UNIT} = 'LF' THEN {EFFECTIVE_LENGTH} END)"
# A segment as an IDR lists it: where it is (its property, and its page), what it is mapped to, its length and what it
# comes to in its pay item's unit, whose it is (state: attributed, excluded, claimed by this IDR, or by another) and
# whether its site overrides its pay item (overridden), and how many points it is drawn through
_SEGMENT_COLUMNS = f"""
            s.segment_id, pg.pir_id, s.pir_page_id, pg.page_number, s.segment_type, s.length_ft, s.reported_length_ft,
            s.note,
            p.id AS property_id, p.kind::text AS property_kind, p.boro::text AS boro, p.block, p.lot, p.address,
            p.intersection_street_1, p.intersection_street_2, p.quadrant::text AS quadrant,
            p.facing_street::text AS facing_street,
            s.pay_item_id, ci.item_no AS pay_item_ref, ci.budget_code, ci.description AS pay_item_description,
            CASE WHEN ci.contract_item_id IS NULL THEN NULL ELSE {FLAG_UNIT} END AS unit,
            CASE WHEN ci.contract_item_id IS NULL THEN NULL ELSE {SEGMENT_AMOUNT} END AS amount,
            a.action AS override, (rs.property_id IS NOT NULL) AS on_imported_site,
            s.claimed_by_idr_id, claimer.idr_number AS claimed_by_idr_number, s.constructed_on, s.deleted_at,
            {_OVERRIDDEN} AS overridden,
            jsonb_array_length(s.points) AS point_count,
            CASE
                WHEN s.claimed_by_idr_id = i.idr_id THEN 'claimed'
                WHEN s.claimed_by_idr_id IS NOT NULL THEN 'claimed_elsewhere'
                WHEN a.action = 'exclude' THEN 'excluded'
                ELSE 'attributed'
            END AS state"""
# From an IDR (i) to every segment of its project, with what an IDR's list reads of each
_FROM = """
        FROM icid.idrs i
        JOIN icid.properties p ON p.project_id = i.project_id
        JOIN icid.pirs pir ON pir.property_id = p.id
        JOIN icid.pir_pages pg ON pg.pir_id = pir.id
        JOIN icid.segments s ON s.pir_page_id = pg.pir_page_id
        LEFT JOIN icid.idr_segment_attributions a ON a.idr_id = i.idr_id AND a.segment_id = s.segment_id
        LEFT JOIN icid.idr_report_sites rs ON rs.idr_id = i.idr_id AND rs.property_id = p.id
        LEFT JOIN icid.contract_items ci ON ci.contract_item_id = s.pay_item_id
        LEFT JOIN icid.idrs claimer ON claimer.idr_id = s.claimed_by_idr_id"""
# A segment drawn now: it, its page, its PIR and its property are live
_LIVE = "s.deleted_at IS NULL AND pg.deleted_at IS NULL AND pir.deleted_at IS NULL AND p.deleted_at IS NULL"
# Which of an IDR's project's segments it lists: before approval, the live ones on the sites its reports imported or
# included; once approved, those it claimed (whatever has happened to them since)
_IN_SCOPE = f"({_LIVE} AND (rs.property_id IS NOT NULL OR a.action = 'include'))"
_CLAIMED = "s.claimed_by_idr_id = i.idr_id"
_ORDER = """
        ORDER BY COALESCE(p.address, p.intersection_street_1 || ' & ' || p.intersection_street_2), p.quadrant,
                 pg.page_number, s.created_at"""
# A segment drawn now, for a statement elsewhere reading s, pg, pir and p as this module does
LIVE_SEGMENT = _LIVE
# The segments of a project an open IDR has attributed to it and none has claimed, each once: in its scope and not
# excluded. A subquery, its one parameter the project id (the progress rollup's in flight).
IN_FLIGHT_SEGMENT_IDS = (
    "\n        SELECT DISTINCT s.segment_id" + _FROM
    + "\n        WHERE i.project_id = %s AND i.deleted_at IS NULL"
    + " AND i.status IN ('draft', 'submitted', 'stage1_review', 'stage2_review')"
    + f"\n          AND {_IN_SCOPE} AND a.action IS DISTINCT FROM 'exclude' AND s.claimed_by_idr_id IS NULL"
)
# One property's curb segments by IDR (Slice V5), as idr_flags.PROPERTY_FLAG_LINES lists its flags (overridden ones left
# out). A subquery, its one parameter the property's id.
PROPERTY_SEGMENT_LINES = (
    "\n        SELECT i.idr_id, 'segments' AS source, s.segment_id AS element_id, ci.contract_item_id, ci.item_no,"
    + f" ci.budget_code, ci.description, {FLAG_UNIT} AS unit, {SEGMENT_AMOUNT} AS amount" + _FROM
    + f"\n        WHERE p.id = %s AND i.deleted_at IS NULL AND ci.contract_item_id IS NOT NULL AND NOT {_OVERRIDDEN}"
    + f"\n          AND ((i.status = 'approved' AND {_CLAIMED})"
    + "\n               OR (i.status IN ('draft', 'submitted', 'stage1_review', 'stage2_review')"
    + f" AND {_IN_SCOPE} AND a.action IS DISTINCT FROM 'exclude' AND s.claimed_by_idr_id IS NULL))"
)


def list_idr_segments(idr_id: UUID, claimed_only: bool) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the segments an IDR lists: before approval, the live segments in its scope (on the sites its reports imported, or included by an override), each with its state; once approved, the segments it claimed.
    Takes the IDR's uuid and whether to list only the segments it claimed (an approved IDR).
    Returns the segment dicts (see _SEGMENT_COLUMNS) by site, page and drawing order; [] for an unknown IDR; None on failure.
    """
    sql = (
        "\n        SELECT" + _SEGMENT_COLUMNS + _FROM
        + f"\n        WHERE i.idr_id = %s AND {_CLAIMED if claimed_only else _IN_SCOPE}"
        + _ORDER + ";"
    )
    return run_query(sql, (idr_id,))


def get_project_segment(segment_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a segment as an IDR's override checks it: its project, its claim and whether it or anything above it is deleted.
    Takes the segment's uuid.
    Returns {segment_id, project_id, claimed_by_idr_id, claimed_by_idr_number, deleted (bool)}, or None when there is no such segment.
    """
    sql = """
        SELECT s.segment_id, p.project_id, s.claimed_by_idr_id, c.idr_number AS claimed_by_idr_number,
               (s.deleted_at IS NOT NULL OR pg.deleted_at IS NOT NULL OR pir.deleted_at IS NOT NULL
                OR p.deleted_at IS NOT NULL) AS deleted
        FROM icid.segments s
        JOIN icid.pir_pages pg ON pg.pir_page_id = s.pir_page_id
        JOIN icid.pirs pir ON pir.id = pg.pir_id
        JOIN icid.properties p ON p.id = pir.property_id
        LEFT JOIN icid.idrs c ON c.idr_id = s.claimed_by_idr_id
        WHERE s.segment_id = %s;
    """
    rows = run_query(sql, (segment_id,))
    return rows[0] if rows else None


def segment_in_default_scope(idr_id: UUID, segment_id: UUID) -> Optional[bool]:
    """
    Whether a segment is on one of the sites an IDR's reports imported, so the IDR holds it without an override.
    Takes the IDR's and the segment's uuids.
    Returns True or False, or None on failure.
    """
    sql = """
        SELECT EXISTS (
            SELECT 1
            FROM icid.idr_report_sites rs
            JOIN icid.pirs pir ON pir.property_id = rs.property_id
            JOIN icid.pir_pages pg ON pg.pir_id = pir.id
            JOIN icid.segments s ON s.pir_page_id = pg.pir_page_id
            WHERE rs.idr_id = %s AND s.segment_id = %s
        ) AS in_scope;
    """
    rows = run_query(sql, (idr_id, segment_id))
    return rows[0]["in_scope"] if rows else None


def set_segment_attribution(idr_id: UUID, segment_id: UUID, action: str, set_by: UUID) -> Optional[dict[str, Any]]:
    """
    Record an override of an IDR's attribution of a segment, replacing any it had.
    Takes the IDR's and the segment's uuids, the action ('exclude' or 'include') and the uuid of the user setting it.
    Returns the override row, or None on failure.
    """
    sql = """
        INSERT INTO icid.idr_segment_attributions (idr_id, segment_id, action, set_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (idr_id, segment_id) DO UPDATE
        SET action = EXCLUDED.action, set_by = EXCLUDED.set_by, set_at = now()
        RETURNING idr_id, segment_id, action, set_by, set_at;
    """
    rows = run_query(sql, (idr_id, segment_id, action, set_by))
    return rows[0] if rows else None


def clear_segment_attribution(idr_id: UUID, segment_id: UUID) -> Optional[int]:
    """
    Drop an IDR's override of a segment, so its default holds again.
    Takes the IDR's and the segment's uuids.
    Returns how many overrides were dropped (0 or 1), or None on failure.
    """
    sql = """
        WITH dropped AS (
            DELETE FROM icid.idr_segment_attributions
            WHERE idr_id = %s AND segment_id = %s
            RETURNING 1
        )
        SELECT count(*) AS dropped FROM dropped;
    """
    rows = run_query(sql, (idr_id, segment_id))
    return rows[0]["dropped"] if rows else None


def claimed_segment_ids(idr_id: UUID) -> Optional[list[UUID]]:
    """
    List the segments an IDR has claimed.
    Takes the IDR's uuid.
    Returns their uuids (empty when it has none), or None on failure.
    """
    rows = run_query("SELECT segment_id FROM icid.segments WHERE claimed_by_idr_id = %s ORDER BY segment_id;", (idr_id,))
    return None if rows is None else [row["segment_id"] for row in rows]


# The segment uuids an admin_delete note lists, from a SQL expression holding the note (text, or NULL)
RELEASED_SEGMENT_IDS = (
    "SELECT jsonb_array_elements_text(COALESCE({note}, '{{}}')::jsonb -> 'released_segment_ids')::uuid"
)


def released_segment_claims(idr_id: UUID) -> Optional[dict[str, Any]]:
    """
    Read which segments a deleted IDR's delete released (its latest admin_delete audit row's note: {"released_segment_ids": [...]}), and which of those another IDR has claimed since.
    Takes the IDR's uuid.
    Returns {released (uuids), taken: [{segment_id, claimed_by_idr_id, claimed_by_idr_number}]}; both empty when there is no record; None on failure.
    """
    sql = f"""
        WITH record AS (
            SELECT a.note
            FROM icid.idr_audit a
            WHERE a.idr_id = %s AND a.action = 'admin_delete' AND a.from_status <> 'deleted'
            ORDER BY a.created_at DESC
            LIMIT 1
        ),
        released AS (
            {RELEASED_SEGMENT_IDS.format(note="(SELECT note FROM record)")} AS segment_id
        )
        SELECT
            ARRAY(SELECT segment_id FROM released) AS released,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'segment_id', s.segment_id, 'claimed_by_idr_id', s.claimed_by_idr_id,
                    'claimed_by_idr_number', c.idr_number))
                FROM released r
                JOIN icid.segments s ON s.segment_id = r.segment_id
                JOIN icid.idrs c ON c.idr_id = s.claimed_by_idr_id
                WHERE s.claimed_by_idr_id <> %s
            ), '[]'::jsonb) AS taken;
    """
    rows = run_query(sql, (idr_id, idr_id))
    return rows[0] if rows else None


def claim_segments_cte(idrs: str, segment_ids: Optional[str] = None) -> str:
    """
    Write the CTE that claims segments for the IDR a statement approves: each still unclaimed is marked constructed, on the IDR's report_date. One another IDR claimed meanwhile is left alone, so a segment is only ever claimed once.
    Takes the name of a CTE defined earlier in the statement holding the IDR (idr_id, report_date; written here, never from a request), and the SQL that lists the segments' uuids (written here too; None takes them as one uuid[] parameter).
    Returns the CTE (claimed_segments, the uuids it claimed), to append to the statement's WITH list.
    """
    ids = "SELECT unnest(%s::uuid[])" if segment_ids is None else segment_ids
    return f"""
        claimed_segments AS (
            UPDATE icid.segments s
            SET claimed_by_idr_id = i.idr_id, constructed_on = i.report_date
            FROM {idrs} i
            WHERE s.segment_id IN ({ids}) AND s.claimed_by_idr_id IS NULL
            RETURNING s.segment_id
        )"""


def release_segments_cte(idrs: str) -> str:
    """
    Write the CTE that releases every segment claimed by the IDR a statement unlocks or deletes, so any IDR can be attributed them again.
    Takes the name of a CTE defined earlier in the statement holding the IDR (idr_id).
    Returns the CTE (released_segments, the uuids it released), to append to the statement's WITH list; no parameters.
    """
    return f"""
        released_segments AS (
            UPDATE icid.segments s
            SET claimed_by_idr_id = NULL, constructed_on = NULL
            FROM {idrs} i
            WHERE s.claimed_by_idr_id = i.idr_id
            RETURNING s.segment_id
        )"""


def reclaim_segment_ids_sql(target: str) -> str:
    """
    The SQL listing the segments an un-deleted IDR claims again: those its delete released, as its audit row recorded them.
    Takes the name of the statement's CTE holding the deleted IDR's audit note (column note_before).
    Returns the SELECT, for claim_segments_cte.
    """
    return RELEASED_SEGMENT_IDS.format(note=f"(SELECT note_before FROM {target})")


def insert_segment_quantities_cte(idrs: str, claimed: str = "claimed_segments") -> str:
    """
    Write the CTE that inserts an IDR's from_segments quantity rows: the segments it just claimed, their lengths (a reported length over the drawn one) summed per pay item in LF and rounded to 2 decimal places, as report type PIR_SEGMENTS. A segment without a pay item, with one in another unit, without a length (no page scale and none reported), or whose site overrides its pay item in the IDR (the override bills instead) adds nothing.
    Takes the names of two CTEs defined earlier in the statement (written here, never from a request): the one holding the IDR (idr_id, project_id, report_date, reporter_uuid) and the one listing the segments it claimed (segment_id).
    Returns the CTE (segment_quantities), to append to the statement's WITH list; no parameters.
    """
    return f"""
        segment_quantities AS (
            INSERT INTO icid.quantities (
                project_id, idr_id, report_date, reporter_uuid, report_type, pay_item_ref, budget_code, description,
                amount, unit, source
            )
            SELECT i.project_id, i.idr_id, i.report_date, i.reporter_uuid, 'PIR_SEGMENTS', ci.item_no, ci.budget_code,
                   ci.description, round(sum({SEGMENT_AMOUNT}), 2), 'LF', 'from_segments'
            FROM {idrs} i
            JOIN icid.segments s ON s.segment_id IN (SELECT segment_id FROM {claimed})
            JOIN icid.pir_pages pg ON pg.pir_page_id = s.pir_page_id
            JOIN icid.pirs pir ON pir.id = pg.pir_id
            JOIN icid.contract_items ci ON ci.contract_item_id = s.pay_item_id
            WHERE {SEGMENT_AMOUNT} IS NOT NULL AND NOT {overridden_sql("i.idr_id", "pir.property_id", "s.pay_item_id")}
            GROUP BY i.project_id, i.idr_id, i.report_date, i.reporter_uuid, ci.contract_item_id
            RETURNING quantity_id
        )"""
