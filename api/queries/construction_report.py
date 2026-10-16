"""
SQL for the Construction Report (R1h): one property's as-built sheet for one approved IDR, DOT form 5/95.

A report covers the flags the IDR claimed on the property, whatever has happened to them since (a claimed flag keeps
its claim through a soft delete, and so does the report). The title block is page 1's of the property's live PIR;
the features and segments are the live ones on the pages the flags sit on.
"""

from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.queries.flags import FLAG_QUANTITY, QUANTITY_LATERALS
from api.queries.properties import PROPERTY_ORDER

# The title block fields the form prints, read off page 1 of the live PIR (tb_ so they don't meet the property's)
_TITLE_BLOCK_FIELDS = (
    "contract_no", "violation_no", "cross_street_left", "cross_street_right", "frontage_street", "tax_map_width_ft",
    "cc_curb_to_curb_ft", "property_status", "corner_property_status", "violation_not_removed_due_to",
    "date_notice_issued", "date_excavated", "date_constructed",
)
_TITLE_BLOCK_COLUMNS = ",\n            ".join(f"a.{field} AS tb_{field}" for field in _TITLE_BLOCK_FIELDS)
# An approved IDR that isn't deleted and claimed at least one flag on the property (p)
_COVERS_PROPERTY = """i.status = 'approved' AND i.deleted_at IS NULL AND EXISTS (
                SELECT 1 FROM icid.flags f JOIN icid.pirs pir ON pir.id = f.pir_id
                WHERE pir.property_id = %s AND f.claimed_by_idr_id = i.idr_id)"""


def find_report_idr(property_id: UUID, idr_id: Optional[UUID] = None) -> Optional[list[dict[str, Any]]]:
    """
    Find the IDR a property's Construction Report is of: the given one, or by default the most recently approved one, provided it is approved, isn't deleted and claimed a flag on the property.
    Takes the property's id and, optionally, the IDR's.
    Returns [{idr_id}] (empty when no IDR fits), or None on failure.
    """
    sql = f"""
        SELECT i.idr_id
        FROM icid.idrs i
        WHERE (%s::uuid IS NULL OR i.idr_id = %s::uuid) AND {_COVERS_PROPERTY}
        ORDER BY i.re_signed_at DESC NULLS LAST, i.updated_at DESC
        LIMIT 1;
    """
    return run_query(sql, (idr_id, idr_id, property_id))


