"""
SQL for icid.idr_field_edits, the log of what reviewers change on an IDR.

An edit is applied and logged in one statement: it locks the IDR (checking it is still in the stage, and under the
reviewer, the caller checked), writes the new value into the IDR, adds the edit row and adds the idr_audit row. The
IDR therefore always holds the current value; the value before a field's first edit is that edit's old_value.

A write only goes through while the field still holds the old value the caller read, so two reviewers' edits can't
cross: the loser gets an empty result and reads again.
"""

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query
from api.queries.idr_audit import EDIT_AUDIT_CTE
from api.queries.idrs import HEADER_COLUMNS

FIELD_EDIT_COLUMNS = """
    edit_id,
    idr_id,
    report_id,
    field_path,
    edit_type,
    old_value,
    new_value,
    editor_uuid,
    editor_stage,
    edited_at
"""

# The review statuses an IDR can be edited in, with the stage recorded on the edit and the column holding its reviewer
EDIT_STAGES = {
    "stage1_review": ("stage1", "stage1_reviewer_uuid"),
    "stage2_review": ("stage2", "re_reviewer_uuid"),
}

# What each kind of edit is logged as in icid.idr_audit
AUDIT_ACTIONS = {"field_change": "field_edit", "pay_item_revision": "pay_item_revise", "pay_item_add": "pay_item_add",
                 "pay_item_approve": "pay_item_approve"}


def _target_cte(as_reviewer: bool, status: str) -> str:
    """
    Write the CTE that locks the IDR an edit is for.
    Takes whether the editor must be the stage's reviewer (False for an admin) and the review status the IDR must be in.
    Returns the CTE's SQL; its parameters are the IDR uuid, the status and, when as_reviewer, the editor's uuid.
    """
    reviewer = f"AND {EDIT_STAGES[status][1]} = %s" if as_reviewer else ""
    return f"""
        target AS (
            SELECT idr_id, status AS from_status
            FROM icid.idrs
            WHERE idr_id = %s AND status = %s AND deleted_at IS NULL {reviewer}
            FOR UPDATE
        )"""


def _log_ctes() -> str:
    """
    Write the CTEs that follow the change itself: the edit row, then its idr_audit row.
    Takes nothing. The statement must define target and, before these, moved (the IDR row after the change; empty when the change didn't go through).
    Returns the SQL; its parameters are report_id, field_path, edit_type, old_value, new_value, editor_uuid, editor_stage, then the audit action.
    """
    return f"""
        edit AS (
            INSERT INTO icid.idr_field_edits
                (idr_id, report_id, field_path, edit_type, old_value, new_value, editor_uuid, editor_stage)
            SELECT m.idr_id, %s, %s, %s, %s, %s, %s, %s
            FROM moved m
            RETURNING *
        ),
        {EDIT_AUDIT_CTE}
        SELECT {FIELD_EDIT_COLUMNS}
        FROM edit;
    """


def _log_values(report_id: Optional[UUID], field_path: str, edit_type: str, old_value: Any, new_value: Any,
                editor_uuid: UUID, status: str) -> tuple:
    """
    Line up the parameters _log_ctes takes.
    Takes the edit's report (None for a header field), path, type, old and new values (old_value None with edit_type 'pay_item_add' means there was none), the editor and the IDR's status.
    Returns the parameter tuple.
    """
    old = None if edit_type == "pay_item_add" else Jsonb(old_value)
    return (report_id, field_path, edit_type, old, Jsonb(new_value), editor_uuid, EDIT_STAGES[status][0],
            AUDIT_ACTIONS[edit_type])


