"""
SQL for the pay items entered by hand on an imported site (icid.idr_report_site_pay_items, Slice V1): a contract item
of the IDR's project and a quantity, on one of a report's sites. They add to what the site's flags and curb segments
fill (no check against them), and are billed at the IDR's approval as one 'from_sites' quantity row per report and
pay item (insert_site_quantities_cte), deleted with the rest when it is unlocked or deleted.

An override (overrides_auto, Slice X.1, migration 056) instead replaces what the site's flags and curb segments come to
for its pay item in the IDR, higher or lower (0 too): those drawings still count as the IDR's and are claimed at
approval, but add nothing (overridden_sql), and the override bills in their place, only when the approval claims at
least one of them (drawings_sql); otherwise it is dropped and logged (dropped_overrides_cte). Setting, changing and
resetting one is logged in icid.idr_audit too (OVERRIDE_ACTIONS).
"""

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query

# A site pay item as every function here returns it (sp), with its contract item's number, budget code, description
# and unit
_COLUMNS = """
            sp.id, sp.report_site_id, sp.contract_item_id, sp.quantity, sp.note, sp.added_at, sp.added_by,
            sp.overrides_auto, ci.item_no AS pay_item_ref, ci.budget_code, ci.description, ci.unit"""
_FROM = """
        JOIN icid.contract_items ci ON ci.contract_item_id = sp.contract_item_id"""
# A contract item's unit (ci) in the short form icid.quantities keeps (api/services/pay_item_units.py's UNIT_FORMS):
# periods and spaces dropped, capitals, the long forms a seeded row may have shortened
_UNIT_KEY = "upper(regexp_replace(ci.unit, '[.[:space:]]', '', 'g'))"
SITE_UNIT = f"""(CASE {_UNIT_KEY}
                WHEN 'EACH' THEN 'EA' WHEN 'TONS' THEN 'TN' WHEN 'TON' THEN 'TN' WHEN 'LBS' THEN 'LB'
                WHEN 'UNITS' THEN 'UNIT' WHEN 'SETS' THEN 'SET' WHEN 'P/HR' THEN 'HR' WHEN 'CREW/HR' THEN 'CREWHR'
                ELSE {_UNIT_KEY} END)"""


# What an override's audit rows are logged as: set, changed, reset to the automatic quantity, and dropped at approval
OVERRIDE_SET, OVERRIDE_CHANGE, OVERRIDE_RESET, OVERRIDE_DROPPED = (
    "site_override_set", "site_override_change", "site_override_reset", "site_override_dropped")
OVERRIDE_ACTIONS = (OVERRIDE_SET, OVERRIDE_CHANGE, OVERRIDE_RESET, OVERRIDE_DROPPED)


class OverrideTakenError(Exception):
    """The site already overrides that pay item (uq_idr_report_site_pay_items_one_override)."""


def overridden_sql(idr: str, property_id: str, contract_item_id: str) -> str:
    """
    Write the condition that an IDR's site overrides a pay item: what a flag or curb segment of that property and pay
    item then adds to the IDR is nothing (the override bills instead).
    Takes three SQL expressions (written here, never from a request): the IDR's uuid, the property's and the contract item's.
    Returns the EXISTS condition; no parameters.
    """
    return f"""EXISTS (
                SELECT 1 FROM icid.idr_report_site_pay_items osp
                JOIN icid.idr_report_sites ors ON ors.id = osp.report_site_id
                WHERE osp.overrides_auto AND ors.idr_id = {idr} AND ors.property_id = {property_id}
                  AND osp.contract_item_id = {contract_item_id})"""


