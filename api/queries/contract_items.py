from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# A contract item as the pay-item picker reads it: the row's own item number, description and section,
# and its unit as pay_unit.
CONTRACT_ITEM_SELECT = """
    SELECT
        ci.contract_item_id,
        ci.project_id,
        ci.spec_item_id,
        ci.budget_code,
        ci.bid_quantity,
        ci.bid_unit_price,
        ci.item_no,
        ci.description,
        ci.spec_section,
        ci.unit AS pay_unit,
        ci.created_at,
        ci.updated_at
    FROM icid.contract_items ci
"""

# A contract item as an admin manages it: a pay item of the project
_PAY_ITEM_COLUMNS = """
            ci.contract_item_id AS pay_item_uuid,
            ci.item_no AS pay_item_ref,
            ci.description,
            ci.unit,
            ci.bid_quantity AS quantity,
            ci.bid_unit_price AS unit_price,
            ci.budget_code,
            ci.spec_section"""
_PAY_ITEM_SELECT = "\n        SELECT" + _PAY_ITEM_COLUMNS + "\n        FROM icid.contract_items ci\n"


def list_contract_items_for_project(project_id: str) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a project's Schedule of Bid Items.
    Takes the project id.
    Returns the rows ordered by item_no then budget_code (empty when none), or None on failure.
    """
    sql = f"""
        {CONTRACT_ITEM_SELECT}
        WHERE ci.project_id = %s
        ORDER BY ci.item_no, ci.budget_code;
    """
    return run_query(sql, (project_id,))


def get_contract_item(contract_item_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single contract item.
    Takes the contract item uuid.
    Returns the row, or None if no contract item matches.
    """
    sql = f"""
        {CONTRACT_ITEM_SELECT}
        WHERE ci.contract_item_id = %s;
    """
    rows = run_query(sql, (contract_item_id,))
    return rows[0] if rows else None


def list_project_pay_items(project_id: str) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every pay item of a project, as an admin manages them.
    Takes the project id.
    Returns the pay item dicts ordered by budget code then item number (empty when none), or None on failure.
    """
    sql = _PAY_ITEM_SELECT + "        WHERE ci.project_id = %s\n        ORDER BY ci.budget_code, ci.item_no;"
    return run_query(sql, (project_id,))


def get_project_pay_item(project_id: str, pay_item_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one pay item of a project.
    Takes the project id and the pay item's uuid.
    Returns the pay item dict (as list_project_pay_items returns it), or None if the project has no such item.
    """
    sql = _PAY_ITEM_SELECT + "        WHERE ci.project_id = %s AND ci.contract_item_id = %s;"
    rows = run_query(sql, (project_id, pay_item_uuid))
    return rows[0] if rows else None


def get_project_pay_item_by_key(project_id: str, item_no: str, budget_code: str) -> Optional[dict[str, Any]]:
    """
    Fetch the pay item a project has under an item number and budget code, matched exactly.
    Takes the project id, the item number and the budget code.
    Returns the pay item dict (as list_project_pay_items returns it), or None if there is none.
    """
    sql = _PAY_ITEM_SELECT + "        WHERE ci.project_id = %s AND ci.item_no = %s AND ci.budget_code = %s;"
    rows = run_query(sql, (project_id, item_no, budget_code))
    return rows[0] if rows else None


def create_project_pay_item(
    project_id: str, item_no: str, description: str, unit: str, quantity: Decimal, unit_price: Decimal,
    budget_code: str, spec_section: Optional[str],
) -> Optional[dict[str, Any]]:
    """
    Add a pay item to a project. Does nothing when the project already has that item number under that budget code.
    Takes the project id and the item's number, description, unit, bid quantity, bid unit price, budget code and spec section (may be None).
    Returns the new pay item dict (as list_project_pay_items returns it), or None when the item number and budget code are taken.
    """
    sql = (
        """
        INSERT INTO icid.contract_items AS ci
            (project_id, item_no, description, unit, bid_quantity, bid_unit_price, budget_code, spec_section)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (project_id, item_no, budget_code) DO NOTHING
        RETURNING"""
        + _PAY_ITEM_COLUMNS
        + ";"
    )
    rows = run_query(sql, (project_id, item_no, description, unit, quantity, unit_price, budget_code, spec_section))
    return rows[0] if rows else None


def update_project_pay_item(project_id: str, pay_item_uuid: UUID, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Change some of a project's pay item; a column left out keeps its value. Changes nothing when another item of the project already has the item number and budget code the row would end up with.
    Takes the project id, the pay item's uuid and the new values keyed by column (item_no, description, unit, bid_quantity, bid_unit_price, budget_code, spec_section; a spec_section of None clears it).
    Returns the updated pay item dict (as list_project_pay_items returns it), or None when the project has no such item or the change would collide.
    """
    sql = (
        """
        UPDATE icid.contract_items ci
        SET item_no = COALESCE(%s, ci.item_no),
            description = COALESCE(%s, ci.description),
            unit = COALESCE(%s, ci.unit),
            bid_quantity = COALESCE(%s, ci.bid_quantity),
            bid_unit_price = COALESCE(%s, ci.bid_unit_price),
            budget_code = COALESCE(%s, ci.budget_code),
            spec_section = CASE WHEN %s THEN %s ELSE ci.spec_section END,
            updated_at = now()
        WHERE ci.project_id = %s
          AND ci.contract_item_id = %s
          AND NOT EXISTS (
              SELECT 1
              FROM icid.contract_items other
              WHERE other.project_id = ci.project_id
                AND other.item_no = COALESCE(%s, ci.item_no)
                AND other.budget_code = COALESCE(%s, ci.budget_code)
                AND other.contract_item_id <> ci.contract_item_id
          )
        RETURNING"""
        + _PAY_ITEM_COLUMNS
        + ";"
    )
    params = (
        fields.get("item_no"), fields.get("description"), fields.get("unit"), fields.get("bid_quantity"),
        fields.get("bid_unit_price"), fields.get("budget_code"), "spec_section" in fields, fields.get("spec_section"),
        project_id, pay_item_uuid, fields.get("item_no"), fields.get("budget_code"),
    )
    rows = run_query(sql, params)
    return rows[0] if rows else None


def delete_project_pay_item(project_id: str, pay_item_uuid: UUID) -> bool:
    """
    Delete one pay item of a project. Reports and quantities keep their own copies of it.
    Takes the project id and the pay item's uuid.
    Returns True when it was deleted, False when the project has no such item.
    """
    sql = """
        DELETE FROM icid.contract_items ci
        WHERE ci.project_id = %s AND ci.contract_item_id = %s
        RETURNING ci.contract_item_id;
    """
    return bool(run_query(sql, (project_id, pay_item_uuid)))


def clear_project_pay_items(project_id: str) -> Optional[int]:
    """
    Delete every pay item of a project. Reports and quantities keep their own copies of them.
    Takes the project id.
    Returns how many were deleted (0 when the project had none), or None if the statement returned nothing.
    """
    sql = """
        WITH gone AS (
            DELETE FROM icid.contract_items ci
            WHERE ci.project_id = %s
            RETURNING ci.contract_item_id
        )
        SELECT count(*) AS deleted_count FROM gone;
    """
    rows = run_query(sql, (project_id,))
    return rows[0]["deleted_count"] if rows else None
