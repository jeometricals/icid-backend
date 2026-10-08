"""
SQL for icid.quantities, the pay-item quantities of approved IDRs.

An IDR's rows change with the IDR, never on their own: there are no multi-statement transactions, so the delete and
the insert ride along in the statement that approves, unlocks or deletes it, as data-modifying CTEs. The two CTEs here
are the one source of both; replace_quantities runs them on their own, for an IDR that is already approved.
"""

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


def delete_quantities_cte(idrs: str) -> str:
    """
    Write the CTE that deletes every quantity row of the IDR a statement is working on.
    Takes the name of a CTE defined earlier in that statement (written here, never from a request) whose idr_id column holds the IDR: "moved" in a statement that moves an IDR, so nothing is deleted unless the IDR did move.
    Returns the CTE, to append to the statement's WITH list; it takes no parameters.
    """
    return f"""
        deleted_quantities AS (
            DELETE FROM icid.quantities
            WHERE idr_id IN (SELECT idr_id FROM {idrs})
        )"""


def insert_quantities_cte(idrs: str) -> str:
    """
    Write the CTE that inserts an IDR's quantity rows from a JSON array, each row taking the IDR the statement is working on.
    Takes the name of a CTE defined earlier in that statement (see delete_quantities_cte): when it holds no IDR, nothing is inserted.
    Returns the CTE, to append to the statement's WITH list; it takes one parameter, the rows (see quantity_rows_json).
    """
    return f"""
        inserted_quantities AS (
            INSERT INTO icid.quantities (
                project_id, idr_id, report_date, reporter_uuid, report_type, pay_item_ref, budget_code, description,
                amount, unit
            )
            SELECT q.project_id, i.idr_id, q.report_date, q.reporter_uuid, q.report_type, q.pay_item_ref,
                   q.budget_code, q.description, q.amount, q.unit
            FROM jsonb_to_recordset(%s) AS q({QUANTITY_FIELDS})
            CROSS JOIN {idrs} i
            RETURNING quantity_id
        )"""


def replace_quantities_ctes(idrs: str) -> str:
    """
    Write the two CTEs that replace an IDR's quantity rows: every row it has goes, and the rows handed over come in.
    Takes the name of a CTE defined earlier in the statement (see delete_quantities_cte).
    Returns the CTEs, comma-separated, to append to the statement's WITH list; they take one parameter, the rows.
    """
    return f"{delete_quantities_cte(idrs)},{insert_quantities_cte(idrs)}"


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
    Replace an IDR's quantity rows with a new set, in one statement: the rows it has are deleted and the given ones inserted. Nothing about the IDR is checked: it is for an IDR already approved (the backfill).
    Takes the IDR uuid and the new rows (see quantity_rows_json); an empty list leaves the IDR with none.
    Returns how many rows were inserted, or None on failure.
    """
    sql = f"""
        WITH given AS (
            SELECT %s::uuid AS idr_id
        ),{replace_quantities_ctes("given")}
        SELECT COUNT(*) AS inserted
        FROM inserted_quantities;
    """
    result = run_query(sql, (idr_id, quantity_rows_json(rows)))
    return result[0]["inserted"] if result else None
