"""
SQL for the pay items entered by hand on an imported site (icid.idr_report_site_pay_items, Slice V1): a contract item
of the IDR's project and a quantity, on one of a report's sites. They add to what the site's flags and curb segments
fill (no check against them), and are billed at the IDR's approval as one 'from_sites' quantity row per report and
pay item (insert_site_quantities_cte), deleted with the rest when it is unlocked or deleted.
"""

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# A site pay item as every function here returns it (sp), with its contract item's number, budget code, description
# and unit
_COLUMNS = """
            sp.id, sp.report_site_id, sp.contract_item_id, sp.quantity, sp.note, sp.added_at, sp.added_by,
            ci.item_no AS pay_item_ref, ci.budget_code, ci.description, ci.unit"""
_FROM = """
        JOIN icid.contract_items ci ON ci.contract_item_id = sp.contract_item_id"""
# A contract item's unit (ci) in the short form icid.quantities keeps (api/services/pay_item_units.py's UNIT_FORMS):
# periods and spaces dropped, capitals, the long forms a seeded row may have shortened
_UNIT_KEY = "upper(regexp_replace(ci.unit, '[.[:space:]]', '', 'g'))"
SITE_UNIT = f"""(CASE {_UNIT_KEY}
                WHEN 'EACH' THEN 'EA' WHEN 'TONS' THEN 'TN' WHEN 'TON' THEN 'TN' WHEN 'LBS' THEN 'LB'
                WHEN 'UNITS' THEN 'UNIT' WHEN 'SETS' THEN 'SET' WHEN 'P/HR' THEN 'HR' WHEN 'CREW/HR' THEN 'CREWHR'
                ELSE {_UNIT_KEY} END)"""


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
                      note: Optional[str], added_by: UUID) -> Optional[dict[str, Any]]:
    """
    Enter a pay item on a report's site. Adds nothing unless the report has the site and the contract item is one of
    the IDR's project.
    Takes the report's and the site's uuids, the contract item's, the quantity, the note and the user entering it.
    Returns the new pay item dict, or None when nothing was added.
    """
    sql = (
        """
        WITH sp AS (
            INSERT INTO icid.idr_report_site_pay_items (report_site_id, contract_item_id, quantity, note, added_by)
            SELECT rs.id, ci.contract_item_id, %s, %s, %s
            FROM icid.idr_report_sites rs
            JOIN icid.idrs i ON i.idr_id = rs.idr_id
            JOIN icid.contract_items ci ON ci.project_id = i.project_id
            WHERE rs.report_id = %s AND rs.id = %s AND ci.contract_item_id = %s
            RETURNING *
        )
        SELECT""" + _COLUMNS + "\n        FROM sp" + _FROM + ";"
    )
    rows = run_query(sql, (quantity, note, added_by, report_id, site_id, contract_item_id))
    return rows[0] if rows else None


def update_site_pay_item(report_id: UUID, site_id: UUID, item_id: UUID, quantity: Optional[Decimal],
                         note: Optional[str], note_sent: bool) -> Optional[dict[str, Any]]:
    """
    Change a site pay item's quantity, its note, or both.
    Takes the report's, the site's and the pay item's uuids, the new quantity (None keeps it), the new note and whether
    one was sent (a note sent as None clears it).
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
        )
        SELECT""" + _COLUMNS + "\n        FROM sp" + _FROM + ";"
    )
    rows = run_query(sql, (quantity, note_sent, note, report_id, site_id, item_id))
    return rows[0] if rows else None


def delete_site_pay_item(report_id: UUID, site_id: UUID, item_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Remove a pay item from a report's site.
    Takes the report's, the site's and the pay item's uuids.
    Returns the deleted row's id in a list ([] when the report's site had no such pay item), or None on failure.
    """
    sql = """
        DELETE FROM icid.idr_report_site_pay_items sp
        USING icid.idr_report_sites rs
        WHERE rs.id = sp.report_site_id AND rs.report_id = %s AND rs.id = %s AND sp.id = %s
        RETURNING sp.id;"""
    return run_query(sql, (report_id, site_id, item_id))


def idr_site_pay_items(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every pay item entered on an IDR's sites, with its site's report and place: what the IDR's pay-item sources
    and the check on its reports' hand-entered items read.
    Takes the IDR's uuid.
    Returns the pay item dicts with report_id, property_id and the site's address fields, in the order added; None on failure.
    """
    sql = (
        "\n        SELECT" + _COLUMNS + """,
            rs.report_id, rs.property_id, p.kind::text AS kind, p.address, p.intersection_street_1,
            p.intersection_street_2, p.quadrant::text AS quadrant, p.facing_street::text AS facing_street
        FROM icid.idr_report_site_pay_items sp
        JOIN icid.idr_report_sites rs ON rs.id = sp.report_site_id
        JOIN icid.properties p ON p.id = rs.property_id""" + _FROM + """
        WHERE rs.idr_id = %s
        ORDER BY sp.added_at, sp.id;"""
    )
    return run_query(sql, (idr_id,))


def insert_site_quantities_cte(idrs: str) -> str:
    """
    Write the CTE that inserts an IDR's from_sites quantity rows: the pay items entered on its sites, summed per report
    and pay item, each row taking its report's type, its contract item's number, budget code and description, and its
    unit in short form (SITE_UNIT).
    Takes the name of a CTE defined earlier in the statement (written here, never from a request) holding the IDR
    (idr_id, project_id, report_date, reporter_uuid): when it holds none, nothing is inserted.
    Returns the CTE (site_quantities), to append to the statement's WITH list; no parameters.
    """
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
            GROUP BY i.project_id, i.idr_id, i.report_date, i.reporter_uuid, r.report_type, ci.contract_item_id
            RETURNING quantity_id
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