def drawings_sql(property_id: str, contract_item_id: str, flag_ids: str, segment_ids: str) -> str:
    """
    Write the condition that some of the given flags or curb segments are on a property and mapped to a pay item: whether
    an override has drawings of its own to replace (an approval's claimed ones, or an approved IDR's).
    Takes SQL expressions (written here): the property's and the contract item's uuids, and the SELECTs listing the flags'
    (id) and the segments' (segment_id) uuids.
    Returns the condition; no parameters.
    """
    return f"""(EXISTS (
                SELECT 1 FROM icid.flags df JOIN icid.pirs dpir ON dpir.id = df.pir_id
                WHERE df.id IN ({flag_ids}) AND dpir.property_id = {property_id} AND df.pay_item_id = {contract_item_id})
             OR EXISTS (
                SELECT 1 FROM icid.segments ds
                JOIN icid.pir_pages dpg ON dpg.pir_page_id = ds.pir_page_id
                JOIN icid.pirs dspir ON dspir.id = dpg.pir_id
                WHERE ds.segment_id IN ({segment_ids}) AND dspir.property_id = {property_id}
                  AND ds.pay_item_id = {contract_item_id}))"""


# An override's audit row, from a CTE (sp) holding the site pay item: written only when it is an override and words are
# given. Its note is JSON naming the site and the pay item, with the words shown. Takes four parameters: the actor, the
# action, the words, the words again.
_OVERRIDE_AUDIT = """
        logged AS (
            INSERT INTO icid.idr_audit (idr_id, actor_uuid, action, from_status, to_status, note)
            SELECT i.idr_id, %s, %s, i.status, i.status,
                   jsonb_build_object('site_id', rs.id, 'contract_item_id', sp.contract_item_id, 'text', %s::text)::text
            FROM sp
            JOIN icid.idr_report_sites rs ON rs.id = sp.report_site_id
            JOIN icid.idrs i ON i.idr_id = rs.idr_id
            WHERE sp.overrides_auto AND %s::text IS NOT NULL
        )"""


