"""
SQL for a PIR's title block as the Construction Report prints it (R1h.2): the PIR's north bearing, and page 1's
contract number, three dates, the streets round the sketch and its two widths.

A save writes only the fields sent, and only where they change: the PIR's row and page 1's title block in one
statement (data-modifying CTEs), so it is never half-applied, and a save that changes nothing writes nothing.
"""

from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# The title block's fields on page 1's pir_annotations row, and their types
BLOCK_FIELDS = {"contract_no": "text", "date_notice_issued": "date", "date_excavated": "date",
                "date_constructed": "date", "cross_street_left": "text", "cross_street_right": "text",
                "frontage_street": "text", "tax_map_width_ft": "numeric", "cc_curb_to_curb_ft": "numeric"}


def get_title_block(pir_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a PIR's title block: its north bearing, its live page 1 and that page's contract number, dates, streets and widths.
    Takes the PIR's id.
    Returns {pir_id, pir_page_id (None without a live page 1), north_bearing, contract_no, date_notice_issued, date_excavated, date_constructed, cross_street_left, cross_street_right, frontage_street, tax_map_width_ft, cc_curb_to_curb_ft}, the page's fields None when it has no title block; None for an unknown PIR.
    """
    sql = """
        SELECT pir.id AS pir_id, pg.pir_page_id, pir.north_bearing::text AS north_bearing,
               a.contract_no, a.date_notice_issued, a.date_excavated, a.date_constructed,
               a.cross_street_left, a.cross_street_right, a.frontage_street, a.tax_map_width_ft, a.cc_curb_to_curb_ft
        FROM icid.pirs pir
        LEFT JOIN icid.pir_pages pg ON pg.pir_id = pir.id AND pg.page_number = 1 AND pg.deleted_at IS NULL
        LEFT JOIN icid.pir_annotations a ON a.pir_page_id = pg.pir_page_id
        WHERE pir.id = %s;
    """
    rows = run_query(sql, (pir_id,))
    return rows[0] if rows else None


def update_title_block(pir_id: UUID, pir_page_id: UUID, fields: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """
    Write the title-block fields sent: north_bearing on the PIR, the rest on its page 1's title block (made when the page has none and a field sent has a value). A field equal to what is stored writes nothing, nor bumps updated_at.
    Takes the PIR's id, its live page 1's id and the fields sent, keyed by name (None clears one; a field left out is unchanged).
    Returns one row {bearing_written, block_written} (each the number of rows written: 0 or 1), or None on failure.
    """
    params: list[Any] = []
    with_parts = []

    if "north_bearing" in fields:
        with_parts.append("""
        bearing AS (
            UPDATE icid.pirs pir
            SET north_bearing = %s::icid.compass_point, updated_at = now()
            WHERE pir.id = %s AND pir.north_bearing IS DISTINCT FROM %s::icid.compass_point
            RETURNING pir.id
        )""")
        params += [fields["north_bearing"], pir_id, fields["north_bearing"]]

    block = {name: fields[name] for name in BLOCK_FIELDS if name in fields}
    if block:
        names = list(block)
        values = ", ".join(f"%s::{BLOCK_FIELDS[name]}" for name in names)
        sets = ", ".join(f"{name} = EXCLUDED.{name}" for name in names)
        changed = " OR ".join(f"a.{name} IS DISTINCT FROM EXCLUDED.{name}" for name in names)
        any_value = " OR ".join(f"%s::{BLOCK_FIELDS[name]} IS NOT NULL" for name in names)
        with_parts.append(f"""
        block AS (
            INSERT INTO icid.pir_annotations AS a (pir_page_id, {", ".join(names)})
            SELECT pg.pir_page_id, {values}
            FROM icid.pir_pages pg
            WHERE pg.pir_page_id = %s AND pg.pir_id = %s AND pg.deleted_at IS NULL
              AND (({any_value}) OR EXISTS (SELECT 1 FROM icid.pir_annotations o WHERE o.pir_page_id = pg.pir_page_id))
            ON CONFLICT (pir_page_id) DO UPDATE SET {sets}, updated_at = now()
            WHERE {changed}
            RETURNING a.pir_page_id
        )""")
        params += [*block.values(), pir_page_id, pir_id, *block.values()]

    sql = (
        "\n        WITH" + ",".join(with_parts) + "\n        SELECT "
        + ("(SELECT count(*) FROM bearing)" if "north_bearing" in fields else "0") + " AS bearing_written, "
        + ("(SELECT count(*) FROM block)" if block else "0") + " AS block_written;"
    )
    return run_query(sql, tuple(params))
