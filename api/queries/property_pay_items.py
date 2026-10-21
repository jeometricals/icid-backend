"""
SQL for a property's pay items across IDRs (Slice V5): what each IDR that isn't deleted reports on it, from its flags,
its curb segments (an approved IDR's claimed, an open IDR's attributed) and the pay items entered on it as an imported
site. The PIR workspace's "Pay Items Reported" reads it; api/services/property_pay_items.py groups it.
"""

from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries.idr_flags import PROPERTY_FLAG_LINES
from api.queries.idr_report_site_pay_items import OVERRIDE_DROPPED_SQL, SITE_UNIT
from api.queries.idr_segments import PROPERTY_SEGMENT_LINES

# The pay items entered on a property as an imported site, a row per IDR and pay item entered; deleted IDRs left out,
# and overrides an approved IDR dropped. Its one parameter the property's id.
_SITE_LINES = f"""
        SELECT rs.idr_id, 'sites' AS source, sp.id AS element_id, ci.contract_item_id, ci.item_no, ci.budget_code,
               ci.description, {SITE_UNIT} AS unit, sp.quantity AS amount
        FROM icid.idr_report_site_pay_items sp
        JOIN icid.idr_report_sites rs ON rs.id = sp.report_site_id
        JOIN icid.idrs i ON i.idr_id = rs.idr_id
        JOIN icid.contract_items ci ON ci.contract_item_id = sp.contract_item_id
        WHERE rs.property_id = %s AND i.deleted_at IS NULL AND NOT """ + OVERRIDE_DROPPED_SQL


def property_pay_item_lines(property_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List what every IDR that isn't deleted reports on a property, a row per IDR and flag, curb segment or site pay item:
    its source, element, contract item (number, budget code, description, unit) and amount, with the IDR's number, work
    date, status and inspector's name. Rows whose amount can't be given in their pay item's unit are left out.
    Takes the property's uuid.
    Returns the rows by work date, newest first; None on failure.
    """
    sql = f"""
        SELECT l.idr_id, l.source, l.element_id, l.contract_item_id, l.item_no, l.budget_code, l.description, l.unit,
               l.amount, i.idr_number, i.report_date, i.status,
               NULLIF(concat_ws(' ', u.first_name, u.last_name), '') AS reporter_name
        FROM ({PROPERTY_FLAG_LINES}
              UNION ALL{PROPERTY_SEGMENT_LINES}
              UNION ALL{_SITE_LINES}
        ) l
        JOIN icid.idrs i ON i.idr_id = l.idr_id
        LEFT JOIN icid.users u ON u.uuid = i.reporter_uuid
        WHERE l.amount IS NOT NULL
        ORDER BY i.report_date DESC, i.idr_number NULLS LAST, l.item_no, l.budget_code;"""
    return run_query(sql, (property_id, property_id, property_id))
