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
}
_FIELDS = tuple(ANNOTATION_TYPES)
_COLUMNS = "pir_page_id, " + ", ".join(_FIELDS) + ", created_at, updated_at"


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
    Set a live page's title block, every field at once (one left out is cleared). Writes nothing for a page that doesn't exist or was replaced.
    Takes the page's id and the fields keyed by name.
    Returns the annotation dict as it now is, or None when nothing was written.
    """
    updates = ",\n                ".join(f"{field} = EXCLUDED.{field}" for field in _FIELDS)
    sql = f"""
        INSERT INTO icid.pir_annotations (pir_page_id, {", ".join(_FIELDS)})
        SELECT page.pir_page_id, {", ".join(placeholders(ANNOTATION_TYPES))}
        FROM icid.pir_pages page
        WHERE page.pir_page_id = %s AND page.deleted_at IS NULL
        ON CONFLICT (pir_page_id) DO UPDATE
            SET {updates},
                updated_at = now()
        RETURNING {_COLUMNS};
    """
    values = write_values(ANNOTATION_TYPES, {**data, "violation_not_removed_due_to": data.get("violation_not_removed_due_to") or []})
    rows = run_query(sql, (*values, pir_page_id))
    return rows[0] if rows else None
