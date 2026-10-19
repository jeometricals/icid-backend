"""
SQL for the Assessment Summary (R1i): every flag a project's approved IDRs claimed, as the Construction Report
measures them, with what decides the month each counts in.

A flag counts whatever has happened to it since its claim (as on the Construction Report). Its month is its
property's Date Constructed, read off page 1 of the property's live PIR as the report prints it, or, without one, the
day the claiming IDR was approved (re_signed_at).
"""

from typing import Any, Optional

from api.db.runner import run_query
from api.queries.flags import FLAG_QUANTITY, QUANTITY_LATERALS


def list_assessment_flags(project_id: str) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every flag claimed by an approved IDR of a project that isn't deleted, for the Assessment Summary.
    Takes the project id.
    Returns rows (idr_id, re_signed_at, property_id and its kind, address fields and community_board, date_constructed: page 1's of its live PIR or None, surveyed_thickness, surveyed_is_assessable, computed_quantity, quantity: the override or else the net area); [] for a project with none; None on failure.
    """
    sql = f"""
        SELECT
            i.idr_id, i.re_signed_at,
            p.id AS property_id, p.kind::text AS property_kind, p.address, p.intersection_street_1,
            p.intersection_street_2, p.quadrant::text AS quadrant, p.facing_street::text AS facing_street,
            p.community_board,
            a.date_constructed,
            f.surveyed_thickness, f.surveyed_is_assessable, f.computed_quantity, {FLAG_QUANTITY} AS quantity
        FROM icid.flags f
        JOIN icid.idrs i ON i.idr_id = f.claimed_by_idr_id AND i.status = 'approved' AND i.deleted_at IS NULL
        JOIN icid.pirs claimed_pir ON claimed_pir.id = f.pir_id
        JOIN icid.properties p ON p.id = claimed_pir.property_id
        LEFT JOIN icid.pirs live ON live.property_id = p.id AND live.deleted_at IS NULL
        LEFT JOIN icid.pir_pages pg1 ON pg1.pir_id = live.id AND pg1.page_number = 1 AND pg1.deleted_at IS NULL
        LEFT JOIN icid.pir_annotations a ON a.pir_page_id = pg1.pir_page_id{QUANTITY_LATERALS}
        WHERE i.project_id = %s;
    """
    return run_query(sql, (project_id,))