def get_report_header(property_id: UUID, idr_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch what a Construction Report prints around its sketch: the property, its project's name and DOT rates, its live PIR (with who digitized it and its north bearing), page 1's title block, and the IDR (its number, signature copy and dates, its inspector and its RE).
    Takes the property's id and the IDR's.
    Returns the row (title block fields as tb_<field>, NULL without a PIR or a title block), or None when the property or the IDR doesn't exist.
    """
    sql = f"""
        SELECT
            p.id AS property_id, p.project_id, p.kind::text AS property_kind, p.boro::text AS boro, p.block, p.lot,
            p.address, p.intersection_street_1, p.intersection_street_2, p.quadrant::text AS quadrant,
            p.facing_street::text AS facing_street,
            pr.project_name, pr.cost_per_sf_4in, pr.cost_per_sf_7in,
            pir.id AS pir_id, pir.inspector_initials, pir.inspector_sign_date, pir.digitized_at,
            pir.north_bearing::text AS north_bearing,
            dig.first_name AS digitizer_first_name, dig.last_name AS digitizer_last_name,
            i.idr_id, i.idr_number, i.report_date, i.inspector_signature_path, i.re_signed_at,
            rep.first_name AS reporter_first_name, rep.last_name AS reporter_last_name,
            re.first_name AS re_first_name, re.last_name AS re_last_name,
            {_TITLE_BLOCK_COLUMNS}
        FROM icid.properties p
        JOIN icid.projects pr ON pr.project_id = p.project_id
        JOIN icid.idrs i ON i.idr_id = %s
        LEFT JOIN icid.pirs pir ON pir.property_id = p.id AND pir.deleted_at IS NULL
        LEFT JOIN icid.users dig ON dig.uuid = pir.digitized_by
        LEFT JOIN icid.users rep ON rep.uuid = i.reporter_uuid
        LEFT JOIN icid.users re ON re.uuid = i.re_reviewer_uuid
        LEFT JOIN icid.pir_pages pg1 ON pg1.pir_id = pir.id AND pg1.page_number = 1 AND pg1.deleted_at IS NULL
        LEFT JOIN icid.pir_annotations a ON a.pir_page_id = pg1.pir_page_id
        WHERE p.id = %s;
    """
    rows = run_query(sql, (idr_id, property_id))
    return rows[0] if rows else None


def list_report_flags(property_id: UUID, idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch the flags an IDR claimed on a property, as the report draws and measures them: shape and place, their page's scale and rotation, thickness, whether surveyed assessable, gross area (computed_quantity) and quantity (FLAG_QUANTITY: the override, or else the net area), and their non-assessable areas (live, or deleted with the flag).
    Takes the property's id and the IDR's.
    Returns the flag dicts (areas a list of {display_order, shape, dimensions, sketch_x, sketch_y, rotation, computed_quantity}) by drawing order; [] when the IDR claimed none there; None on failure.
    """
    sql = f"""
        SELECT
            f.id, f.pir_page_id, pg.page_number, pg.scale_px_per_ft, pg.rotation_deg, f.display_order, f.shape,
            f.dimensions, f.sketch_x, f.sketch_y, f.rotation, f.surveyed_thickness, f.surveyed_is_assessable,
            f.computed_quantity, {FLAG_QUANTITY} AS quantity,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'display_order', na.display_order, 'shape', na.shape, 'dimensions', na.dimensions,
                    'sketch_x', na.sketch_x, 'sketch_y', na.sketch_y, 'rotation', na.rotation,
                    'computed_quantity', na.computed_quantity) ORDER BY na.display_order)
                FROM icid.non_assessable_areas na
                WHERE na.flag_id = f.id AND (na.deleted_at IS NULL OR na.deleted_at = f.deleted_at)
            ), '[]'::jsonb) AS areas
        FROM icid.flags f
        JOIN icid.pirs pir ON pir.id = f.pir_id
        LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id{QUANTITY_LATERALS}
        WHERE pir.property_id = %s AND f.claimed_by_idr_id = %s
        ORDER BY f.display_order, f.created_at;
    """
    return run_query(sql, (property_id, idr_id))


def list_page_drawings(page_ids: list[UUID]) -> Optional[dict[str, Any]]:
    """
    Fetch the live features and segments on some PIR pages, for the report's sketch.
    Takes the pages' ids.
    Returns {features: [{feature_type, area_impact, sketch_x, sketch_y, rotation, shape, dimensions, label}], segments: [{segment_type, points}]}, each in drawing order; None on failure.
    """
    sql = """
        SELECT
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'feature_type', ft.feature_type, 'area_impact', ft.area_impact, 'sketch_x', ft.sketch_x,
                    'sketch_y', ft.sketch_y, 'rotation', ft.rotation, 'shape', ft.shape, 'dimensions', ft.dimensions,
                    'label', ft.label) ORDER BY ft.created_at)
                FROM icid.features ft
                WHERE ft.pir_page_id = ANY(%s::uuid[]) AND ft.deleted_at IS NULL
            ), '[]'::jsonb) AS features,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object('segment_type', sg.segment_type, 'points', sg.points)
                                 ORDER BY sg.created_at)
                FROM icid.segments sg
                WHERE sg.pir_page_id = ANY(%s::uuid[]) AND sg.deleted_at IS NULL
            ), '[]'::jsonb) AS segments;
    """
    rows = run_query(sql, (page_ids, page_ids))
    return rows[0] if rows else None


def list_idr_report_properties(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List the properties an approved IDR claimed flags on, one Construction Report each.
    Takes the IDR's id.
    Returns [{id, kind, address, intersection_street_1, intersection_street_2, quadrant, facing_street}] ordered as a project's properties are; [] for an IDR that isn't approved, is deleted or claimed none; None on failure.
    """
    sql = f"""
        SELECT p.id, p.kind::text AS kind, p.address, p.intersection_street_1, p.intersection_street_2,
               p.quadrant::text AS quadrant, p.facing_street::text AS facing_street
        FROM icid.properties p
        WHERE p.id IN (
            SELECT pir.property_id
            FROM icid.flags f
            JOIN icid.pirs pir ON pir.id = f.pir_id
            JOIN icid.idrs i ON i.idr_id = f.claimed_by_idr_id
            WHERE f.claimed_by_idr_id = %s AND i.status = 'approved' AND i.deleted_at IS NULL
        )
        ORDER BY {PROPERTY_ORDER};
    """
    return run_query(sql, (idr_id,))
