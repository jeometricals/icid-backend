"""
SQL for icid.idr_audit, the log of what is done to an IDR in review.

A row is written by the statement that changes the IDR, never on its own: there are no multi-statement transactions,
so the insert rides along as a data-modifying CTE.
"""

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