def apply_header_edit(idr_id: UUID, column: str, old_value: Any, new_value: Any, editor_uuid: UUID, status: str,
                      as_reviewer: bool) -> Optional[list[dict[str, Any]]]:
    """
    Change one header field of an IDR in review and log the edit, in one statement.
    Takes the IDR uuid, the header column (one of HEADER_COLUMNS), the value it holds now and the new one (as the column's own type), the editor's uuid, the review status the IDR is in, and whether the editor must be that stage's reviewer (False for an admin).
    Returns a one-row list with the edit; an empty list if the IDR left that status, changed reviewer, or no longer holds old_value; None on failure. Raises ValueError for a column that isn't a header field.
    """
    if column not in HEADER_COLUMNS:
        raise ValueError(f"Not a header column: {column}")

    sql = f"""
        WITH {_target_cte(as_reviewer, status)},
        moved AS (
            UPDATE icid.idrs i
            SET {column} = %s, updated_at = now()
            FROM target t
            WHERE i.idr_id = t.idr_id AND i.{column} IS NOT DISTINCT FROM %s
            RETURNING i.*
        ),
        {_log_ctes()}"""
    locked_by = (editor_uuid,) if as_reviewer else ()
    return run_query(sql, (
        idr_id, status, *locked_by, new_value, old_value,
        *_log_values(None, f"header.{column}", "field_change", _json_safe(old_value), _json_safe(new_value),
                     editor_uuid, status),
    ))


def _json_safe(value: Any) -> Any:
    """
    Make a header value storable as JSON: a temperature read from its NUMERIC column becomes a number, a time its text.
    Takes the value.
    Returns it unchanged when it is None, text, a bool or a number; a Decimal as a float; anything else as str(value).
    """
    if isinstance(value, Decimal):
        return float(value)
    return value if value is None or isinstance(value, (str, bool, int, float)) else str(value)


def apply_report_edit(idr_id: UUID, report_id: UUID, field_path: str, json_path: list[str], edit_type: str,
                      old_value: Any, new_value: Any, editor_uuid: UUID, status: str,
                      as_reviewer: bool) -> Optional[list[dict[str, Any]]]:
    """
    Change one field inside a report's report_data, for an IDR in review, and log the edit, in one statement.
    Takes the IDR and report uuids, the field_path to record, the same field as a path into report_data (keys and list positions, e.g. ['payItems', '2', 'payQuantity']), the edit type ('field_change' or 'pay_item_revision'), the value there now and the new one, the editor's uuid, the review status the IDR is in, and whether the editor must be that stage's reviewer (False for an admin).
    Returns a one-row list with the edit; an empty list if the IDR left that status, changed reviewer, the report isn't in it, or the field no longer holds old_value (or isn't there); None on failure.
    """
    sql = f"""
        WITH {_target_cte(as_reviewer, status)},
        changed AS (
            UPDATE icid.idr_reports r
            SET report_data = jsonb_set(r.report_data, %s, %s, false), updated_at = now()
            FROM target t
            WHERE r.idr_id = t.idr_id AND r.report_id = %s AND r.report_data #> %s = %s
            RETURNING r.report_id
        ),
        moved AS (
            UPDATE icid.idrs i
            SET updated_at = now()
            WHERE i.idr_id IN (SELECT idr_id FROM target) AND EXISTS (SELECT 1 FROM changed)
            RETURNING i.*
        ),
        {_log_ctes()}"""
    locked_by = (editor_uuid,) if as_reviewer else ()
    return run_query(sql, (
        idr_id, status, *locked_by, json_path, Jsonb(new_value), report_id, json_path, Jsonb(old_value),
        *_log_values(report_id, field_path, edit_type, old_value, new_value, editor_uuid, status),
    ))


