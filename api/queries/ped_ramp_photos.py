from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query

# The most photos one inspection or one TIF takes, its layout included; the INSERT counts them
MAX_PHOTOS_PER_FORM = 20

# A photo as every function here returns it (f)
_PHOTO_COLUMNS = """
            f.id, f.inspection_id, f.tif_id, f.property_id, f.kind, f.photo_number, f.caption, f.storage_path,
            f.content_type, f.byte_size, f.uploaded_at, f.uploaded_by"""


def get_photo(photo_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one photo, with what its writes are checked against: its project, its TIF's status (a TIF's photo) and the status of the inspection it is under (its own, or its TIF's).
    Takes the photo's id.
    Returns the photo dict (project_id, tif_status, inspection_status added), or None if none matches.
    """
    sql = "\n        SELECT" + _PHOTO_COLUMNS + """,
            c.project_id, t.status AS tif_status, i.status AS inspection_status
        FROM icid.ped_ramp_photos f
        LEFT JOIN icid.ped_ramp_tifs t ON t.id = f.tif_id
        JOIN icid.ped_ramp_inspections i ON i.id = COALESCE(f.inspection_id, t.inspection_id)
        JOIN icid.ped_ramp_corners c ON c.corner_id = i.corner_id
        WHERE f.id = %s;"""
    rows = run_query(sql, (photo_id,))
    return rows[0] if rows else None


def get_photo_tif(tif_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch what a TIF's photo routes check: the TIF's status, ramp and project, and its inspection's status.
    Takes the TIF's id.
    Returns {id, property_id, inspection_id, status, project_id, inspection_status}, or None if none matches.
    """
    sql = """
        SELECT t.id, t.property_id, t.inspection_id, t.status, c.project_id, i.status AS inspection_status
        FROM icid.ped_ramp_tifs t
        JOIN icid.ped_ramp_inspections i ON i.id = t.inspection_id
        JOIN icid.ped_ramp_corners c ON c.corner_id = i.corner_id
        WHERE t.id = %s;"""
    rows = run_query(sql, (tif_id,))
    return rows[0] if rows else None


def is_ramp_of_inspection(inspection_id: UUID, property_id: UUID) -> Optional[bool]:
    """
    Check that a property is a live ped ramp at an inspection's corner.
    Takes the inspection's id and the property's id.
    Returns True or False, or None if the statement failed.
    """
    sql = """
        SELECT EXISTS (
            SELECT 1
            FROM icid.ped_ramp_inspections i
            JOIN icid.properties p ON p.corner_id = i.corner_id
            WHERE i.id = %s AND p.id = %s AND p.kind = 'ped_ramp' AND p.deleted_at IS NULL
        ) AS is_ramp;"""
    rows = run_query(sql, (inspection_id, property_id))
    return None if rows is None else bool(rows[0]["is_ramp"])


def list_photos(inspection_id: Optional[UUID] = None, tif_id: Optional[UUID] = None) -> Optional[list[dict[str, Any]]]:
    """
    List an inspection's or a TIF's photos: by kind (photos, then the layout), then number.
    Takes the inspection's id or the TIF's id (one of them).
    Returns the photo dicts (empty when it has none), or None if the statement failed.
    """
    column = "inspection_id" if inspection_id is not None else "tif_id"
    sql = "\n        SELECT" + _PHOTO_COLUMNS + f"""
        FROM icid.ped_ramp_photos f
        WHERE f.{column} = %s
        ORDER BY (f.kind = 'layout'), f.photo_number, f.uploaded_at;"""
    return run_query(sql, (inspection_id if inspection_id is not None else tif_id,))


def _insert(parent_sql: str, values_sql: str, params: tuple[Any, ...]) -> Optional[dict[str, Any]]:
    """
    Add a photo under a parent that may still take one, in one statement that reads the parent's checks and inserts only when they all hold.
    Takes the parent CTE (one row with ramp_ok, photo_count and number_taken, none when the parent is locked or gone), the INSERT's SELECT list and the parameters of both.
    Returns {ramp_ok, photo_count, number_taken, ...the photo's columns, None when nothing was added}, or None when the parent had no row.
    """
    sql = f"""
        WITH parent AS ({parent_sql}
        ), added AS (
            INSERT INTO icid.ped_ramp_photos (id, inspection_id, tif_id, property_id, kind, photo_number, caption,
                                              storage_path, content_type, byte_size, uploaded_by)
            SELECT {values_sql}
            FROM parent
            WHERE parent.ramp_ok AND parent.photo_count < {MAX_PHOTOS_PER_FORM} AND NOT parent.number_taken
            ON CONFLICT DO NOTHING
            RETURNING *
        )
        SELECT parent.ramp_ok, parent.photo_count, parent.number_taken,""" + _PHOTO_COLUMNS + """
        FROM parent
        LEFT JOIN added f ON true;"""
    try:
        rows = run_query(sql, params)
    except UniqueViolation:
        # Another upload took the number between the statement's check and its write
        return {"ramp_ok": True, "photo_count": 0, "number_taken": True, "id": None}
    if rows is None:
        raise RuntimeError("Failed to save the photo")
    return rows[0] if rows else None


def insert_inspection_photo(photo_id: UUID, inspection_id: UUID, property_id: UUID, photo_number: int,
                            caption: Optional[str], storage_path: str, content_type: str, byte_size: int,
                            uploaded_by: UUID) -> Optional[dict[str, Any]]:
    """
    Add a photo of one ramp to an inspection that isn't approved: only when the ramp is a live ped ramp at its corner, the inspection has fewer than MAX_PHOTOS_PER_FORM photos and the number is free on it.
    Takes the new photo's id, the inspection's id, the ramp's property id, the number, the caption (None for none), the stored file's path, type and size, and the uploading user's uuid.
    Returns the checks and the photo as _insert does, or None when the inspection is approved or gone; raises RuntimeError when the statement fails.
    """
    parent_sql = """
            SELECT i.id,
                   EXISTS (SELECT 1 FROM icid.properties p
                           WHERE p.id = %s AND p.corner_id = i.corner_id AND p.kind = 'ped_ramp'
                             AND p.deleted_at IS NULL) AS ramp_ok,
                   (SELECT count(*)::int FROM icid.ped_ramp_photos o WHERE o.inspection_id = i.id) AS photo_count,
                   EXISTS (SELECT 1 FROM icid.ped_ramp_photos o
                           WHERE o.inspection_id = i.id AND o.photo_number = %s) AS number_taken
            FROM icid.ped_ramp_inspections i
            WHERE i.id = %s AND i.status <> 'approved'"""
    values_sql = "%s, parent.id, NULL, %s, 'photo', %s, %s, %s, %s, %s, %s"
    return _insert(parent_sql, values_sql, (
        property_id, photo_number, inspection_id,
        photo_id, property_id, photo_number, caption, storage_path, content_type, byte_size, uploaded_by,
    ))


def insert_tif_photo(photo_id: UUID, tif_id: UUID, kind: str, photo_number: int, caption: Optional[str],
                     storage_path: str, content_type: str, byte_size: int, uploaded_by: UUID) -> Optional[dict[str, Any]]:
    """
    Add a photo, or the site layout, to a draft TIF whose inspection isn't approved: only when it has fewer than MAX_PHOTOS_PER_FORM photos and the number (for a layout, the layout's one place) is free. The photo names the TIF's ramp.
    Takes the new photo's id, the TIF's id, the kind ('photo' or 'layout'), the number (1 for a layout), the caption, the stored file's path, type and size, and the uploading user's uuid.
    Returns the checks and the photo as _insert does (ramp_ok always true), or None when the TIF is approved, its inspection is, or it is gone; raises RuntimeError when the statement fails.
    """
    parent_sql = """
            SELECT t.id, t.property_id, true AS ramp_ok,
                   (SELECT count(*)::int FROM icid.ped_ramp_photos o WHERE o.tif_id = t.id) AS photo_count,
                   EXISTS (SELECT 1 FROM icid.ped_ramp_photos o
                           WHERE o.tif_id = t.id AND o.kind = %s
                             AND (o.kind = 'layout' OR o.photo_number = %s)) AS number_taken
            FROM icid.ped_ramp_tifs t
            JOIN icid.ped_ramp_inspections i ON i.id = t.inspection_id
            WHERE t.id = %s AND t.status = 'draft' AND i.status <> 'approved'"""
    values_sql = "%s, NULL, parent.id, parent.property_id, %s, %s, %s, %s, %s, %s, %s"
    return _insert(parent_sql, values_sql, (
        kind, photo_number, tif_id,
        photo_id, kind, photo_number, caption, storage_path, content_type, byte_size, uploaded_by,
    ))


def update_photo(photo_id: UUID, photo_number: Optional[int], caption_sent: bool,
                 caption: Optional[str]) -> Optional[dict[str, Any]]:
    """
    Renumber a photo or change its caption, only when no other photo of its form (of the same kind) has the new number.
    Takes the photo's id, the new number (None keeps it), whether a caption was sent and the caption (None clears it).
    Returns {number_taken, ...the photo's columns, None when nothing changed}, or None when the photo is gone; raises RuntimeError when the statement fails.
    """
    sql = """
        WITH target AS (
            SELECT t.id, EXISTS (
                SELECT 1 FROM icid.ped_ramp_photos o
                WHERE o.id <> t.id AND o.kind = t.kind AND o.photo_number = %s
                  AND (o.inspection_id = t.inspection_id OR o.tif_id = t.tif_id)
            ) AS number_taken
            FROM icid.ped_ramp_photos t
            WHERE t.id = %s
        ), changed AS (
            UPDATE icid.ped_ramp_photos p
            SET photo_number = COALESCE(%s, p.photo_number),
                caption = CASE WHEN %s THEN %s ELSE p.caption END
            FROM target
            WHERE p.id = target.id AND NOT target.number_taken
            RETURNING p.*
        )
        SELECT target.number_taken,""" + _PHOTO_COLUMNS + """
        FROM target
        LEFT JOIN changed f ON true;"""
    try:
        rows = run_query(sql, (photo_number, photo_id, photo_number, caption_sent, caption))
    except UniqueViolation:
        return {"number_taken": True, "id": None}
    if rows is None:
        raise RuntimeError("Failed to update the photo")
    return rows[0] if rows else None


def delete_photo(photo_id: UUID) -> Optional[dict[str, Any]]:
    """
    Delete a photo's row outright (nothing names a photo).
    Takes the photo's id.
    Returns {id, storage_path} of the row deleted, or None when there was none or the statement failed.
    """
    rows = run_query("""
        DELETE FROM icid.ped_ramp_photos
        WHERE id = %s
        RETURNING id, storage_path;""", (photo_id,))
    return rows[0] if rows else None
