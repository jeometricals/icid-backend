from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query
from api.queries.flag_measurements import REASON_RETIRE, remesh_ctes, snapshot_intent
from api.queries.segments import segment_length_sql

# A page as every function here returns it: its own columns, and its PIR's property, project and deleted states
# (what the routes check before a write). Read from a row source named pg.
_PAGE_COLUMNS = """
            pg.pir_page_id, pg.pir_id, pir.property_id, p.project_id, pg.page_number, pg.image_path, pg.image_mime,
            pg.width_px, pg.height_px, pg.scale_px_per_ft, pg.rotation_deg, pg.created_at, pg.updated_at,
            pg.deleted_at, pg.deleted_by, pir.deleted_at AS pir_deleted_at, p.deleted_at AS property_deleted_at"""
_FROM = """
        JOIN icid.pirs pir ON pir.id = pg.pir_id
        JOIN icid.properties p ON p.id = pir.property_id"""
# What goes with a PIR's pages when they are replaced or deleted, all in the same statement: the live flags on them
# (each unclaimed one in a bundle leaving it for a measurement of its own: remesh_ctes, Slice Y3b-1), those flags' live
# non-assessable areas, and the live features, segments and match lines on them. Reads old_pages (pir_page_id,
# deleted_by); takes the deleting user's uuid five times.
_RETIRED_FLAGS = "SELECT id FROM icid.flags WHERE pir_page_id IN (SELECT pir_page_id FROM old_pages) AND deleted_at IS NULL"
_RETIRE_DRAWINGS = (
    """
        intent AS (
            """
    + snapshot_intent("delete", REASON_RETIRE, _RETIRED_FLAGS)
    + """
        )"""
    + remesh_ctes("(SELECT deleted_by FROM old_pages LIMIT 1)")
    + """,
        old_flags AS (
            UPDATE icid.flags flag
            SET deleted_at = now(), deleted_by = %s, updated_at = now(), measurement_id = rf.measurement_id,
                area_share_fraction = rf.share
            FROM rm_flags rf
            WHERE flag.pir_page_id IN (SELECT pir_page_id FROM old_pages) AND flag.deleted_at IS NULL
              AND rf.flag_id = flag.id
            RETURNING flag.id
        ),
        old_areas AS (
            UPDATE icid.non_assessable_areas
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE flag_id IN (SELECT id FROM old_flags) AND deleted_at IS NULL
            RETURNING non_assessable_area_id
        ),
        old_features AS (
            UPDATE icid.features
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE pir_page_id IN (SELECT pir_page_id FROM old_pages) AND deleted_at IS NULL
            RETURNING feature_id
        ),
        old_segments AS (
            UPDATE icid.segments
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE pir_page_id IN (SELECT pir_page_id FROM old_pages) AND deleted_at IS NULL
            RETURNING segment_id
        ),
        old_match_lines AS (
            UPDATE icid.pir_match_lines
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE pir_page_id IN (SELECT pir_page_id FROM old_pages) AND deleted_at IS NULL
            RETURNING match_line_id
        )"""
)
# Makes the statement wait for every CTE of _RETIRE_DRAWINGS
_DRAWINGS_RETIRED = """
            CROSS JOIN (SELECT count(*) FROM old_flags) AS flags_retired
            CROSS JOIN (SELECT count(*) FROM old_areas) AS areas_retired
            CROSS JOIN (SELECT count(*) FROM old_features) AS features_retired
            CROSS JOIN (SELECT count(*) FROM old_segments) AS segments_retired
            CROSS JOIN (SELECT count(*) FROM old_match_lines) AS match_lines_retired"""


