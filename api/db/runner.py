from typing import Any, Optional, Sequence

from psycopg.rows import dict_row

from api.db.connection import get_connection


def run_query(sql: str, params: Optional[Sequence[Any]] = None) -> Optional[list[dict[str, Any]]]:
    """
    Execute a SQL statement against the ICID database.
    Takes a parameterized SQL string and an optional sequence of parameters.
    Returns a list of dicts keyed by column name for SELECTs, or None for writes.
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)

            if cur.description:
                return cur.fetchall()

            conn.commit()
            return None
