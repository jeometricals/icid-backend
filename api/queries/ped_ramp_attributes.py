from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# The columns a PUT writes, all of them every time
WRITTEN_COLUMNS = (
    "ramp_id_label", "is_there_ponding", "maximum_curb_reveal_at_ramp_in", "clear_space_within_crosswalk",
    "dws_length_min_24_inch", "dws_full_ramp_width", "dws_aligned_with_ramp", "dws_left_offset_from_curb_in",
    "dws_right_offset_from_curb_in", "dws_color", "dws_material", "construction", "action_taken", "comments",
    "left_flare_traversable_path", "right_flare_traversable_path", "outside_of_parallel_travel_lane",
    "ramp_running_slope_pct", "ramp_cross_slope_pct", "ramp_length_in", "ramp_width_in", "landing_running_slope_pct",
    "landing_cross_slope_pct", "landing_length_in", "landing_width_in", "left_flare_slope_pct", "right_flare_slope_pct",
    "gutter_cross_slope_pct", "gutter_flow_slope_pct", "left_sidewalk_approach_width_in",
    "left_sidewalk_running_slope_pct", "left_sidewalk_cross_slope_pct", "right_sidewalk_approach_width_in",
    "right_sidewalk_running_slope_pct", "right_sidewalk_cross_slope_pct", "bottom_landing_running_slope_pct",
    "bottom_landing_cross_slope_pct", "bottom_landing_length_in", "bottom_landing_width_in",
    "left_ramp_running_slope_pct", "left_ramp_cross_slope_pct", "left_ramp_length_in", "left_ramp_width_in",
    "right_ramp_running_slope_pct", "right_ramp_cross_slope_pct", "right_ramp_length_in", "right_ramp_width_in",
    "cut_through_length_in", "min_clearance_between_dws_in",
)
# A ramp's attributes (a), with its property's ramp type (p)
_ATTRIBUTE_COLUMNS = "a.*, p.ramp_type::text AS ramp_type"


def get_attributes(property_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a ped ramp's attributes, with its ramp type.
    Takes the property's id.
    Returns the attributes dict, or None when none have been entered.
    """
    sql = f"""
        SELECT {_ATTRIBUTE_COLUMNS}
        FROM icid.ped_ramp_attributes a
        JOIN icid.properties p ON p.id = a.property_id
        WHERE a.property_id = %s;"""
    rows = run_query(sql, (property_id,))
    return rows[0] if rows else None


def upsert_attributes(property_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Write a ped ramp's attributes whole: added the first time, replaced after. Writes nothing for a property that isn't a ped ramp.
    Takes the property's id and every written column's value keyed by column (one left out is written as None).
    Returns the attributes dict with the ramp type, or None when the property isn't a ped ramp.
    """
    columns = ", ".join(WRITTEN_COLUMNS)
    sql = f"""
        WITH a AS (
            INSERT INTO icid.ped_ramp_attributes (property_id, {columns})
            SELECT p.id, {", ".join(["%s"] * len(WRITTEN_COLUMNS))}
            FROM icid.properties p
            WHERE p.id = %s AND p.kind = 'ped_ramp'
            ON CONFLICT (property_id) DO UPDATE
            SET {", ".join(f"{column} = EXCLUDED.{column}" for column in WRITTEN_COLUMNS)}, updated_at = now()
            RETURNING *
        )
        SELECT {_ATTRIBUTE_COLUMNS}
        FROM a
        JOIN icid.properties p ON p.id = a.property_id;"""
    params = (*(data.get(column) for column in WRITTEN_COLUMNS), property_id)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def delete_attributes(property_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Remove a ped ramp's attributes.
    Takes the property's id.
    Returns the deleted rows' property ids ([] when there were none), or None on failure.
    """
    sql = "DELETE FROM icid.ped_ramp_attributes WHERE property_id = %s RETURNING property_id;"
    return run_query(sql, (property_id,))


def list_corner_ramps(corner_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a corner's live ramps, each with its attributes, for its inspection report.
    Takes the corner's id.
    Returns dicts of the ramp's property fields and its attributes (a dict, or None when none were entered), ordered by quadrant, ramp type and when added, or None on failure.
    """
    sql = """
        SELECT p.id AS property_id, p.intersection_street_1, p.intersection_street_2, p.quadrant::text AS quadrant,
               p.facing_street::text AS facing_street, p.ramp_type::text AS ramp_type, a.ramp_id_label,
               a.property_id IS NOT NULL AS has_attributes,
               CASE WHEN a.property_id IS NULL THEN NULL
                    ELSE to_jsonb(a) || jsonb_build_object('ramp_type', p.ramp_type::text) END AS attributes
        FROM icid.properties p
        LEFT JOIN icid.ped_ramp_attributes a ON a.property_id = p.id
        WHERE p.corner_id = %s AND p.deleted_at IS NULL
        ORDER BY p.quadrant, p.ramp_type, p.created_at;"""
    return run_query(sql, (corner_id,))

