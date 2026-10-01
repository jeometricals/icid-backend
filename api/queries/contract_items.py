from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# A contract item joined to its spec item, so each row carries item_no, description,
# spec_section and pay_unit inline.
CONTRACT_ITEM_SELECT = """
    SELECT
        ci.contract_item_id,
        ci.project_id,
        ci.spec_item_id,
        ci.budget_code,
        ci.bid_quantity,
        ci.bid_unit_price,
        si.item_no,
        si.description,
        si.spec_section,
        si.pay_unit,
        ci.created_at,
        ci.updated_at
    FROM icid.contract_items ci
    JOIN icid.spec_items si ON si.spec_item_id = ci.spec_item_id
"""


def list_contract_items_for_project(project_id: str) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a project's Schedule of Bid Items, each joined to its spec item.
    Takes the project id.
    Returns the joined rows ordered by item_no then budget_code (empty when none), or None on failure.
    """
    sql = f"""
        {CONTRACT_ITEM_SELECT}
        WHERE ci.project_id = %s
        ORDER BY si.item_no, ci.budget_code;
    """
    return run_query(sql, (project_id,))


def get_contract_item(contract_item_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single contract item joined to its spec item.
    Takes the contract item uuid.
    Returns the joined row, or None if no contract item matches.
    """
    sql = f"""
        {CONTRACT_ITEM_SELECT}
        WHERE ci.contract_item_id = %s;
    """
    rows = run_query(sql, (contract_item_id,))
    return rows[0] if rows else None