def append_pay_item(idr_id: UUID, report_id: UUID, pay_item: dict[str, Any], editor_uuid: UUID, status: str,
                    as_reviewer: bool) -> Optional[list[dict[str, Any]]]:
    """
    Add a pay item to the end of a report's payItems, for an IDR in review, and log it as a 'pay_item_add' edit, in one statement.
    Takes the IDR and report uuids, the item (it must carry its "id"), the editor's uuid, the review status the IDR is in, and whether the editor must be that stage's reviewer (False for an admin).
    Returns a one-row list with the edit; an empty list if the IDR left that status, changed reviewer, or the report isn't in it; None on failure. Raises ValueError for an item without an id.
    """
    if not pay_item.get("id"):
        raise ValueError("A pay item needs an id before it is added")

    sql = f"""
        WITH {_target_cte(as_reviewer, status)},
        changed AS (
            UPDATE icid.idr_reports r
            SET report_data = jsonb_set(
                    r.report_data, '{{payItems}}',
                    (CASE WHEN jsonb_typeof(r.report_data->'payItems') = 'array'
                          THEN r.report_data->'payItems' ELSE '[]'::jsonb END) || jsonb_build_array(%s::jsonb)),
                updated_at = now()
            FROM target t
            WHERE r.idr_id = t.idr_id AND r.report_id = %s
            RETURNING r.report_id
        ),
        moved AS (
            UPDATE icid.idrs i
            SET updated_at = now()
            WHERE i.idr_id IN (SELECT idr_id FROM target) AND EXISTS (SELECT 1 FROM changed)
            RETURNING i.*
        ),
        {_log_ctes()}"""
    locked_by = (editor_uuid,) if as_reviewer else ()
    return run_query(sql, (
        idr_id, status, *locked_by, Jsonb(pay_item), report_id,
        *_log_values(report_id, f"payItems[{pay_item['id']}]", "pay_item_add", None, pay_item, editor_uuid, status),
    ))


def log_pay_item_approval(idr_id: UUID, report_id: UUID, field_path: str, quantity_path: list[str], quantity: Any,
                          editor_uuid: UUID, status: str, as_reviewer: bool) -> Optional[list[dict[str, Any]]]:
    """
    Log a reviewer's approval of a pay item as it stands, for an IDR in review, in one statement. Nothing in the report changes: the edit row holds the quantity approved as both its old and its new value.
    Takes the IDR and report uuids, the field_path to record (payItems[<item id>]), where the item's quantity is in report_data (e.g. ['payItems', '2', 'payQuantity']), the quantity the reviewer saw, the editor's uuid, the review status the IDR is in, and whether the editor must be that stage's reviewer (False for an admin).
    Returns a one-row list with the edit; an empty list if the IDR left that status, changed reviewer, the report isn't in it, or the item no longer holds that quantity; None on failure.
    """
    sql = f"""
        WITH {_target_cte(as_reviewer, status)},
        moved AS (
            SELECT t.idr_id
            FROM target t
            WHERE EXISTS (
                SELECT 1 FROM icid.idr_reports r
                WHERE r.idr_id = t.idr_id AND r.report_id = %s AND r.report_data #> %s = %s
            )
        ),
        {_log_ctes()}"""
    locked_by = (editor_uuid,) if as_reviewer else ()
    return run_query(sql, (
        idr_id, status, *locked_by, report_id, quantity_path, Jsonb(quantity),
        *_log_values(report_id, field_path, "pay_item_approve", quantity, quantity, editor_uuid, status),
    ))


def list_field_edits(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List every edit made on an IDR, oldest first, each with its editor's name for the initials.
    Takes the IDR uuid.
    Returns a list of edit dicts with editor_first_name and editor_last_name (empty if the IDR was never edited), or None on failure.
    """
    sql = """
        SELECT
            e.edit_id,
            e.idr_id,
            e.report_id,
            e.field_path,
            e.edit_type,
            e.old_value,
            e.new_value,
            e.editor_uuid,
            e.editor_stage,
            e.edited_at,
            u.first_name AS editor_first_name,
            u.last_name AS editor_last_name
        FROM icid.idr_field_edits e
        JOIN icid.users u ON u.uuid = e.editor_uuid
        WHERE e.idr_id = %s
        ORDER BY e.edited_at, e.edit_id;
    """
    return run_query(sql, (idr_id,))
