from datetime import date
from typing import Any, Optional, Union
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.db.runner import run_query
from api.queries.idr_audit import AUDIT_CTE
from api.queries.idr_reports import REPORT_DATA_WITH_PAY_ITEM_IDS

IDR_COLUMNS = """
    idr_id,
    project_id,
    reporter_uuid,
    report_date,
    work_start_time,
    work_end_time,
    inspector_start_time,
    inspector_end_time,
    temp_low,
    temp_high,
    weather_am,
    weather_pm,
    total_pages,
    has_dismissed_auto_general,
    status,
    submitted_at,
    created_at,
    updated_at,
    inspector_signature_path,
    inspector_signed_at,
    idr_number,
    stage1_reviewer_uuid,
    stage1_reviewed_at,
    re_reviewer_uuid,
    re_signature_path,
    re_signed_at,
    return_reason,
    returned_from,
    deleted_at,
    deleted_by
"""

# What the IDR lists add to each row (FROM icid.idrs i): its report count, whether it holds a General, and the
# names of its inspector and reviewers
IDR_LIST_EXTRAS = """
            (
                SELECT COUNT(*)
                FROM icid.idr_reports r
                WHERE r.idr_id = i.idr_id
            ) AS report_count,
            EXISTS (
                SELECT 1
                FROM icid.idr_reports r
                WHERE r.idr_id = i.idr_id AND r.report_type = 'GEN' AND r.is_addendum = false
            ) AS has_general,
            (
                SELECT NULLIF(concat_ws(' ', u.first_name, u.last_name), '')
                FROM icid.users u
                WHERE u.uuid = i.reporter_uuid
            ) AS reporter_name,
            (
                SELECT NULLIF(concat_ws(' ', u.first_name, u.last_name), '')
                FROM icid.users u
                WHERE u.uuid = i.stage1_reviewer_uuid
            ) AS stage1_reviewer_name,
            (
                SELECT NULLIF(concat_ws(' ', u.first_name, u.last_name), '')
                FROM icid.users u
                WHERE u.uuid = i.re_reviewer_uuid
            ) AS re_reviewer_name
"""

# The project roles that see each review queue
QUEUE_ROLES = {"submitted": ["oe", "re"], "stage1_review": ["oe", "re"], "stage2_review": ["re"]}


class IdrNumberTakenError(Exception):
    """The IDR number is already used by another IDR on the project that isn't deleted."""


def create_idr(
    project_id: str, reporter_uuid: UUID, report_date: date
) -> Optional[list[dict[str, Any]]]:
    """
    Insert a new draft IDR unless one already exists for this project, reporter and date.
    Takes the project id, the reporter's user uuid and the report date.
    Returns a one-row list with the new IDR, an empty list if that day's IDR already exists, or None on failure.
    """
    sql = f"""
        INSERT INTO icid.idrs (project_id, reporter_uuid, report_date)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING {IDR_COLUMNS};
    """
    return run_query(sql, (project_id, reporter_uuid, report_date))


