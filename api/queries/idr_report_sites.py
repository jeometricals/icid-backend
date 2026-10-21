"""
SQL for the sites a report imports (icid.idr_report_sites, Slice U): each a property of the IDR's project, with the
work done there. A site is imported once per IDR; its flags and curb segments are the IDR's (api/queries/idr_flags.py,
idr_segments.py), claimed at its approval.
"""

from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# A user's name as the lists show it: first and last, whichever is on file
_NAME = "NULLIF(concat_ws(' ', {u}.first_name, {u}.last_name), '')"
# A site as every function here returns it (rs), with its property's fields and the names of who added it and who
# last changed its work (V2)
_SITE_COLUMNS = f"""
            rs.id, rs.report_id, rs.idr_id, rs.property_id, rs.work_type, rs.work_type_other, rs.added_at, rs.added_by,
            rs.updated_at, rs.updated_by, {_NAME.format(u="ua")} AS added_by_name, {_NAME.format(u="uu")} AS updated_by_name,
            p.kind::text AS kind, p.boro::text AS boro, p.address, p.block, p.lot, p.intersection_street_1,
            p.intersection_street_2, p.quadrant::text AS quadrant, p.facing_street::text AS facing_street,
            p.ramp_type::text AS ramp_type, p.community_board, p.deleted_at AS property_deleted_at"""
_FROM = """
        JOIN icid.properties p ON p.id = rs.property_id
        LEFT JOIN icid.users ua ON ua.uuid = rs.added_by
        LEFT JOIN icid.users uu ON uu.uuid = rs.updated_by"""


def _read(where: str, params: tuple[Any, ...], order: str = "") -> Optional[list[dict[str, Any]]]:
    """
    Read sites by a condition on rs.
    Takes the WHERE condition, its parameters and an ORDER BY clause.
    Returns the site dicts, or None on failure.
    """
    sql = "\n        SELECT" + _SITE_COLUMNS + "\n        FROM icid.idr_report_sites rs" + _FROM + f"\n        WHERE {where}{order};"
    return run_query(sql, params)


