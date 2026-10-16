from datetime import date
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query
from api.queries._shared import pay_item_in_project
from api.queries.features import WRITABLE_TYPES as FEATURE_TYPES
from api.queries.flags import WRITABLE_TYPES as FLAG_TYPES
from api.queries.non_assessable_areas import WRITABLE_TYPES as AREA_TYPES
from api.queries.pir_annotations import ANNOTATION_TYPES
from api.queries.segments import WRITABLE_TYPES as SEGMENT_TYPES
from api.queries.segments import segment_length_sql


def _recordset(columns: dict[str, str]) -> str:
    """
    The column list of a jsonb_to_recordset over an import's rows.
    Takes the columns and their SQL types, in order.
    Returns the "x(column type, ...)" clause.
    """
    return "x(" + ", ".join(f"{column} {sql_type}" for column, sql_type in columns.items()) + ")"


def _from_x(columns: dict[str, str]) -> str:
    """
    The recordset's columns as an INSERT's SELECT list takes them.
    Takes the columns, in order.
    Returns "x.column, ...".
    """
    return ", ".join(f"x.{column}" for column in columns)


def _json_ready(rows: list[dict[str, Any]]) -> Jsonb:
    """
    Wrap an import's rows as one JSON parameter, decimals, uuids and dates as text (so a NUMERIC column gets every digit, and a date is ISO).
    Takes the rows keyed by column.
    Returns the Jsonb parameter.
    """
    def ready(value: Any) -> Any:
        return str(value) if isinstance(value, (Decimal, UUID, date)) else value

    return Jsonb([{column: ready(value) for column, value in row.items()} for row in rows])