def get_idr_by_id(idr_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single IDR with its header fields.
    Takes the IDR uuid.
    Returns the IDR dict, or None if no IDR matches.
    """
    sql = f"""
        SELECT {IDR_COLUMNS}
        FROM icid.idrs
        WHERE idr_id = %s;
    """
    rows = run_query(sql, (idr_id,))
    return rows[0] if rows else None


def get_idr_id_for_day(
    project_id: str, reporter_uuid: UUID, report_date: date
) -> Optional[UUID]:
    """
    Look up the IDR a reporter already has on a project for a given date, deleted IDRs left out (a deleted one no longer holds its day).
    Takes the project id, the reporter's user uuid and the report date.
    Returns that IDR's uuid, or None if there is none.
    """
    sql = """
        SELECT idr_id
        FROM icid.idrs
        WHERE project_id = %s AND reporter_uuid = %s AND report_date = %s AND deleted_at IS NULL;
    """
    rows = run_query(sql, (project_id, reporter_uuid, report_date))
    return rows[0]["idr_id"] if rows else None


def touch_idr(idr_id: UUID) -> None:
    """
    Set an IDR's updated_at to now.
    Takes the IDR uuid.
    Returns nothing.
    """
    sql = """
        UPDATE icid.idrs
        SET updated_at = now()
        WHERE idr_id = %s;
    """
    run_query(sql, (idr_id,))


def set_dismissed_auto_general(idr_id: UUID) -> None:
    """
    Mark an IDR as having its auto-generated General dismissed, blocking future auto-creation, and stamp updated_at.
    Takes the IDR uuid.
    Returns nothing.
    """
    sql = """
        UPDATE icid.idrs
        SET has_dismissed_auto_general = true, updated_at = now()
        WHERE idr_id = %s;
    """
    run_query(sql, (idr_id,))


HEADER_COLUMNS = (
    "work_start_time",
    "work_end_time",
    "inspector_start_time",
    "inspector_end_time",
    "temp_low",
    "temp_high",
    "weather_am",
    "weather_pm",
)


def update_idr_header(idr_id: UUID, fields: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """
    Set the given header columns on a draft IDR and stamp updated_at; columns not in fields are untouched.
    Takes the IDR uuid and a dict of header column -> value (None clears); keys must be in HEADER_COLUMNS.
    Returns a one-row list with the updated IDR, an empty list if the IDR is not a draft, or None on failure.
    """
    unknown = set(fields) - set(HEADER_COLUMNS)
    if unknown:
        raise ValueError(f"Not header columns: {sorted(unknown)}")

    columns = [column for column in HEADER_COLUMNS if column in fields]
    assignments = [f"{column} = %s" for column in columns] + ["updated_at = now()"]

    sql = f"""
        UPDATE icid.idrs
        SET {", ".join(assignments)}
        WHERE idr_id = %s AND status = 'draft'
        RETURNING {IDR_COLUMNS};
    """
    return run_query(sql, (*[fields[column] for column in columns], idr_id))


def list_idrs(
    viewer_uuid: UUID,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    reporter_uuid: Optional[UUID] = None,
    include_deleted: bool = False,
    include_all_drafts: bool = False,
) -> Optional[list[dict[str, Any]]]:
    """
    List IDRs, most recently edited first, each with its report count, whether it holds a General, and the names of its inspector and reviewers. Deleted IDRs are left out, and so are other people's drafts, unless asked for.
    Takes the uuid of the user the list is for; optional project id, status and reporter uuid filters (any left as None is not applied); and whether to include deleted IDRs and other people's drafts (the endpoint allows an admin only).
    Returns a list of IDR dicts with the extra columns (empty if none match), or None on failure.
    """
    conditions: list[str] = []
    params: list[Any] = []

    if project_id is not None:
        conditions.append("i.project_id = %s")
        params.append(project_id)

    if status is not None:
        conditions.append("i.status = %s")
        params.append(status)

    if reporter_uuid is not None:
        conditions.append("i.reporter_uuid = %s")
        params.append(reporter_uuid)

    if not include_deleted:
        conditions.append("i.deleted_at IS NULL")

    if not include_all_drafts:
        conditions.append("(i.status <> 'draft' OR i.reporter_uuid = %s)")
        params.append(viewer_uuid)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sql = f"""
        SELECT
            {IDR_COLUMNS},
            {IDR_LIST_EXTRAS}
        FROM icid.idrs i
        {where}
        ORDER BY i.updated_at DESC, i.created_at DESC, i.idr_id;
    """
    return run_query(sql, tuple(params))


def list_review_queue(status: str, user_uuid: UUID, is_admin: bool) -> Optional[list[dict[str, Any]]]:
    """
    List the IDRs waiting in one review queue, oldest submission first, with the same extra columns as list_idrs.
    Takes the queue's status (a key of QUEUE_ROLES), the user's uuid and whether they are an admin. An admin sees every project; anyone else sees the projects where they hold a role that works that queue.
    Returns a list of IDR dicts (empty if none), or None on failure.
    """
    conditions = ["i.status = %s", "i.deleted_at IS NULL"]
    params: list[Any] = [status]

    if not is_admin:
        conditions.append("""EXISTS (
                SELECT 1
                FROM icid.project_users pu
                WHERE pu.project_id = i.project_id AND pu.user_uuid = %s AND pu.role = ANY(%s)
            )""")
        params.extend([user_uuid, QUEUE_ROLES[status]])

    sql = f"""
        SELECT
            {IDR_COLUMNS},
            {IDR_LIST_EXTRAS}
        FROM icid.idrs i
        WHERE {' AND '.join(conditions)}
        ORDER BY i.submitted_at ASC NULLS LAST, i.created_at, i.idr_id;
    """
    return run_query(sql, tuple(params))


def submit_idr(idr_id: UUID, signature_path: str, actor_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Submit a draft IDR in one statement: lock it, number its reports, set total_pages, status, submitted_at and updated_at, give every pay item that has none an id, stamp the inspector's signature (its path, and now as when it was signed), clear any return, and log the submit in icid.idr_audit.
    Takes the IDR uuid, the object path of the IDR's own copy of the signature, and the uuid of the user submitting. Pages run General's group first, then other main reports by creation, each followed by its addendums, then standalone addendums.
    Returns a one-row list with the submitted IDR, an empty list if it is not a draft or has no reports, or None on failure.
    """
    sql = f"""
        WITH target AS (
            SELECT idr_id, status AS from_status
            FROM icid.idrs
            WHERE idr_id = %s AND status = 'draft' AND deleted_at IS NULL
            FOR UPDATE
        ),
        ordered AS (
            SELECT
                r.report_id,
                ROW_NUMBER() OVER (
                    ORDER BY
                        (r.is_addendum AND r.parent_report_id IS NULL),
                        (COALESCE(p.report_type, r.report_type) = 'GEN') DESC,
                        COALESCE(p.created_at, r.created_at),
                        COALESCE(p.report_id, r.report_id),
                        r.is_addendum,
                        r.created_at,
                        r.report_id
                ) AS page_number
            FROM icid.idr_reports r
            JOIN target t ON t.idr_id = r.idr_id
            LEFT JOIN icid.idr_reports p ON p.report_id = r.parent_report_id
        ),
        numbered AS (
            UPDATE icid.idr_reports r
            SET page_number = o.page_number, updated_at = now(),
                report_data = {REPORT_DATA_WITH_PAY_ITEM_IDS}
            FROM ordered o
            WHERE r.report_id = o.report_id
        ),
        moved AS (
            UPDATE icid.idrs i
            SET status = 'submitted',
                submitted_at = now(),
                updated_at = now(),
                total_pages = (SELECT COUNT(*) FROM ordered),
                inspector_signature_path = %s,
                inspector_signed_at = now(),
                return_reason = NULL,
                returned_from = NULL
            WHERE i.idr_id IN (SELECT idr_id FROM target)
                AND EXISTS (SELECT 1 FROM ordered)
            RETURNING i.*
        ),
        {AUDIT_CTE}
        SELECT {IDR_COLUMNS}
        FROM moved;
    """
    return run_query(sql, (idr_id, signature_path, actor_uuid, "submit", None))


def _move_idr(
    idr_id: UUID,
    actor_uuid: UUID,
    action: str,
    from_status: Union[str, list[str], None],
    to_status: str,
    assignments: str,
    values: tuple = (),
    reviewer_column: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[list[dict[str, Any]]]:
    """
    Move an IDR from one review status to another in one statement: lock it, set the status, updated_at and the given columns, and log the move in icid.idr_audit.
    Takes the IDR uuid, the acting user's uuid, the action to log, the status it must be in (one, a list of them, or None for any) and the one it moves to, the extra SET assignments (written here, never from a request) with their values, an optional reviewer column that must hold the actor, and an optional note for the log. A deleted IDR is never moved.
    Returns a one-row list with the moved IDR, an empty list if it wasn't in that status (or the actor isn't that reviewer), or None on failure.
    """
    reviewer = f"AND {reviewer_column} = %s" if reviewer_column else ""
    if from_status is None:
        in_status, status_values = "", ()
    elif isinstance(from_status, str):
        in_status, status_values = "AND status = %s", (from_status,)
    else:
        in_status, status_values = "AND status = ANY(%s)", (list(from_status),)
    sql = f"""
        WITH target AS (
            SELECT idr_id, status AS from_status
            FROM icid.idrs
            WHERE idr_id = %s {in_status} AND deleted_at IS NULL {reviewer}
            FOR UPDATE
        ),
        moved AS (
            UPDATE icid.idrs i
            SET status = %s, updated_at = now(), {assignments}
            FROM target t
            WHERE i.idr_id = t.idr_id
            RETURNING i.*
        ),
        {AUDIT_CTE}
        SELECT {IDR_COLUMNS}
        FROM moved;
    """
    locked_by = (actor_uuid,) if reviewer_column else ()
    return run_query(sql, (idr_id, *status_values, *locked_by, to_status, *values, actor_uuid, action, note))


def find_idr_by_number(project_id: str, idr_number: str, except_idr_id: UUID) -> Optional[UUID]:
    """
    Look up the IDR on a project that already uses an IDR number, deleted IDRs left out.
    Takes the project id, the IDR number and the uuid of the IDR asking (never returned).
    Returns that other IDR's uuid, or None when the number is free.
    """
    sql = """
        SELECT idr_id
        FROM icid.idrs
        WHERE project_id = %s AND idr_number = %s AND deleted_at IS NULL AND idr_id <> %s
        LIMIT 1;
    """
    rows = run_query(sql, (project_id, idr_number, except_idr_id))
    return rows[0]["idr_id"] if rows else None


def accept_stage1(idr_id: UUID, actor_uuid: UUID, idr_number: Optional[str]) -> Optional[list[dict[str, Any]]]:
    """
    Pick a submitted IDR up for Stage 1: move it to stage1_review with the actor as its Stage 1 reviewer, and give it the IDR number unless it already has one (a number is kept across resubmits).
    Takes the IDR uuid, the reviewer's uuid and the IDR number to set (ignored when the IDR has one).
    Returns a one-row list with the IDR, an empty list if it isn't submitted, or None on failure; raises IdrNumberTakenError when another IDR on the project holds the number.
    """
    assignments = "stage1_reviewer_uuid = %s, stage1_reviewed_at = NULL, idr_number = COALESCE(i.idr_number, %s)"
    try:
        return _move_idr(idr_id, actor_uuid, "accept_stage1", "submitted", "stage1_review", assignments,
                         (actor_uuid, idr_number))
    except UniqueViolation as exc:
        raise IdrNumberTakenError(idr_number) from exc


def approve_stage1(idr_id: UUID, actor_uuid: UUID, as_reviewer: bool) -> Optional[list[dict[str, Any]]]:
    """
    Pass an IDR from Stage 1 to Stage 2: move it to stage2_review, stamp stage1_reviewed_at and clear any return.
    Takes the IDR uuid, the acting user's uuid, and whether they must be the IDR's Stage 1 reviewer (False for an admin).
    Returns a one-row list with the IDR, an empty list if it isn't in Stage 1 review under that reviewer, or None on failure.
    """
    assignments = "stage1_reviewed_at = now(), return_reason = NULL, returned_from = NULL"
    return _move_idr(idr_id, actor_uuid, "approve_stage1", "stage1_review", "stage2_review", assignments,
                     reviewer_column="stage1_reviewer_uuid" if as_reviewer else None)


def accept_stage2(idr_id: UUID, actor_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Pick an IDR up for Stage 2: it stays in stage2_review and the actor becomes its RE reviewer (the last to accept wins).
    Takes the IDR uuid and the RE's uuid.
    Returns a one-row list with the IDR, an empty list if it isn't in Stage 2 review, or None on failure.
    """
    return _move_idr(idr_id, actor_uuid, "accept_stage2", "stage2_review", "stage2_review", "re_reviewer_uuid = %s",
                     (actor_uuid,))


def approve_stage2(
    idr_id: UUID, actor_uuid: UUID, signature_path: str, as_reviewer: bool
) -> Optional[list[dict[str, Any]]]:
    """
    Approve an IDR for good: move it to approved, stamp the approver's signature (its path, and now as when it was signed), record them as its RE reviewer, and clear any return. The reviewer is set here so the name on the IDR is always the signer's, also when an admin approves in a reviewer's place.
    Takes the IDR uuid, the acting user's uuid, the object path of the IDR's own copy of their signature, and whether they must already be the IDR's RE reviewer (False for an admin).
    Returns a one-row list with the IDR, an empty list if it isn't in Stage 2 review under that reviewer, or None on failure.
    """
    assignments = ("re_reviewer_uuid = %s, re_signature_path = %s, re_signed_at = now(), "
                   "return_reason = NULL, returned_from = NULL")
    return _move_idr(idr_id, actor_uuid, "approve_stage2", "stage2_review", "approved", assignments,
                     (actor_uuid, signature_path), reviewer_column="re_reviewer_uuid" if as_reviewer else None)


def return_idr(
    idr_id: UUID, actor_uuid: UUID, from_status: str, to: str, comment: str, as_reviewer: bool
) -> Optional[list[dict[str, Any]]]:
    """
    Send an IDR back from review with a comment: to its inspector (it becomes a draft again) or, from Stage 2, to the OE (back to stage1_review). Sets return_reason and returned_from, and logs the comment.
    Takes the IDR uuid, the acting user's uuid, the review status it is in ('stage1_review' or 'stage2_review'), who it goes to ('inspector' or 'oe'), the comment, and whether the actor must be that stage's reviewer (False for an admin).
    Returns a one-row list with the IDR, an empty list if it isn't in that status under that reviewer, or None on failure.
    """
    stage_two = from_status == "stage2_review"
    return _move_idr(
        idr_id, actor_uuid, f"return_to_{to}", from_status, "draft" if to == "inspector" else "stage1_review",
        "return_reason = %s, returned_from = %s", (comment, "stage2" if stage_two else "stage1"),
        reviewer_column=("re_reviewer_uuid" if stage_two else "stage1_reviewer_uuid") if as_reviewer else None,
        note=comment,
    )


# The statuses an admin can unlock an IDR from: approved, or already back with the RE
UNLOCKABLE_STATUSES = ["approved", "stage2_review"]


def admin_unlock_idr(idr_id: UUID, actor_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Unlock an IDR for the RE to review again: move it to stage2_review, clear the RE's signature and its time, and clear the RE reviewer so an RE has to accept it again. Its number and the inspector's signature stay.
    Takes the IDR uuid and the admin's uuid.
    Returns a one-row list with the IDR, an empty list if it isn't in one of UNLOCKABLE_STATUSES (or is deleted), or None on failure.
    """
    assignments = "re_signature_path = NULL, re_signed_at = NULL, re_reviewer_uuid = NULL"
    return _move_idr(idr_id, actor_uuid, "admin_unlock", UNLOCKABLE_STATUSES, "stage2_review", assignments)


def admin_delete_idr(idr_id: UUID, actor_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Soft-delete an IDR, whatever its status: mark it deleted with when and by whom. The row and everything under it are kept; its day and its IDR number become free again.
    Takes the IDR uuid and the admin's uuid.
    Returns a one-row list with the IDR, an empty list if it was already deleted, or None on failure.
    """
    return _move_idr(idr_id, actor_uuid, "admin_delete", None, "deleted", "deleted_at = now(), deleted_by = %s",
                     (actor_uuid,))