def list_site_pay_items(report_id: UUID, site_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the pay items entered on one of a report's sites, in the order they were added.
    Takes the report's and the site's uuids.
    Returns the pay item dicts (empty when the report has no such site), or None on failure.
    """
    sql = (
        "\n        SELECT" + _COLUMNS + """
        FROM icid.idr_report_site_pay_items sp
        JOIN icid.idr_report_sites rs ON rs.id = sp.report_site_id""" + _FROM + """
        WHERE rs.report_id = %s AND rs.id = %s
        ORDER BY sp.added_at, sp.id;"""
    )
    return run_query(sql, (report_id, site_id))


def add_site_pay_item(report_id: UUID, site_id: UUID, contract_item_id: UUID, quantity: Decimal,
                      note: Optional[str], added_by: UUID, overrides_auto: bool = False,
                      audit_text: Optional[str] = None) -> Optional[dict[str, Any]]:
    """
    Enter a pay item on a report's site, or an override of what its flags and curb segments come to (logged as
    site_override_set, in the same statement). Adds nothing unless the report has the site and the contract item is one
    of the IDR's project.
    Takes the report's and the site's uuids, the contract item's, the quantity, the note, the user entering it, whether
    it overrides and the audit row's words for an override.
    Returns the new pay item dict, or None when nothing was added; raises OverrideTakenError when the site already
    overrides that pay item.
    """
    sql = (
        """
        WITH sp AS (
            INSERT INTO icid.idr_report_site_pay_items (report_site_id, contract_item_id, quantity, note, added_by,
                                                        overrides_auto)
            SELECT rs.id, ci.contract_item_id, %s, %s, %s, %s
            FROM icid.idr_report_sites rs
            JOIN icid.idrs i ON i.idr_id = rs.idr_id
            JOIN icid.contract_items ci ON ci.project_id = i.project_id
            WHERE rs.report_id = %s AND rs.id = %s AND ci.contract_item_id = %s
            RETURNING *
        ),""" + _OVERRIDE_AUDIT + """
        SELECT""" + _COLUMNS + "\n        FROM sp" + _FROM + ";"
    )
    try:
        rows = run_query(sql, (quantity, note, added_by, overrides_auto, report_id, site_id, contract_item_id,
                               added_by, OVERRIDE_SET, audit_text, audit_text))
    except UniqueViolation as exc:
        raise OverrideTakenError(str(contract_item_id)) from exc
    return rows[0] if rows else None


def update_site_pay_item(report_id: UUID, site_id: UUID, item_id: UUID, quantity: Optional[Decimal],
                         note: Optional[str], note_sent: bool, actor: Optional[UUID] = None,
                         audit_text: Optional[str] = None) -> Optional[dict[str, Any]]:
    """
    Change a site pay item's quantity, its note, or both. A change of an override's quantity is logged as
    site_override_change, in the same statement.
    Takes the report's, the site's and the pay item's uuids, the new quantity (None keeps it), the new note and whether
    one was sent (a note sent as None clears it), the user changing it and the audit row's words (None logs nothing).
    Returns the updated pay item dict, or None when the report's site has no such pay item.
    """
    sql = (
        """
        WITH sp AS (
            UPDATE icid.idr_report_site_pay_items sp
            SET quantity = COALESCE(%s, sp.quantity), note = CASE WHEN %s THEN %s ELSE sp.note END
            FROM icid.idr_report_sites rs
            WHERE rs.id = sp.report_site_id AND rs.report_id = %s AND rs.id = %s AND sp.id = %s
            RETURNING sp.*
        ),""" + _OVERRIDE_AUDIT + """
        SELECT""" + _COLUMNS + "\n        FROM sp" + _FROM + ";"
    )
    rows = run_query(sql, (quantity, note_sent, note, report_id, site_id, item_id, actor, OVERRIDE_CHANGE, audit_text,
                           audit_text))
    return rows[0] if rows else None


def delete_site_pay_item(report_id: UUID, site_id: UUID, item_id: UUID, actor: Optional[UUID] = None,
                         audit_text: Optional[str] = None) -> Optional[list[dict[str, Any]]]:
    """
    Remove a pay item from a report's site. Removing an override resets the pay item to its automatic quantity, logged
    as site_override_reset in the same statement.
    Takes the report's, the site's and the pay item's uuids, the user removing it and the audit row's words for an
    override (None logs nothing).
    Returns the deleted row's id in a list ([] when the report's site had no such pay item), or None on failure.
    """
    sql = """
        WITH sp AS (
            DELETE FROM icid.idr_report_site_pay_items sp
            USING icid.idr_report_sites rs
            WHERE rs.id = sp.report_site_id AND rs.report_id = %s AND rs.id = %s AND sp.id = %s
            RETURNING sp.*
        ),""" + _OVERRIDE_AUDIT + """
        SELECT id FROM sp;"""
    return run_query(sql, (report_id, site_id, item_id, actor, OVERRIDE_RESET, audit_text, audit_text))


def latest_override_audit(idr_id: UUID, site_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the latest override audit row per pay item of one of an IDR's sites: what the site's table shows under each
    automatic row.
    Takes the IDR's and the site's uuids.
    Returns {contract_item_id, action, text, at, actor_name} rows, one per pay item; None on failure.
    """
    sql = """
        SELECT DISTINCT ON (n.contract_item_id) n.contract_item_id, a.action, n.text, a.created_at AS at,
               NULLIF(concat_ws(' ', u.first_name, u.last_name), '') AS actor_name
        FROM icid.idr_audit a
        CROSS JOIN LATERAL (
            SELECT (a.note::jsonb ->> 'contract_item_id')::uuid AS contract_item_id,
                   a.note::jsonb ->> 'site_id' AS site_id, a.note::jsonb ->> 'text' AS text
        ) n
        LEFT JOIN icid.users u ON u.uuid = a.actor_uuid
        WHERE a.idr_id = %s AND a.action = ANY(%s) AND n.site_id = %s::text
        ORDER BY n.contract_item_id, a.created_at DESC;"""
    return run_query(sql, (idr_id, list(OVERRIDE_ACTIONS), str(site_id)))


# An override its approved IDR (i) billed nothing for: the IDR claimed none of its flags or curb segments. A condition
# reading sp (the site pay item), rs (its site) and i, with no parameters.
OVERRIDE_DROPPED_SQL = "(sp.overrides_auto AND i.status = 'approved' AND NOT " + drawings_sql(
    "rs.property_id", "sp.contract_item_id", "SELECT id FROM icid.flags WHERE claimed_by_idr_id = i.idr_id",
    "SELECT segment_id FROM icid.segments WHERE claimed_by_idr_id = i.idr_id") + ")"