def list_pages_for_pir(pir_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a PIR's live pages.
    Takes the PIR's id.
    Returns the page dicts ordered by page number, or None on failure.
    """
    sql = (
        "\n        SELECT" + _PAGE_COLUMNS + "\n        FROM icid.pir_pages pg" + _FROM
        + "\n        WHERE pg.pir_id = %s AND pg.deleted_at IS NULL\n        ORDER BY pg.page_number;"
    )
    return run_query(sql, (pir_id,))


def get_page(pir_page_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one page, replaced or not, with what its PIR and property are.
    Takes the page's id.
    Returns the page dict, or None if no page matches.
    """
    sql = "\n        SELECT" + _PAGE_COLUMNS + "\n        FROM icid.pir_pages pg" + _FROM + "\n        WHERE pg.pir_page_id = %s;"
    rows = run_query(sql, (pir_page_id,))
    return rows[0] if rows else None


def replace_pages(pir_id: UUID, pages: list[dict[str, Any]], replaced_by: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Give a PIR a new set of pages in one statement: its live pages are marked replaced, with their live flags (and those flags' non-assessable areas), features, segments and match lines soft-deleted, and the new pages are inserted, numbered as given. The old pages' files and title blocks are kept, with the old pages. The new page 1 starts with a title block holding a tax-map width (Slice Y1): the replaced page 1's when it was typed by hand (source manual, a cleared one included), else the property's MapPLUTO frontage; none when there is neither.
    Takes the PIR's id, the new pages (each {page_number, image_path, image_mime, width_px, height_px, scale_px_per_ft}) and the uuid of the user replacing them.
    Returns the new page dicts ordered by page number, or None on failure.
    """
    # The INSERT reads the count of replaced pages first, so the old pages are out of the live unique index before the
    # new ones go in
    sql = (
        """
        WITH old_pages AS (
            UPDATE icid.pir_pages
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE pir_id = %s AND deleted_at IS NULL
            RETURNING pir_page_id, page_number, deleted_by
        ),"""
        + _RETIRE_DRAWINGS
        + """,
        pg AS (
            INSERT INTO icid.pir_pages (pir_id, page_number, image_path, image_mime, width_px, height_px, scale_px_per_ft)
            SELECT %s, x.page_number, x.image_path, x.image_mime, x.width_px, x.height_px, x.scale_px_per_ft
            FROM jsonb_to_recordset(%s::jsonb) AS x(page_number int, image_path text, image_mime text, width_px int,
                                                    height_px int, scale_px_per_ft numeric)
            CROSS JOIN (SELECT count(*) FROM old_pages) AS replaced"""
        + _DRAWINGS_RETIRED
        + """
            RETURNING *
        ),
        carried AS (
            SELECT a.tax_map_width_ft, a.tax_map_source, a.tax_map_fetched_at
            FROM old_pages op
            JOIN icid.pir_annotations a ON a.pir_page_id = op.pir_page_id
            WHERE op.page_number = 1 AND a.tax_map_source = 'manual'
        ),
        title_block AS (
            INSERT INTO icid.pir_annotations (pir_page_id, tax_map_width_ft, tax_map_source, tax_map_fetched_at)
            SELECT pg.pir_page_id,
                   CASE WHEN carried.tax_map_source IS NOT NULL THEN carried.tax_map_width_ft ELSE p.tax_map_frontage_ft END,
                   COALESCE(carried.tax_map_source, p.tax_map_source),
                   CASE WHEN carried.tax_map_source IS NOT NULL THEN carried.tax_map_fetched_at ELSE p.tax_map_fetched_at END
            FROM pg"""
        + _FROM
        + """
            LEFT JOIN carried ON true
            WHERE pg.page_number = 1 AND COALESCE(carried.tax_map_source, p.tax_map_source) IS NOT NULL
            ON CONFLICT (pir_page_id) DO NOTHING
            RETURNING pir_page_id
        )
        SELECT"""
        + _PAGE_COLUMNS
        + "\n        FROM pg"
        + _FROM
        + "\n        CROSS JOIN (SELECT count(*) FROM title_block) AS title_blocks_started"
        + "\n        ORDER BY pg.page_number;"
    )
    payload = Jsonb([
        {**page, "scale_px_per_ft": None if page.get("scale_px_per_ft") is None else str(page["scale_px_per_ft"])}
        for page in pages
    ])
    return run_query(sql, (replaced_by, pir_id, *(replaced_by,) * 5, pir_id, payload))


def delete_pages_for_pir(pir_id: UUID, deleted_by: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Take a PIR's pages away in one statement: its live pages are marked deleted, with their live flags (and those flags' non-assessable areas), features and segments soft-deleted. Their files are kept.
    Takes the PIR's id and the uuid of the user deleting them.
    Returns the pages deleted, as {pir_page_id, image_path} (empty when it had none), or None on failure.
    """
    sql = (
        """
        WITH old_pages AS (
            UPDATE icid.pir_pages
            SET deleted_at = now(), deleted_by = %s, updated_at = now()
            WHERE pir_id = %s AND deleted_at IS NULL
            RETURNING pir_page_id, image_path, deleted_by
        ),"""
        + _RETIRE_DRAWINGS
        + """
        SELECT pir_page_id, image_path FROM old_pages"""
        + _DRAWINGS_RETIRED
        + ";"
    )
    return run_query(sql, (deleted_by, pir_id, *(deleted_by,) * 5))


def _update_page(set_clause: str, params: tuple[Any, ...], pir_page_id: UUID) -> Optional[dict[str, Any]]:
    """
    Change one live page and return it as every function here does.
    Takes the SET clause, its parameters and the page's id.
    Returns the updated page dict, or None if no live page matches.
    """
    sql = (
        """
        WITH pg AS (
            UPDATE icid.pir_pages
            SET """
        + set_clause
        + """, updated_at = now()
            WHERE pir_page_id = %s AND deleted_at IS NULL
            RETURNING *
        )
        SELECT"""
        + _PAGE_COLUMNS
        + "\n        FROM pg"
        + _FROM
        + ";"
    )
    rows = run_query(sql, (*params, pir_page_id))
    return rows[0] if rows else None


def update_page_scale(pir_page_id: UUID, scale_px_per_ft: Optional[Decimal]) -> Optional[dict[str, Any]]:
    """
    Set a live page's scale and, in the same statement, the length of every live segment on it (NULL once the scale is cleared).
    Takes the page's id and the scale in pixels per foot (None clears it).
    Returns the updated page dict, or None if no live page matches.
    """
    sql = (
        """
        WITH pg AS (
            UPDATE icid.pir_pages
            SET scale_px_per_ft = %s::numeric, updated_at = now()
            WHERE pir_page_id = %s AND deleted_at IS NULL
            RETURNING *
        ),
        lengths AS (
            UPDATE icid.segments s
            SET length_ft = """
        + segment_length_sql("s.points", "pg.scale_px_per_ft")
        + """, updated_at = now()
            FROM pg
            WHERE s.pir_page_id = pg.pir_page_id AND s.deleted_at IS NULL
            RETURNING s.segment_id
        )
        SELECT"""
        + _PAGE_COLUMNS
        + "\n        FROM pg"
        + _FROM
        + "\n        CROSS JOIN (SELECT count(*) FROM lengths) AS lengths_set;"
    )
    rows = run_query(sql, (scale_px_per_ft, pir_page_id))
    return rows[0] if rows else None


def update_page_rotation(pir_page_id: UUID, rotation_deg: int) -> Optional[dict[str, Any]]:
    """
    Set a live page's rotation.
    Takes the page's id and the rotation (0, 90, 180 or 270 degrees).
    Returns the updated page dict, or None if no live page matches.
    """
    return _update_page("rotation_deg = %s", (rotation_deg,), pir_page_id)
