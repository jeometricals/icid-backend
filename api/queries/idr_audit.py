"""
SQL for icid.idr_audit, the log of what is done to an IDR in review.

A row is written by the statement that changes the IDR, never on its own: there are no multi-statement transactions,
so the insert rides along as a data-modifying CTE.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from api.db.runner import run_query


def last_action_time(idr_id: UUID, action: str) -> Optional[datetime]:
    """
    Find when an action was last logged for an IDR, e.g. when it was last accepted at a stage.
    Takes the IDR uuid and the action ('accept_stage1', 'accept_stage2', ...).
    Returns the time of the latest such row, or None when there is none (or the lookup fails).
    """
    sql = """
        SELECT max(created_at) AS at
        FROM icid.idr_audit
        WHERE idr_id = %s AND action = %s;
    """
    rows = run_query(sql, (idr_id, action))
    return rows[0]["at"] if rows else None


# Appended to a statement's WITH list. The statement must define two CTEs before it: target (the IDR before the
# change: idr_id, from_status) and moved (the IDR after it: idr_id, status). Takes three parameters, in this order:
# the actor's uuid, the action, and a note (or None).
AUDIT_CTE = """
        logged AS (
            INSERT INTO icid.idr_audit (idr_id, actor_uuid, action, from_status, to_status, note)
            SELECT m.idr_id, %s, %s, t.from_status, m.status, %s
            FROM moved m
            JOIN target t ON t.idr_id = m.idr_id
        )
"""

# The same for a reviewer's edit of a field, which changes no status: the row's note points at the edit
# ({"edit_id", "field_path"}) rather than repeating its old and new values. The statement must define target and,
# before this, edit (the icid.idr_field_edits row just inserted). Takes one parameter: the action.
EDIT_AUDIT_CTE = """
        logged AS (
            INSERT INTO icid.idr_audit (idr_id, actor_uuid, action, from_status, to_status, note)
            SELECT e.idr_id, e.editor_uuid, %s, t.from_status, t.from_status,
                   jsonb_build_object('edit_id', e.edit_id, 'field_path', e.field_path)::text
            FROM edit e
            JOIN target t ON t.idr_id = e.idr_id
        )
"""
