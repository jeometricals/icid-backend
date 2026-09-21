from typing import Any, Optional

from api.db.runner import run_query


def get_schema_table_names() -> Optional[list[dict[str, Any]]]:
    """
    Fetch the names of every table in the icid schema.
    Takes no arguments.
    Returns a list of dicts with a table_name key, ordered by name.
    """
    sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'icid'
        ORDER BY table_name;
    """
    return run_query(sql)


def get_table_columns(table_name: str) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the column definitions of one table in the icid schema.
    Takes the table name.
    Returns a list of dicts with column_name and data_type keys, in ordinal order.
    """
    sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'icid' AND table_name = %s
        ORDER BY ordinal_position;
    """
    return run_query(sql, (table_name,))
