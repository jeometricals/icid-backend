from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries._shared import placeholders, write_values

# The title block's fields, in the order their values are passed, each with its type
ANNOTATION_TYPES = {
    "contract_no": "text", "violation_no": "text", "boro": "text", "property_address": "text", "block": "text",
    "lot": "text", "cross_street_left": "text", "cross_street_right": "text", "frontage_street": "text",
    "tax_map_width_ft": "numeric", "cc_curb_to_curb_ft": "numeric", "property_status": "text",
    "corner_property_status": "text", "violation_not_removed_due_to": "text[]",
    "date_notice_issued": "date", "date_excavated": "date", "date_constructed": "date",
}
_FIELDS = tuple(ANNOTATION_TYPES)
_COLUMNS = "pir_page_id, " + ", ".join(_FIELDS) + ", tax_map_source, tax_map_fetched_at, created_at, updated_at"


def manual_tax_map_values(new_width: str) -> str:
    """
    The values a new title block's tax_map_source and tax_map_fetched_at take (Slice Y1): a width given by hand is
    'manual', fetched now; no width, no source.
    Takes the SQL expression of the width being written.
    Returns the two values, comma-separated.
    """
    return (f"CASE WHEN {new_width} IS NOT NULL THEN 'manual' END, "
            f"CASE WHEN {new_width} IS NOT NULL THEN now() END")


def manual_tax_map_sets(target: str, new_width: str) -> str:
    """
    The SET items that stamp a title block's tax-map width as typed by hand (Slice Y1): 'manual', fetched now, when the
    width written differs from the one stored (a cleared width included); otherwise the source stays as it is.
    Takes the name or alias of the row being updated and the SQL expression of the width being written.
    Returns the two SET items, comma-separated.
    """
    changed = f"{target}.tax_map_width_ft IS DISTINCT FROM {new_width}"
    return (f"tax_map_source = CASE WHEN {changed} THEN 'manual' ELSE {target}.tax_map_source END, "
            f"tax_map_fetched_at = CASE WHEN {changed} THEN now() ELSE {target}.tax_map_fetched_at END")


def get_annotations(pir_page_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a page's title block.
    Takes the page's id.
    Returns the annotation dict, or None when the page has none.
    """
    rows = run_query(f"SELECT {_COLUMNS} FROM icid.pir_annotations WHERE pir_page_id = %s;", (pir_page_id,))
    return rows[0] if rows else None


def upsert_annotations(pir_page_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Set some of a live page's title block: the fields given are written (None clears one), the others keep their values; a page without a title block gets one, the fields not given empty. A tax_map_width_ft given that changes the width is stamped manual (Slice Y1). Writes nothing for a page that doesn't exist or was replaced.
    Takes the page's id and the fields to write, keyed by name.
    Returns the annotation dict as it now is, or None when nothing was written.
    """
    updates = "".join(f"{field} = EXCLUDED.{field},\n                " for field in _FIELDS if field in data)
    if "tax_map_width_ft" in data:
        # SET reads the row as it was, so the stamp compares against the stored width
        updates += manual_tax_map_sets("pir_annotations", "EXCLUDED.tax_map_width_ft") + ",\n                "
    sql = f"""
        INSERT INTO icid.pir_annotations (pir_page_id, {", ".join(_FIELDS)}, tax_map_source, tax_map_fetched_at)
        SELECT page.pir_page_id, {", ".join(placeholders(ANNOTATION_TYPES))}, {manual_tax_map_values("%s::numeric")}
        FROM icid.pir_pages page
        WHERE page.pir_page_id = %s AND page.deleted_at IS NULL
        ON CONFLICT (pir_page_id) DO UPDATE
            SET {updates}updated_at = now()
        RETURNING {_COLUMNS};
    """
    values = write_values(ANNOTATION_TYPES, {**data, "violation_not_removed_due_to": data.get("violation_not_removed_due_to") or []})
    width = data.get("tax_map_width_ft")
    rows = run_query(sql, (*values, width, width, pir_page_id))
    return rows[0] if rows else None
