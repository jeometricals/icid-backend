from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query

IDR_REPORT_COLUMNS = """
    report_id,
    idr_id,
    report_type,
    is_addendum,
    parent_report_id,
    page_number,
    report_data,
    is_auto_generated,
    created_at,
    updated_at
"""


def _report_data_with_ids(key: str, null_is_missing: bool = False) -> str:
    """
    Write the expression for a report's report_data with an "id" on every entry of one of its lists that has none.
    Takes the list's key in report_data ("payItems", "trucks" or "cylinders"; never anything a caller sent) and whether an entry whose "id" is null counts as having none.
    Returns the SQL, in which r is the idr_reports row; a report without the list, or with an empty one, keeps its report_data as it is.
    """
    without_id = "(e.item->>'id') IS NULL" if null_is_missing else "NOT (e.item ? 'id')"
    return f"""
                CASE WHEN jsonb_typeof(r.report_data->'{key}') = 'array'
                          AND jsonb_array_length(r.report_data->'{key}') > 0
                     THEN jsonb_set(r.report_data, '{{{key}}}', (
                              SELECT jsonb_agg(
                                  CASE WHEN jsonb_typeof(e.item) = 'object' AND {without_id}
                                       THEN e.item || jsonb_build_object('id', gen_random_uuid()::text)
                                       ELSE e.item END
                                  ORDER BY e.ord)
                              FROM jsonb_array_elements(r.report_data->'{key}') WITH ORDINALITY AS e(item, ord)
                          ))
                     ELSE r.report_data END"""


# A report's report_data with an "id" on every pay item that has none (r is the idr_reports row). Pay items are
# entries in report_data, not rows, and a reviewer's edit has to name one; position alone doesn't survive a re-save.
# migrations/020_field_edits.sql runs the same expression over the reports that existed before it.
REPORT_DATA_WITH_PAY_ITEM_IDS = _report_data_with_ids("payItems")

# The same for a Concrete Truck & Mix Info report's trucks, so the export can find a truck a reviewer added
# (migrations/023_truck_add.sql gave the existing ones theirs).
REPORT_DATA_WITH_TRUCK_IDS = _report_data_with_ids("trucks")

# The same for a Concrete Cylinder Data report's cylinders, so a reviewer's edit can name one. A draft's cylinders are
# saved with "id": null, which counts as none.
REPORT_DATA_WITH_CYLINDER_IDS = _report_data_with_ids("cylinders", null_is_missing=True)

# What submit writes as each report's report_data: ids on its trucks for a CONC_MIX, on its cylinders for a CONC_CYL,
# on its pay items otherwise (no report holds two of the lists)
REPORT_DATA_WITH_IDS = f"""
                CASE WHEN r.report_type = 'CONC_MIX' THEN {REPORT_DATA_WITH_TRUCK_IDS}
                     WHEN r.report_type = 'CONC_CYL' THEN {REPORT_DATA_WITH_CYLINDER_IDS}
                     ELSE {REPORT_DATA_WITH_PAY_ITEM_IDS} END"""


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


