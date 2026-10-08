from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query

# What one quantity row carries besides its IDR, as the statement reads it out of the JSON it is handed
QUANTITY_FIELDS = """
            project_id text,
            report_date date,
            reporter_uuid uuid,
            report_type text,
            pay_item_ref text,
            budget_code text,
            description text,
            amount numeric,
            unit text
"""

# The two steps that replace an IDR's quantities, as CTEs: every row the IDR has goes, and the rows handed over come
# in. Takes two parameters, in order: the IDR uuid and the rows as a JSON array (see quantity_rows_json). Written to be
# put in a larger statement too, so the rows can change in the same statement that approves the IDR.
REPLACE_QUANTITIES_CTES = f"""
        deleted_quantities AS (
            DELETE FROM icid.quantities
            WHERE idr_id = %s
        ),
        inserted_quantities AS (
            INSERT INTO icid.quantities (
                project_id, idr_id, report_date, reporter_uuid, report_type, pay_item_ref, budget_code, description,
                amount, unit
            )
            SELECT q.project_id, %s, q.report_date, q.reporter_uuid, q.report_type, q.pay_item_ref, q.budget_code,
                   q.description, q.amount, q.unit
            FROM jsonb_to_recordset(%s) AS q({QUANTITY_FIELDS})
            RETURNING quantity_id
        )"""


def quantity_rows_json(rows: list[dict[str, Any]]) -> Jsonb:
    """
    Ready quantity rows for the statement: as a JSON array, the date, the uuid and the amount as text.
    Takes the rows (each with project_id, report_date, reporter_uuid, report_type, pay_item_ref, budget_code,
    description, amount and unit).
    Returns the Jsonb parameter.
    """
    return Jsonb([
        {
            "project_id": row["project_id"],
            "report_date": row["report_date"].isoformat(),
            "reporter_uuid": str(row["reporter_uuid"]),
            "report_type": row["report_type"],
            "pay_item_ref": row["pay_item_ref"],
            "budget_code": row["budget_code"],
            "description": row["description"],
            "amount": str(row["amount"]),
            "unit": row["unit"],
        }
        for row in rows
    ])


def replace_quantities(idr_id: UUID, rows: list[dict[str, Any]]) -> Optional[int]:
    """
    Replace an IDR's quantity rows with a new set, in one statement: the rows it has are deleted and the given ones inserted.
    Takes the IDR uuid and the new rows (see quantity_rows_json); an empty list leaves the IDR with none.
    Returns how many rows were inserted, or None on failure.
    """
    sql = f"""
        WITH {REPLACE_QUANTITIES_CTES}
        SELECT COUNT(*) AS inserted
        FROM inserted_quantities;
    """
    result = run_query(sql, (idr_id, idr_id, quantity_rows_json(rows)))
    return result[0]["inserted"] if result else None
