from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query
from api.schemas.ped_ramp_tif import TifFields

# The columns an edit writes, the TIF's sections 1-7, in a fixed order
WRITTEN_COLUMNS = tuple(TifFields.model_fields)
# A TIF as every function here returns it (t), with its ramp, its project and corner, its inspection's status and the
# name of who signed it
_TIF_COLUMNS = """
            t.*, p.project_id, i.corner_id, i.status AS inspection_status, p.ramp_type::text AS ramp_type,
            p.quadrant::text AS quadrant, a.ramp_id_label,
            NULLIF(concat_ws(' ', su.first_name, su.last_name), '') AS signed_by_name"""
# What every function's statement selects from, the TIF named t
_FROM = """
        JOIN icid.properties p ON p.id = t.property_id
        JOIN icid.ped_ramp_inspections i ON i.id = t.inspection_id
        LEFT JOIN icid.ped_ramp_attributes a ON a.property_id = t.property_id
        LEFT JOIN icid.users su ON su.uuid = t.signed_by_uuid"""


def _changed(cte: str, params: tuple[Any, ...]) -> Optional[dict[str, Any]]:
    """
    Run a data-modifying statement on one TIF and read it back as every function here returns it.
    Takes the INSERT or UPDATE (RETURNING *, run as the CTE t) and its parameters.
    Returns the TIF dict, or None when it changed nothing.
    """
    rows = run_query("\n        WITH t AS (" + cte + "\n        )\n        SELECT" + _TIF_COLUMNS + "\n        FROM t" + _FROM + ";",
                 params)
    return rows[0] if rows else None


def get_tif(tif_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one TIF, with its ramp, its project and its inspection's status.
    Takes the TIF's id.
    Returns the TIF dict, or None if none matches.
    """
    rows = run_query("\n        SELECT" + _TIF_COLUMNS + "\n        FROM icid.ped_ramp_tifs t" + _FROM + "\n        WHERE t.id = %s;",
                 (tif_id,))
    return rows[0] if rows else None


def list_inspection_tifs(inspection_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch an inspection's TIFs, oldest first.
    Takes the inspection's id.
    Returns the TIF dicts, or None on failure.
    """
    sql = ("\n        SELECT" + _TIF_COLUMNS + "\n        FROM icid.ped_ramp_tifs t" + _FROM
           + "\n        WHERE t.inspection_id = %s\n        ORDER BY t.created_at;")
    return run_query(sql, (inspection_id,))


def create_tif(inspection_id: UUID, property_id: UUID, created_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Draft a TIF for one ramp of an inspection, prefilled from what ICID knows: the corner's DOT id and permittee, the ramp's label as DOT's ramp id, and its construction. Adds nothing unless the ramp is a live ped ramp at the inspection's corner and the inspection isn't approved, or when the ramp already has a TIF on it.
    Takes the inspection's id, the ramp's (property's) id and the uuid of the user drafting it.
    Returns the new TIF dict, or None when nothing was added.
    """
    cte = """
            INSERT INTO icid.ped_ramp_tifs (property_id, inspection_id, created_by, dot_corner_id, dot_ramp_id,
                                           permittee_id, ramp_construction)
            SELECT p.id, i.id, %s, c.dot_corner_id, a.ramp_id_label, c.permittee_id,
                   CASE a.construction WHEN 'new_install' THEN 'new_installation' WHEN 'upgrade' THEN 'upgrade' END
            FROM icid.ped_ramp_inspections i
            JOIN icid.ped_ramp_corners c ON c.corner_id = i.corner_id
            JOIN icid.properties p ON p.corner_id = i.corner_id AND p.id = %s
                                   AND p.kind = 'ped_ramp' AND p.deleted_at IS NULL
            LEFT JOIN icid.ped_ramp_attributes a ON a.property_id = p.id
            WHERE i.id = %s AND i.status <> 'approved'
            ON CONFLICT DO NOTHING
            RETURNING *"""
    return _changed(cte, (created_by_uuid, property_id, inspection_id))


def update_tif(tif_id: UUID, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Write a draft TIF's sections 1-7 whole, as an edit leaves them. Changes nothing once it, or its inspection, is approved.
    Takes the TIF's id and every written column's value keyed by column (one left out is written as None, or unticked).
    Returns the updated TIF dict, or None when none matches or it can't be changed.
    """
    cte = (
        """
            UPDATE icid.ped_ramp_tifs tif
            SET """
        + ", ".join(f"{column} = %s" for column in WRITTEN_COLUMNS)
        + """, updated_at = now()
            WHERE tif.id = %s AND tif.status = 'draft'
              AND NOT EXISTS (SELECT 1 FROM icid.ped_ramp_inspections i
                              WHERE i.id = tif.inspection_id AND i.status = 'approved')
            RETURNING tif.*"""
    )
    values = tuple(data.get(column) for column in WRITTEN_COLUMNS)
    return _changed(cte, (*values, tif_id))


def approve_tif(tif_id: UUID, signed_by_uuid: UUID, signer_name: str, license_no: str,
                signature_path: str) -> Optional[dict[str, Any]]:
    """
    Approve a draft TIF, signed by the RE: their name and licence as they print, their signature's copy and the time. Approves nothing once its inspection is approved.
    Takes the TIF's id, the RE's uuid, their name, their New York licence number and the path of the TIF's copy of their signature.
    Returns the updated TIF dict, or None when none matches, it isn't a draft or its inspection is approved.
    """
    cte = """
            UPDATE icid.ped_ramp_tifs tif
            SET status = 'approved', signed_by_uuid = %s, signer_name = %s, signer_ny_license_no = %s,
                signer_signature_path = %s, signed_at = now(), updated_at = now()
            WHERE tif.id = %s AND tif.status = 'draft'
              AND NOT EXISTS (SELECT 1 FROM icid.ped_ramp_inspections i
                              WHERE i.id = tif.inspection_id AND i.status = 'approved')
            RETURNING tif.*"""
    return _changed(cte, (signed_by_uuid, signer_name, license_no, signature_path, tif_id))


def delete_tif(tif_id: UUID) -> Optional[dict[str, Any]]:
    """
    Delete a TIF for good, with its photos' rows, in one statement; nothing is deleted once its inspection is approved.
    Takes the TIF's id.
    Returns {id (None when nothing was deleted), photo_paths: the deleted photos' Storage paths}, or None on failure.
    """
    sql = """
        WITH target AS (
            SELECT t.id FROM icid.ped_ramp_tifs t
            JOIN icid.ped_ramp_inspections i ON i.id = t.inspection_id
            WHERE t.id = %s AND i.status <> 'approved'
        ),
        photos AS (
            DELETE FROM icid.ped_ramp_photos ph USING target WHERE ph.tif_id = target.id RETURNING ph.storage_path
        ),
        gone AS (
            DELETE FROM icid.ped_ramp_tifs t USING target WHERE t.id = target.id RETURNING t.id
        )
        SELECT (SELECT id FROM gone) AS id,
               COALESCE((SELECT array_agg(storage_path) FROM photos), '{}') AS photo_paths;"""
    rows = run_query(sql, (tif_id,))
    return rows[0] if rows else None