def list_report_sites(report_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the sites a report imported, in the order they were added.
    Takes the report's uuid.
    Returns the site dicts with their properties, or None on failure.
    """
    return _read("rs.report_id = %s", (report_id,), "\n        ORDER BY rs.added_at, rs.id")


def get_report_site(report_id: UUID, site_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one site, only if the report imported it.
    Takes the report's and the site's uuids.
    Returns the site dict, or None when the report has no such site.
    """
    rows = _read("rs.report_id = %s AND rs.id = %s", (report_id, site_id))
    return rows[0] if rows else None


def find_idr_site(idr_id: UUID, property_id: UUID) -> Optional[dict[str, Any]]:
    """
    Find where an IDR already imported a property, for a 409: the site and its report's type and page.
    Takes the IDR's and the property's uuids.
    Returns {id, report_id, report_type, page_number, is_auto_generated}, or None when the IDR hasn't imported it.
    """
    sql = """
        SELECT rs.id, rs.report_id, r.report_type, r.page_number, r.is_auto_generated
        FROM icid.idr_report_sites rs
        JOIN icid.idr_reports r ON r.report_id = rs.report_id
        WHERE rs.idr_id = %s AND rs.property_id = %s;"""
    rows = run_query(sql, (idr_id, property_id))
    return rows[0] if rows else None


def add_report_site(report_id: UUID, idr_id: UUID, property_id: UUID, work_type: str,
                    work_type_other: Optional[str], added_by: UUID) -> Optional[dict[str, Any]]:
    """
    Import a site into a report. Adds nothing unless the property is a live one of the IDR's project, or when the IDR
    already imported it (the unique (idr_id, property_id)).
    Takes the report's and the IDR's uuids, the property's, the work type, its text for 'other' and the user adding it.
    Returns the new site dict with its property, or None when nothing was added.
    """
    sql = (
        """
        WITH rs AS (
            INSERT INTO icid.idr_report_sites (report_id, idr_id, property_id, work_type, work_type_other, added_by)
            SELECT %s, i.idr_id, p.id, %s, %s, %s
            FROM icid.idrs i
            JOIN icid.properties p ON p.project_id = i.project_id
            WHERE i.idr_id = %s AND p.id = %s AND p.deleted_at IS NULL
            ON CONFLICT (idr_id, property_id) DO NOTHING
            RETURNING *
        )
        SELECT""" + _SITE_COLUMNS + "\n        FROM rs" + _FROM + ";"
    )
    rows = run_query(sql, (report_id, work_type, work_type_other, added_by, idr_id, property_id))
    return rows[0] if rows else None


def update_report_site(report_id: UUID, site_id: UUID, work_type: str,
                       work_type_other: Optional[str], updated_by: UUID) -> Optional[dict[str, Any]]:
    """
    Change the work done at an imported site; when it does change, stamp updated_at and updated_by (V2).
    Takes the report's and the site's uuids, the work type, its text for 'other' (None otherwise) and the user changing it.
    Returns the updated site dict with its property, or None when the report has no such site.
    """
    sql = (
        """
        WITH changed AS (
            SELECT s.id, (s.work_type, s.work_type_other) IS DISTINCT FROM (%s::text, %s::text) AS is_changed
            FROM icid.idr_report_sites s
            WHERE s.report_id = %s AND s.id = %s
        ),
        rs AS (
            UPDATE icid.idr_report_sites s
            SET work_type = %s, work_type_other = %s,
                updated_at = CASE WHEN c.is_changed THEN now() ELSE s.updated_at END,
                updated_by = CASE WHEN c.is_changed THEN %s::uuid ELSE s.updated_by END
            FROM changed c
            WHERE s.id = c.id
            RETURNING s.*
        )
        SELECT""" + _SITE_COLUMNS + "\n        FROM rs" + _FROM + ";"
    )
    rows = run_query(sql, (work_type, work_type_other, report_id, site_id, work_type, work_type_other, updated_by))
    return rows[0] if rows else None


def delete_report_site(report_id: UUID, site_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Remove a site from a report (and so from its IDR's flags and curb segments).
    Takes the report's and the site's uuids.
    Returns the deleted row's id in a list ([] when the report had no such site), or None on failure.
    """
    sql = "DELETE FROM icid.idr_report_sites WHERE report_id = %s AND id = %s RETURNING id;"
    return run_query(sql, (report_id, site_id))


def site_pay_item_keys(property_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List the pay items a site's live, unclaimed flags and curb segments would fill once imported: what a report of the
    IDR can't then also hold by hand.
    Takes the property's uuid.
    Returns {item_no, budget_code} rows, each pair once, or None on failure.
    """
    sql = """
        SELECT DISTINCT ci.item_no, ci.budget_code
        FROM icid.pirs pir
        JOIN icid.flags f ON f.pir_id = pir.id
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
        JOIN icid.contract_items ci ON ci.contract_item_id = f.pay_item_id
        WHERE pir.property_id = %s AND pir.deleted_at IS NULL AND f.deleted_at IS NULL AND pg.deleted_at IS NULL
          AND f.claimed_by_idr_id IS NULL
        UNION
        SELECT ci.item_no, ci.budget_code
        FROM icid.pirs pir
        JOIN icid.pir_pages pg ON pg.pir_id = pir.id
        JOIN icid.segments s ON s.pir_page_id = pg.pir_page_id
        JOIN icid.contract_items ci ON ci.contract_item_id = s.pay_item_id
        WHERE pir.property_id = %s AND pir.deleted_at IS NULL AND pg.deleted_at IS NULL AND s.deleted_at IS NULL
          AND s.claimed_by_idr_id IS NULL AND s.length_ft IS NOT NULL;"""
    return run_query(sql, (property_id, property_id))


def property_work_history(property_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List every time a property was imported into a report (V2), across every IDR that still exists, deleted and
    returned ones included: the work, the IDR's number, work date, status and deleted_at, its return reason and its
    inspector's name.
    Takes the property's uuid.
    Returns the rows by work date, newest first, then the newest import; None on failure.
    """
    sql = """
        SELECT rs.id, rs.idr_id, rs.report_id, r.report_type, rs.work_type, rs.work_type_other, rs.added_at,
               i.idr_number, i.report_date, i.status, i.deleted_at, i.return_reason, i.reporter_uuid,
               NULLIF(concat_ws(' ', u.first_name, u.last_name), '') AS reporter_name
        FROM icid.idr_report_sites rs
        JOIN icid.idrs i ON i.idr_id = rs.idr_id
        JOIN icid.idr_reports r ON r.report_id = rs.report_id
        LEFT JOIN icid.users u ON u.uuid = i.reporter_uuid
        WHERE rs.property_id = %s
        ORDER BY i.report_date DESC, rs.added_at DESC, rs.id;"""
    return run_query(sql, (property_id,))
