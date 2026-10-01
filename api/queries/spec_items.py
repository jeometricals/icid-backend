from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

SPEC_ITEM_COLUMNS = """
    spec_item_id,
    item_no,
    description,
    spec_section,
    pay_unit,
    created_at,
    updated_at
"""


def list_spec_items() -> Optional[list[dict[str, Any]]]:
    """
    Fetch the whole NYCDOT spec-item catalog.
    Takes nothing.
    Returns the spec item rows ordered by item_no, or None on failure.
    """
    sql = f"""
        SELECT {SPEC_ITEM_COLUMNS}
        FROM icid.spec_items
        ORDER BY item_no;
    """
    return run_query(sql)


def get_spec_item(spec_item_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single spec item.
    Takes the spec item uuid.
    Returns the spec item row, or None if no spec item matches.
    """
    sql = f"""
        SELECT {SPEC_ITEM_COLUMNS}
        FROM icid.spec_items
        WHERE spec_item_id = %s;
    """
    rows = run_query(sql, (spec_item_id,))
    return rows[0] if rows else None