def get_idr_report(idr_id: UUID, report_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single report, only if it belongs to the given IDR.
    Takes the IDR uuid and the report uuid.
    Returns the report dict, or None if no report with that id exists in that IDR.
    """
    sql = f"""
        SELECT {IDR_REPORT_COLUMNS}
        FROM icid.idr_reports
        WHERE idr_id = %s AND report_id = %s;
    """
    rows = run_query(sql, (idr_id, report_id))
    return rows[0] if rows else None


def create_idr_report(
    idr_id: UUID, report_type: str, is_addendum: bool, parent_report_id: Optional[UUID]
) -> Optional[list[dict[str, Any]]]:
    """
    Insert a new report into an IDR with empty report_data, unless it would be the IDR's second General.
    Takes the IDR uuid, the report type, the addendum flag and the parent report uuid (or None).
    Returns a one-row list with the new report, an empty list if the IDR already has a General, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.idr_reports (idr_id, report_type, is_addendum, parent_report_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (idr_id) WHERE report_type = 'GEN' AND is_addendum = false DO NOTHING
        RETURNING {IDR_REPORT_COLUMNS};
    """
    return run_query(sql, (idr_id, report_type, is_addendum, parent_report_id))


def get_general_report_id(idr_id: UUID) -> Optional[UUID]:
    """
    Look up the IDR's General (non-addendum GEN) report.
    Takes the IDR uuid.
    Returns that report's uuid, or None if the IDR has no General.
    """
    sql = """
        SELECT report_id
        FROM icid.idr_reports
        WHERE idr_id = %s AND report_type = 'GEN' AND is_addendum = false;
    """
    rows = run_query(sql, (idr_id,))
    return rows[0]["report_id"] if rows else None


def get_general_report(idr_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch the IDR's General (non-addendum GEN) report in full, including is_auto_generated.
    Takes the IDR uuid.
    Returns the General report dict, or None if the IDR has no General.
    """
    sql = f"""
        SELECT {IDR_REPORT_COLUMNS}
        FROM icid.idr_reports
        WHERE idr_id = %s AND report_type = 'GEN' AND is_addendum = false;
    """
    rows = run_query(sql, (idr_id,))
    return rows[0] if rows else None


def list_non_general_main_reports(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List an IDR's non-addendum, non-General reports in creation order, for auto-summary aggregation.
    Takes the IDR uuid.
    Returns a list of report dicts (empty when none), or None on failure.
    """
    sql = f"""
        SELECT {IDR_REPORT_COLUMNS}
        FROM icid.idr_reports
        WHERE idr_id = %s AND report_type != 'GEN' AND is_addendum = false
        ORDER BY created_at, report_id;
    """
    return run_query(sql, (idr_id,))


def create_auto_general(
    idr_id: UUID, report_data: dict[str, Any]
) -> Optional[list[dict[str, Any]]]:
    """
    Insert a backend-managed (is_auto_generated) General with the given aggregated report_data.
    Takes the IDR uuid and the report_data dict (stored as JSONB); does nothing if a General already exists.
    Returns a one-row list with the new General, an empty list if one already exists, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.idr_reports (idr_id, report_type, is_addendum, is_auto_generated, report_data)
        VALUES (%s, 'GEN', false, true, %s)
        ON CONFLICT (idr_id) WHERE report_type = 'GEN' AND is_addendum = false DO NOTHING
        RETURNING {IDR_REPORT_COLUMNS};
    """
    return run_query(sql, (idr_id, Jsonb(report_data)))


def update_auto_general(
    idr_id: UUID, report_data: dict[str, Any]
) -> Optional[list[dict[str, Any]]]:
    """
    Replace the report_data of the IDR's auto-generated General and stamp its updated_at.
    Takes the IDR uuid and the aggregated report_data dict (stored as JSONB); only an is_auto_generated General is touched.
    Returns a one-row list with the updated General, an empty list if there is no auto-General, or None on failure.
    """
    sql = f"""
        UPDATE icid.idr_reports
        SET report_data = %s, updated_at = now()
        WHERE idr_id = %s AND report_type = 'GEN' AND is_addendum = false AND is_auto_generated = true
        RETURNING {IDR_REPORT_COLUMNS};
    """
    return run_query(sql, (Jsonb(report_data), idr_id))


def delete_auto_general(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Delete the IDR's auto-generated General.
    Takes the IDR uuid; only an is_auto_generated General is removed (an inspector's General is left alone).
    Returns a one-row list with the deleted report_id, an empty list if there is no auto-General, or None on failure.
    """
    sql = """
        DELETE FROM icid.idr_reports
        WHERE idr_id = %s AND report_type = 'GEN' AND is_addendum = false AND is_auto_generated = true
        RETURNING report_id;
    """
    return run_query(sql, (idr_id,))


def save_report_data(
    idr_id: UUID, report_id: UUID, report_data: dict[str, Any]
) -> Optional[list[dict[str, Any]]]:
    """
    Replace a report's report_data and stamp updated_at on both the report and its IDR, in one statement.
    Takes the IDR uuid, the report uuid and the data dict (stored as JSONB); only a report in that IDR, while the IDR is a draft, is changed.
    Returns a one-row list with the saved report, an empty list if no such report in a draft IDR, or None on failure.
    """
    sql = f"""
        WITH saved AS (
            UPDATE icid.idr_reports r
            SET report_data = %s, updated_at = now()
            FROM icid.idrs i
            WHERE r.idr_id = %s AND r.report_id = %s
                AND i.idr_id = r.idr_id AND i.status = 'draft'
            RETURNING r.*
        ),
        touched AS (
            UPDATE icid.idrs
            SET updated_at = now()
            WHERE idr_id IN (SELECT idr_id FROM saved)
        )
        SELECT {IDR_REPORT_COLUMNS}
        FROM saved;
    """
    return run_query(sql, (Jsonb(report_data), idr_id, report_id))


def delete_idr_report(idr_id: UUID, report_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Delete a report (its addendums go with it via ON DELETE CASCADE) and stamp its IDR's updated_at, in one statement.
    Takes the IDR uuid and the report uuid; only a report in that IDR, while the IDR is a draft, is deleted.
    Returns a one-row list with the deleted report's id, type, is_addendum and is_auto_generated (for auto-summary classification), an empty list if no such report in a draft IDR, or None on failure.
    """
    sql = """
        WITH deleted AS (
            DELETE FROM icid.idr_reports r
            USING icid.idrs i
            WHERE r.idr_id = %s AND r.report_id = %s
                AND i.idr_id = r.idr_id AND i.status = 'draft'
            RETURNING r.report_id, r.idr_id, r.report_type, r.is_addendum, r.is_auto_generated
        ),
        touched AS (
            UPDATE icid.idrs
            SET updated_at = now()
            WHERE idr_id IN (SELECT idr_id FROM deleted)
        )
        SELECT report_id, report_type, is_addendum, is_auto_generated
        FROM deleted;
    """
    return run_query(sql, (idr_id, report_id))
