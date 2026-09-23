from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query


def upsert_completed_form(
    report_id: UUID, form_template_id: str, form_data: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """
    Create the report's completed form for a template, or replace its form_data if it exists.
    Takes the report uuid, the form template id and the form data as a dict (stored as JSONB).
    Returns a dict with completed_form_id and updated_at, or None on failure.
    """
    sql = """
        INSERT INTO icid.completed_forms (report_id, form_template_id, form_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (report_id, form_template_id)
        DO UPDATE SET form_data = EXCLUDED.form_data, updated_at = now()
        RETURNING completed_form_id, updated_at;
    """
    rows = run_query(sql, (report_id, form_template_id, Jsonb(form_data)))
    return rows[0] if rows else None


def get_completed_form(report_id: UUID, form_template_id: str) -> Optional[dict[str, Any]]:
    """
    Fetch the report's completed form for a template.
    Takes the report uuid and the form template id.
    Returns a dict with completed_form_id, form_data (a dict) and updated_at, or None if none exists.
    """
    sql = """
        SELECT completed_form_id, form_data, updated_at
        FROM icid.completed_forms
        WHERE report_id = %s AND form_template_id = %s;
    """
    rows = run_query(sql, (report_id, form_template_id))
    return rows[0] if rows else None
