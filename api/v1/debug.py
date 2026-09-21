from typing import Any

from fastapi import APIRouter

from api.queries.debug import get_schema_table_names, get_table_columns

router = APIRouter(prefix="/debug", tags=["Debug"])


@router.get("/schema")
def get_schema() -> dict[str, Any]:
    """
    List every table and its columns in the icid schema.
    Takes no arguments.
    Returns the schema name and a mapping of table name to column definitions.
    Dev-only endpoint — remove before production.
    """
    tables = get_schema_table_names() or []

    result: dict[str, list[dict[str, str]]] = {}
    for table in tables:
        table_name = table["table_name"]
        columns = get_table_columns(table_name) or []
        result[table_name] = [
            {"column": column["column_name"], "type": column["data_type"]}
            for column in columns
        ]

    return {"schema": "icid", "tables": result}
