from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

IDR_REPORT_COLUMNS = """
    report_id,
    idr_id,
    report_type,
    is_addendum,
    parent_report_id,
    page_number,
    report_data,
    created_at,
    updated_at
"""


def list_reports_for_idr(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List every report in an IDR, in page order once submitted and creation order before.
    Takes the IDR uuid.
    Returns a list of report dicts (report_data as a dict), empty if the IDR has none, or None on failure.
    """
    sql = f"""
        SELECT {IDR_REPORT_COLUMNS}
        FROM icid.idr_reports
        WHERE idr_id = %s
        ORDER BY page_number NULLS LAST, created_at;
    """
    return run_query(sql, (idr_id,))
