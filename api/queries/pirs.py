from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query

# A PIR as every function here returns it: its own columns and its property's project.
# DEPRECATED (R1e, remove in R1f): source_image_url and source_image_scale are read from the PIR's live page 1, for
# the R1d image card the deployed frontend still shows; the columns themselves were dropped by migration 033.
_PIR_COLUMNS = """
            pir.id, pir.property_id,
            (SELECT p.project_id FROM icid.properties p WHERE p.id = pir.property_id) AS project_id,
            (SELECT pg.image_path FROM icid.pir_pages pg
             WHERE pg.pir_id = pir.id AND pg.page_number = 1 AND pg.deleted_at IS NULL) AS source_image_url,
            (SELECT pg.scale_px_per_ft::float8 FROM icid.pir_pages pg
             WHERE pg.pir_id = pir.id AND pg.page_number = 1 AND pg.deleted_at IS NULL) AS source_image_scale,
            pir.inspector_initials, pir.inspector_sign_date,
            pir.digitized_by, pir.digitized_at, pir.as_built_signed_by, pir.as_built_signed_at,
            pir.north_bearing::text AS north_bearing,
            pir.created_at, pir.updated_at, pir.deleted_at, pir.deleted_by"""


def get_pir_by_property(property_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a property's live PIR.
    Takes the property's id.
    Returns the PIR dict, or None when the property has no PIR that isn't deleted.
    """
    sql = (
        "\n        SELECT"
        + _PIR_COLUMNS
        + """
        FROM icid.pirs pir
        WHERE pir.property_id = %s AND pir.deleted_at IS NULL;"""
    )
    rows = run_query(sql, (property_id,))
    return rows[0] if rows else None


def list_pirs_for_property(property_id: UUID, include_deleted: bool = False) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a property's PIRs, the soft-deleted ones only when asked for.
    Takes the property's id and whether to include soft-deleted PIRs.
    Returns the PIR dicts, most recently digitized first, or None on failure.
    """
    sql = (
        "\n        SELECT"
        + _PIR_COLUMNS
        + """
        FROM icid.pirs pir
        WHERE pir.property_id = %s AND (%s OR pir.deleted_at IS NULL)
        ORDER BY pir.digitized_at DESC;"""
    )
    return run_query(sql, (property_id, include_deleted))


def get_pir(pir_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one PIR, deleted or not.
    Takes the PIR's id.
    Returns the PIR dict, or None if no PIR matches.
    """
    sql = "\n        SELECT" + _PIR_COLUMNS + "\n        FROM icid.pirs pir\n        WHERE pir.id = %s;"
    rows = run_query(sql, (pir_id,))
    return rows[0] if rows else None


def create_pir(property_id: UUID, digitized_by_uuid: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Add a PIR to a property, digitized by the given admin now. Adds nothing when the property doesn't exist, is deleted, or already has a live PIR.
    Takes the property's id, the uuid of the admin digitizing it, and the values keyed by column (inspector_initials, inspector_sign_date).
    Returns the new PIR dict, or None when nothing was added.
    """
    sql = (
        """
        INSERT INTO icid.pirs AS pir (property_id, inspector_initials, inspector_sign_date, digitized_by)
        SELECT p.id, %s, %s, %s
        FROM icid.properties p
        WHERE p.id = %s AND p.deleted_at IS NULL
        ON CONFLICT DO NOTHING
        RETURNING"""
        + _PIR_COLUMNS
        + ";"
    )
    params = (data["inspector_initials"], data["inspector_sign_date"], digitized_by_uuid, property_id)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def update_pir(pir_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Change some of a PIR; a column left out keeps its value. north_bearing, when it is there, is set even to None (clearing it).
    Takes the PIR's id and the new values keyed by column (inspector_initials, inspector_sign_date, north_bearing).
    Returns the updated PIR dict, or None if no PIR matches.
    """
    sql = (
        """
        UPDATE icid.pirs pir
        SET inspector_initials = COALESCE(%s, pir.inspector_initials),
            inspector_sign_date = COALESCE(%s::date, pir.inspector_sign_date),
            north_bearing = CASE WHEN %s THEN %s::icid.compass_point ELSE pir.north_bearing END,
            updated_at = now()
        WHERE pir.id = %s
        RETURNING"""
        + _PIR_COLUMNS
        + ";"
    )
    params = (data.get("inspector_initials"), data.get("inspector_sign_date"), "north_bearing" in data,
              data.get("north_bearing"), pir_id)
    rows = run_query(sql, params)
    return rows[0] if rows else None


def soft_delete_pir(pir_id: UUID, deleted_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a PIR deleted, with when and by whom; it frees its property for a replacement.
    Takes the PIR's id and the uuid of the admin deleting it.
    Returns the updated PIR dict, or None if no PIR matches or it is already deleted.
    """
    sql = (
        """
        UPDATE icid.pirs pir
        SET deleted_at = now(),
            deleted_by = %s,
            updated_at = now()
        WHERE pir.id = %s AND pir.deleted_at IS NULL
        RETURNING"""
        + _PIR_COLUMNS
        + ";"
    )
    rows = run_query(sql, (deleted_by_uuid, pir_id))
    return rows[0] if rows else None


def undelete_pir(pir_id: UUID) -> Optional[dict[str, Any]]:
    """
    Bring a deleted PIR back: clear deleted_at and deleted_by. Changes nothing when its property now has another live PIR.
    Takes the PIR's id.
    Returns the updated PIR dict, or None if no PIR matches, it isn't deleted, or its property has a live PIR.
    """
    sql = (
        """
        UPDATE icid.pirs pir
        SET deleted_at = NULL,
            deleted_by = NULL,
            updated_at = now()
        WHERE pir.id = %s
          AND pir.deleted_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM icid.pirs other
              WHERE other.property_id = pir.property_id
                AND other.id <> pir.id
                AND other.deleted_at IS NULL
          )
        RETURNING"""
        + _PIR_COLUMNS
        + ";"
    )
    try:
        rows = run_query(sql, (pir_id,))
    except UniqueViolation:
        # Another PIR was created for the property between the statement's own check and its write
        return None
    return rows[0] if rows else None