def idr_site_pay_items(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every pay item entered on an IDR's sites, with its site's report and place: what the IDR's pay-item sources
    and the check on its reports' hand-entered items read.
    Takes the IDR's uuid.
    Returns the pay item dicts with report_id, property_id, the site's address fields and override_dropped (an override
    its approved IDR billed nothing for: it claimed none of its flags or curb segments), in the order added; None on
    failure.
    """
    sql = (
        "\n        SELECT" + _COLUMNS + """,
            rs.report_id, rs.property_id, p.kind::text AS kind, p.address, p.intersection_street_1,
            p.intersection_street_2, p.quadrant::text AS quadrant, p.facing_street::text AS facing_street,
            """ + OVERRIDE_DROPPED_SQL + """ AS override_dropped
        FROM icid.idr_report_site_pay_items sp
        JOIN icid.idr_report_sites rs ON rs.id = sp.report_site_id
        JOIN icid.idrs i ON i.idr_id = rs.idr_id
        JOIN icid.properties p ON p.id = rs.property_id""" + _FROM + """
        WHERE rs.idr_id = %s
        ORDER BY sp.added_at, sp.id;"""
    )
    return run_query(sql, (idr_id,))


def insert_site_quantities_cte(idrs: str, claimed_flags: str = "claimed_flags",
                               claimed_segments: str = "claimed_segments") -> str:
    """
    Write the CTE that inserts an IDR's from_sites quantity rows: the pay items entered on its sites, summed per report
    and pay item, each row taking its report's type, its contract item's number, budget code and description, and its
    unit in short form (SITE_UNIT). An override counts only when the statement claims at least one of its own flags or
    curb segments (drawings_sql); else it is dropped (dropped_overrides_cte).
    Takes the names of CTEs defined earlier in the statement (written here, never from a request): the one holding the
    IDR (idr_id, project_id, report_date, reporter_uuid; when it holds none, nothing is inserted), and those listing the
    flags (id) and segments (segment_id) it claims.
    Returns the CTE (site_quantities), to append to the statement's WITH list; no parameters.
    """
    own = drawings_sql("rs.property_id", "sp.contract_item_id", f"SELECT id FROM {claimed_flags}",
                       f"SELECT segment_id FROM {claimed_segments}")
    return f"""
        site_quantities AS (
            INSERT INTO icid.quantities (
                project_id, idr_id, report_date, reporter_uuid, report_type, pay_item_ref, budget_code, description,
                amount, unit, source
            )
            SELECT i.project_id, i.idr_id, i.report_date, i.reporter_uuid, r.report_type, ci.item_no, ci.budget_code,
                   ci.description, sum(sp.quantity), {SITE_UNIT}, 'from_sites'
            FROM {idrs} i
            JOIN icid.idr_report_sites rs ON rs.idr_id = i.idr_id
            JOIN icid.idr_reports r ON r.report_id = rs.report_id
            JOIN icid.idr_report_site_pay_items sp ON sp.report_site_id = rs.id
            JOIN icid.contract_items ci ON ci.contract_item_id = sp.contract_item_id
            WHERE NOT sp.overrides_auto OR {own}
            GROUP BY i.project_id, i.idr_id, i.report_date, i.reporter_uuid, r.report_type, ci.contract_item_id
            RETURNING quantity_id
        )"""


def dropped_overrides_cte(idrs: str, actor: str, claimed_flags: str = "claimed_flags",
                          claimed_segments: str = "claimed_segments") -> str:
    """
    Write the CTE that logs the overrides an approval drops: those of the IDR's sites whose flags and curb segments it
    claims none of, each an audit row (site_override_dropped) saying "override dropped at approval (185 SF, flags
    already claimed by IDR-002)" ("curb segments" for an LF pay item; "no flags or curb segments left" when no other IDR
    holds them).
    Takes the names of CTEs defined earlier in the statement (written here, never from a request): the one holding the
    IDR (idr_id, status), and those listing the flags and segments it claims; and the SQL expression of the actor's uuid.
    Returns the CTE (dropped_overrides, the audit rows' ids), to append to the statement's WITH list; no parameters.
    """
    own = drawings_sql("rs.property_id", "sp.contract_item_id", f"SELECT id FROM {claimed_flags}",
                       f"SELECT segment_id FROM {claimed_segments}")
    return f"""
        dropped_overrides AS (
            INSERT INTO icid.idr_audit (idr_id, actor_uuid, action, from_status, to_status, note)
            SELECT i.idr_id, {actor}, '{OVERRIDE_DROPPED}', i.status, i.status,
                   jsonb_build_object(
                       'site_id', rs.id, 'contract_item_id', sp.contract_item_id,
                       'text', 'override dropped at approval (' || trim_scale(sp.quantity)::text || ' ' || {SITE_UNIT}
                               || ', ' || COALESCE(
                                   CASE WHEN {SITE_UNIT} = 'LF' THEN 'curb segments' ELSE 'flags' END
                                   || ' already claimed by ' || holders.numbers,
                                   'no flags or curb segments left') || ')'
                   )::text
            FROM {idrs} i
            JOIN icid.idr_report_sites rs ON rs.idr_id = i.idr_id
            JOIN icid.idr_report_site_pay_items sp ON sp.report_site_id = rs.id AND sp.overrides_auto
            JOIN icid.contract_items ci ON ci.contract_item_id = sp.contract_item_id
            LEFT JOIN LATERAL (
                SELECT string_agg(DISTINCT COALESCE(c.idr_number, 'another IDR'), ', ') AS numbers
                FROM (
                    SELECT f.claimed_by_idr_id AS holder
                    FROM icid.flags f JOIN icid.pirs hp ON hp.id = f.pir_id
                    WHERE hp.property_id = rs.property_id AND f.pay_item_id = sp.contract_item_id
                      AND f.claimed_by_idr_id IS NOT NULL AND f.claimed_by_idr_id <> i.idr_id
                    UNION
                    SELECT s.claimed_by_idr_id
                    FROM icid.segments s
                    JOIN icid.pir_pages hpg ON hpg.pir_page_id = s.pir_page_id
                    JOIN icid.pirs hsp ON hsp.id = hpg.pir_id
                    WHERE hsp.property_id = rs.property_id AND s.pay_item_id = sp.contract_item_id
                      AND s.claimed_by_idr_id IS NOT NULL AND s.claimed_by_idr_id <> i.idr_id
                ) h
                JOIN icid.idrs c ON c.idr_id = h.holder
            ) holders ON true
            WHERE NOT {own}
            RETURNING audit_id
        )"""


def pay_item_site_uses(project_id: str, contract_item_id: Optional[UUID] = None) -> Optional[list[dict[str, Any]]]:
    """
    List the IDRs whose sites hold a project's pay item (or any of its pay items): what an admin's pay-item delete is
    refused for.
    Takes the project id and the contract item's uuid (None for every item of the project).
    Returns {idr_id, idr_number, report_date, item_no, budget_code} rows, one per IDR and item, by work date; None on failure.
    """
    sql = """
        SELECT DISTINCT i.idr_id, i.idr_number, i.report_date, ci.item_no, ci.budget_code
        FROM icid.idr_report_site_pay_items sp
        JOIN icid.contract_items ci ON ci.contract_item_id = sp.contract_item_id
        JOIN icid.idr_report_sites rs ON rs.id = sp.report_site_id
        JOIN icid.idrs i ON i.idr_id = rs.idr_id
        WHERE ci.project_id = %s AND (%s::uuid IS NULL OR ci.contract_item_id = %s::uuid)
        ORDER BY i.report_date, i.idr_number, ci.item_no, ci.budget_code;"""
    return run_query(sql, (project_id, contract_item_id, contract_item_id))