def import_pir_drawings(
    pir_id: UUID, created_by: UUID, pages: list[dict[str, Any]], flags: list[dict[str, Any]],
    areas: list[dict[str, Any]], features: list[dict[str, Any]], segments: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
) -> Optional[dict[str, int]]:
    """
    Write a Smart PIR import in one statement, so all of it lands or none: page scales and rotations (and the lengths of the segments already on a page whose scale changed), then new flags, their non-assessable areas, features, segments, and title blocks (replacing a page's). Only live pages of the PIR take anything, a pay item must be one of the project's, an area's flag and a feature's flag must be ones this import adds.
    Takes the PIR's id, the importing user's uuid, and the rows: pages {pir_page_id, scale_px_per_ft, rotation_deg}; flags {id, pir_page_id, the flag's writable columns, computed_quantity}; areas {flag_id, the area's writable columns, computed_quantity}; features {pir_page_id, the feature's writable columns, computed_quantity}; segments {pir_page_id, scale_px_per_ft (the import's scale for the page, or None), the segment's writable columns}; annotations {pir_page_id, the title block's fields}.
    Returns {pages_updated, segments_rescaled, flags, non_assessable_areas, features, segments, annotations}: how many of each were written; or None on failure.
    """
    flag_columns = {"id": "uuid", "pir_page_id": "uuid", **FLAG_TYPES, "computed_quantity": "numeric"}
    area_columns = {"flag_id": "uuid", **AREA_TYPES, "computed_quantity": "numeric"}
    feature_columns = {"pir_page_id": "uuid", **FEATURE_TYPES, "computed_quantity": "numeric"}
    segment_columns = {"pir_page_id": "uuid", "scale_px_per_ft": "numeric", **SEGMENT_TYPES}
    annotation_columns = {"pir_page_id": "uuid", **ANNOTATION_TYPES}
    annotation_updates = ", ".join(f"{field} = EXCLUDED.{field}" for field in ANNOTATION_TYPES)

    sql = f"""
        WITH live AS (
            SELECT page.pir_page_id, page.pir_id, page.scale_px_per_ft, p.project_id
            FROM icid.pir_pages page
            JOIN icid.pirs pir ON pir.id = page.pir_id
            JOIN icid.properties p ON p.id = pir.property_id
            WHERE page.pir_id = %s AND page.deleted_at IS NULL
        ),
        page_settings AS (
            UPDATE icid.pir_pages page
            SET scale_px_per_ft = COALESCE(x.scale_px_per_ft, page.scale_px_per_ft),
                rotation_deg = COALESCE(x.rotation_deg, page.rotation_deg),
                updated_at = now()
            FROM jsonb_to_recordset(%s::jsonb) AS x(pir_page_id uuid, scale_px_per_ft numeric, rotation_deg int)
            WHERE page.pir_page_id = x.pir_page_id AND page.pir_page_id IN (SELECT pir_page_id FROM live)
            RETURNING page.pir_page_id, page.scale_px_per_ft
        ),
        rescaled AS (
            UPDATE icid.segments s
            SET length_ft = {segment_length_sql("s.points", "ps.scale_px_per_ft")}, updated_at = now()
            FROM page_settings ps
            WHERE s.pir_page_id = ps.pir_page_id AND s.deleted_at IS NULL
            RETURNING s.segment_id
        ),
        new_flags AS (
            INSERT INTO icid.flags (id, pir_id, pir_page_id, {", ".join(FLAG_TYPES)}, computed_quantity, created_by)
            SELECT x.id, live.pir_id, live.pir_page_id, {_from_x(FLAG_TYPES)}, x.computed_quantity, %s::uuid
            FROM jsonb_to_recordset(%s::jsonb) AS {_recordset(flag_columns)}
            JOIN live ON live.pir_page_id = x.pir_page_id
            WHERE {pay_item_in_project("x.pay_item_id", "live.project_id")}
            RETURNING id
        ),
        new_areas AS (
            INSERT INTO icid.non_assessable_areas (flag_id, {", ".join(AREA_TYPES)}, computed_quantity, created_by)
            SELECT nf.id, {_from_x(AREA_TYPES)}, x.computed_quantity, %s::uuid
            FROM jsonb_to_recordset(%s::jsonb) AS {_recordset(area_columns)}
            JOIN new_flags nf ON nf.id = x.flag_id
            RETURNING non_assessable_area_id
        ),
        new_features AS (
            INSERT INTO icid.features (pir_page_id, {", ".join(FEATURE_TYPES)}, computed_quantity, created_by)
            SELECT live.pir_page_id, {_from_x(FEATURE_TYPES)}, x.computed_quantity, %s::uuid
            FROM jsonb_to_recordset(%s::jsonb) AS {_recordset(feature_columns)}
            JOIN live ON live.pir_page_id = x.pir_page_id
            LEFT JOIN new_flags nf ON nf.id = x.flag_id
            WHERE (x.flag_id IS NULL OR nf.id IS NOT NULL)
              AND {pay_item_in_project("x.pay_item_id", "live.project_id")}
            RETURNING feature_id
        ),
        new_segments AS (
            INSERT INTO icid.segments (pir_page_id, {", ".join(SEGMENT_TYPES)}, length_ft, created_by)
            SELECT live.pir_page_id, {_from_x(SEGMENT_TYPES)},
                   {segment_length_sql("x.points", "COALESCE(x.scale_px_per_ft, live.scale_px_per_ft)")}, %s::uuid
            FROM jsonb_to_recordset(%s::jsonb) AS {_recordset(segment_columns)}
            JOIN live ON live.pir_page_id = x.pir_page_id
            WHERE {pay_item_in_project("x.pay_item_id", "live.project_id")}
            RETURNING segment_id
        ),
        new_annotations AS (
            INSERT INTO icid.pir_annotations (pir_page_id, {", ".join(ANNOTATION_TYPES)})
            SELECT live.pir_page_id, {_from_x(ANNOTATION_TYPES)}
            FROM jsonb_to_recordset(%s::jsonb) AS {_recordset(annotation_columns)}
            JOIN live ON live.pir_page_id = x.pir_page_id
            ON CONFLICT (pir_page_id) DO UPDATE SET {annotation_updates}, updated_at = now()
            RETURNING pir_page_id
        )
        SELECT
            (SELECT count(*) FROM page_settings) AS pages_updated,
            (SELECT count(*) FROM rescaled) AS segments_rescaled,
            (SELECT count(*) FROM new_flags) AS flags,
            (SELECT count(*) FROM new_areas) AS non_assessable_areas,
            (SELECT count(*) FROM new_features) AS features,
            (SELECT count(*) FROM new_segments) AS segments,
            (SELECT count(*) FROM new_annotations) AS annotations;
    """
    params = (
        pir_id, _json_ready(pages),
        created_by, _json_ready(flags),
        created_by, _json_ready(areas),
        created_by, _json_ready(features),
        created_by, _json_ready(segments),
        _json_ready(annotations),
    )
    rows = run_query(sql, params)
    return rows[0] if rows else None
