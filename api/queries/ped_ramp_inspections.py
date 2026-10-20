from typing import Any, Optional
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query

# An inspection as every function here returns it (i), with its corner's project and streets, the names of who
# signed and returned it, and how many TIFs it has (in all, and still drafts)
_INSPECTION_COLUMNS = """
            i.id, i.corner_id, c.project_id, c.north_south_street, c.east_west_street, c.corner_direction,
            i.status, i.requirements_met,
            i.inspector_uuid, NULLIF(concat_ws(' ', iu.first_name, iu.last_name), '') AS inspector_name,
            i.submitted_at, i.inspector_signed_at, i.inspector_signature_path,
            i.re_uuid, NULLIF(concat_ws(' ', ru.first_name, ru.last_name), '') AS re_name,
            i.re_signed_at, i.re_signature_path,
            i.return_comment, i.returned_at, i.returned_by,
            NULLIF(concat_ws(' ', bu.first_name, bu.last_name), '') AS returned_by_name,
            i.dot_pdf_path, i.created_at, i.updated_at, i.created_by,
            (SELECT count(*)::int FROM icid.ped_ramp_tifs t WHERE t.inspection_id = i.id) AS tif_count,
            (SELECT count(*)::int FROM icid.ped_ramp_tifs t
             WHERE t.inspection_id = i.id AND t.status = 'draft') AS draft_tif_count"""
# What every function's statement selects from, the inspection named i
_FROM = """
        JOIN icid.ped_ramp_corners c ON c.corner_id = i.corner_id
        LEFT JOIN icid.users iu ON iu.uuid = i.inspector_uuid
        LEFT JOIN icid.users ru ON ru.uuid = i.re_uuid
        LEFT JOIN icid.users bu ON bu.uuid = i.returned_by"""


def _one(sql: str, params: tuple[Any, ...]) -> Optional[dict[str, Any]]:
    """
    Run a statement that returns at most one inspection.
    Takes the SQL and its parameters.
    Returns the inspection dict, or None when it returned no row.
    """
    rows = run_query(sql, params)
    return rows[0] if rows else None


def _changed(cte: str, params: tuple[Any, ...]) -> Optional[dict[str, Any]]:
    """
    Run a data-modifying statement on one inspection and read it back as every function here returns it.
    Takes the UPDATE or INSERT (RETURNING *, run as the CTE i) and its parameters.
    Returns the inspection dict, or None when it changed nothing.
    """
    return _one("\n        WITH i AS (" + cte + "\n        )\n        SELECT" + _INSPECTION_COLUMNS + "\n        FROM i" + _FROM + ";", params)


def get_inspection(inspection_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one inspection, with its corner's project.
    Takes the inspection's id.
    Returns the inspection dict, or None if none matches.
    """
    return _one("\n        SELECT" + _INSPECTION_COLUMNS + "\n        FROM icid.ped_ramp_inspections i" + _FROM
                + "\n        WHERE i.id = %s;", (inspection_id,))


def get_corner_inspection(corner_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a corner's inspection: its live one (not approved), else its latest approved one.
    Takes the corner's id.
    Returns the inspection dict, or None when the corner has none.
    """
    sql = (
        "\n        SELECT" + _INSPECTION_COLUMNS + "\n        FROM icid.ped_ramp_inspections i" + _FROM + """
        WHERE i.corner_id = %s
        ORDER BY (i.status <> 'approved') DESC, i.created_at DESC
        LIMIT 1;"""
    )
    return _one(sql, (corner_id,))


def create_inspection(corner_id: UUID, created_by_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Start a corner's inspection as a draft. Adds nothing for a deleted corner or one that already has an inspection (one per corner for the pilot).
    Takes the corner's id and the uuid of the user starting it.
    Returns the new inspection dict, or None when nothing was added.
    """
    cte = """
            INSERT INTO icid.ped_ramp_inspections (corner_id, created_by)
            SELECT c.corner_id, %s
            FROM icid.ped_ramp_corners c
            WHERE c.corner_id = %s AND c.deleted_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM icid.ped_ramp_inspections other WHERE other.corner_id = c.corner_id)
            RETURNING *"""
    try:
        return _changed(cte, (created_by_uuid, corner_id))
    except UniqueViolation:
        # Another start took the corner between the statement's own check and its write
        return None


def set_requirements_met(inspection_id: UUID, requirements_met: Optional[bool]) -> Optional[dict[str, Any]]:
    """
    Record the inspector's answer to "are all ADA requirements met?" on an inspection that isn't approved.
    Takes the inspection's id and the answer (None clears it).
    Returns the updated inspection dict, or None when none matches or it is approved.
    """
    cte = """
            UPDATE icid.ped_ramp_inspections
            SET requirements_met = %s, updated_at = now()
            WHERE id = %s AND status <> 'approved'
            RETURNING *"""
    return _changed(cte, (requirements_met, inspection_id))


def submit_inspection(inspection_id: UUID, inspector_uuid: UUID, signature_path: str) -> Optional[dict[str, Any]]:
    """
    Submit a draft inspection, signed by the inspector: their signature's copy and the time, and the last return cleared.
    Takes the inspection's id, the submitting user's uuid and the path of the inspection's copy of their signature.
    Returns the updated inspection dict, or None when none matches or it isn't a draft.
    """
    cte = """
            UPDATE icid.ped_ramp_inspections
            SET status = 'submitted', inspector_uuid = %s, submitted_at = now(), inspector_signed_at = now(),
                inspector_signature_path = %s, return_comment = NULL, returned_at = NULL, returned_by = NULL,
                updated_at = now()
            WHERE id = %s AND status = 'draft'
            RETURNING *"""
    return _changed(cte, (inspector_uuid, signature_path, inspection_id))


def return_inspection(inspection_id: UUID, returned_by_uuid: UUID, comment: str) -> Optional[dict[str, Any]]:
    """
    Send a submitted inspection back to its inspector as a draft, with the RE's comment.
    Takes the inspection's id, the returning user's uuid and the comment.
    Returns the updated inspection dict, or None when none matches or it isn't submitted.
    """
    cte = """
            UPDATE icid.ped_ramp_inspections
            SET status = 'draft', return_comment = %s, returned_at = now(), returned_by = %s, updated_at = now()
            WHERE id = %s AND status = 'submitted'
            RETURNING *"""
    return _changed(cte, (comment, returned_by_uuid, inspection_id))


def approve_inspection(inspection_id: UUID, re_uuid: UUID, signature_path: str,
                       dot_pdf_path: Optional[str]) -> Optional[dict[str, Any]]:
    """
    Approve a submitted inspection, signed by the RE: their signature's copy, the time, and the DOT form filled for it. Approves nothing while one of its TIFs is a draft.
    Takes the inspection's id, the approving user's uuid, the path of the inspection's copy of their signature and the DOT form's path (None when none was stored).
    Returns the updated inspection dict, or None when none matches, it isn't submitted or a TIF of it is a draft.
    """
    cte = """
            UPDATE icid.ped_ramp_inspections ins
            SET status = 'approved', re_uuid = %s, re_signed_at = now(), re_signature_path = %s, dot_pdf_path = %s,
                updated_at = now()
            WHERE ins.id = %s AND ins.status = 'submitted'
              AND NOT EXISTS (SELECT 1 FROM icid.ped_ramp_tifs t WHERE t.inspection_id = ins.id AND t.status = 'draft')
            RETURNING ins.*"""
    return _changed(cte, (re_uuid, signature_path, dot_pdf_path, inspection_id))
