"""
SQL pieces the queries of a PIR's drawings share (flags, non-assessable areas, features, segments): writing typed
values, checking a pay item's project, and reaching a page's PIR and property.
Not a table area itself.
"""

from typing import Any

from psycopg.types.json import Jsonb


def pay_item_in_project(pay_item: str, project: str) -> str:
    """
    The SQL check that a pay item, if any, is one of a project's.
    Takes the SQL expressions for the pay item's id and the project's id.
    Returns the SQL condition.
    """
    return f"""({pay_item} IS NULL OR EXISTS (
              SELECT 1 FROM icid.contract_items ci
              WHERE ci.contract_item_id = {pay_item} AND ci.project_id = {project}
          ))"""


# The pay item, if any, must be one of the project's: p is the property the row is under. Takes the pay item's id twice.
PAY_ITEM_OF_PROJECT = pay_item_in_project("%s::uuid", "p.project_id")

# What a row drawn on a page carries about its parents, read through PAGE_PARENTS
PARENT_COLUMNS = """
            pg.pir_id, pir.property_id, p.project_id,
            pg.deleted_at AS page_deleted_at, pir.deleted_at AS pir_deleted_at, p.deleted_at AS property_deleted_at"""


def page_parents(alias: str) -> str:
    """
    The joins from a row drawn on a page to its page (pg), PIR (pir) and property (p).
    Takes the alias of the row source, which has a pir_page_id.
    Returns the JOIN clauses.
    """
    return f"""
        JOIN icid.pir_pages pg ON pg.pir_page_id = {alias}.pir_page_id
        JOIN icid.pirs pir ON pir.id = pg.pir_id
        JOIN icid.properties p ON p.id = pir.property_id"""


def placeholders(types: dict[str, str]) -> list[str]:
    """
    One typed placeholder per writable column, so a NULL parameter is typed, not text.
    Takes the columns and their SQL types, in order.
    Returns the placeholders, in the same order.
    """
    return [f"%s::{sql_type}" for sql_type in types.values()]


def write_values(types: dict[str, str], data: dict[str, Any]) -> list[Any]:
    """
    Order a row's writable values as its columns are named, a JSONB column's value wrapped as JSON.
    Takes the columns and their SQL types, in order, and the values keyed by column (a missing one is None).
    Returns the list of parameters.
    """
    return [
        Jsonb(data[column]) if sql_type == "jsonb" and data.get(column) is not None else data.get(column)
        for column, sql_type in types.items()
    ]
