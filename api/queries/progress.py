"""
SQL for the progress rollup (R1g): a project's flags, summed per property, pay item and state.

A flag is approved once an IDR has claimed it (whatever has happened to it since), in flight while an open IDR has it
attributed (IN_FLIGHT_FLAG_IDS, R1f's scope rule), and unclaimed otherwise; one that comes to nothing in a pay item's
unit (no pay item, or a unit a flag can't measure) is unmapped, and counted only while it is live.
"""

from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries.flags import FLAG_AMOUNT, QUANTITY_LATERALS
from api.queries.idr_flags import IN_FLIGHT_FLAG_IDS, LIVE_FLAG


def list_flag_progress(project_id: str, property_ids: Optional[list[UUID]]) -> Optional[list[dict[str, Any]]]:
    """
    Sum a project's flags per property, pay item and state (approved, in_flight, unclaimed, unmapped): the claimed ones whatever their state, the others only while live.
    Takes the project id and the properties to read (None: all of the project's; an empty list reads none).
    Returns rows (property_id, the property's kind and address fields, pay_item_id (None when unmapped), state, flag_count, quantity: the unrounded sum in the pay item's unit, 0 when unmapped); None on failure.
    """
    sql = f"""
        WITH in_flight AS ({IN_FLIGHT_FLAG_IDS}
        ),
        scoped AS (
            SELECT p.id AS property_id, f.pay_item_id, ({LIVE_FLAG}) AS live,
                   CASE
                       WHEN f.claimed_by_idr_id IS NOT NULL THEN 'approved'
                       WHEN f.id IN (SELECT id FROM in_flight) THEN 'in_flight'
                       ELSE 'unclaimed'
                   END AS state,
                   CASE WHEN ci.contract_item_id IS NULL THEN NULL ELSE {FLAG_AMOUNT} END AS amount
            FROM icid.flags f
            JOIN icid.pirs pir ON pir.id = f.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
            LEFT JOIN icid.contract_items ci ON ci.contract_item_id = f.pay_item_id{QUANTITY_LATERALS}
            WHERE p.project_id = %s AND (%s::uuid[] IS NULL OR p.id = ANY(%s::uuid[]))
              AND (f.claimed_by_idr_id IS NOT NULL OR {LIVE_FLAG})
        )
        SELECT s.property_id, p.kind::text AS property_kind, p.address, p.intersection_street_1,
               p.intersection_street_2, p.quadrant::text AS quadrant, p.facing_street::text AS facing_street,
               CASE WHEN s.amount IS NULL THEN NULL ELSE s.pay_item_id END AS pay_item_id,
               CASE WHEN s.amount IS NULL THEN 'unmapped' ELSE s.state END AS state,
               count(*)::int AS flag_count, COALESCE(sum(s.amount), 0) AS quantity
        FROM scoped s
        JOIN icid.properties p ON p.id = s.property_id
        WHERE s.amount IS NOT NULL OR s.live
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9;
    """
    return run_query(sql, (project_id, project_id, property_ids, property_ids))
