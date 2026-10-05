from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# What sign-in reads about a user. The password hash is added only by get_user_for_auth.
_AUTH_USER_COLUMNS = """
            u.uuid,
            u.email,
            u.first_name,
            u.last_name,
            u.client_id,
            u.role,
            u.is_demo,
            u.signature_path,
            u.signature_type,
            u.signature_set_at"""
_AUTH_USER_SELECT = "\n        SELECT" + _AUTH_USER_COLUMNS + "\n        FROM icid.users u\n"
_AUTH_USER_WITH_HASH_SELECT = _AUTH_USER_SELECT.replace("u.is_demo", "u.is_demo,\n            u.password_hash")
# Emails are stored as entered and matched without regard to case; the oldest wins if two differ only by case
_BY_EMAIL = "        WHERE lower(u.email) = %s\n        ORDER BY u.created_at, u.uuid\n        LIMIT 1;"


def get_all_users() -> Optional[list[dict[str, Any]]]:
    """
    Fetch every user with their employing client.
    Takes no arguments.
    Returns a list of user dicts ordered by last then first name, or None on failure.
    """
    sql = """
        SELECT
            u.uuid AS user_id,
            u.email,
            u.first_name,
            u.last_name,
            u.phone_number,
            u.client_id AS employer
        FROM icid.users u
        ORDER BY u.last_name, u.first_name;
    """
    return run_query(sql)


def get_user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    """
    Fetch a single user by their uuid.
    Takes the user uuid.
    Returns the user dict, or None if no user matches.
    """
    sql = """
        SELECT
            u.uuid AS user_id,
            u.email,
            u.first_name,
            u.last_name,
            u.phone_number,
            u.client_id AS employer
        FROM icid.users u
        WHERE u.uuid = %s;
    """
    rows = run_query(sql, (user_id,))
    return rows[0] if rows else None


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    """
    Fetch a single user by email, ignoring case, without their password hash.
    Takes the email.
    Returns the user dict (uuid, email, names, client_id, role, is_demo), or None if no user matches.
    """
    rows = run_query(_AUTH_USER_SELECT + _BY_EMAIL, (email.strip().lower(),))
    return rows[0] if rows else None


def get_user_for_auth(email: str) -> Optional[dict[str, Any]]:
    """
    Fetch a single user by email, ignoring case, with their password hash. For checking credentials only.
    Takes the email.
    Returns the user dict including password_hash, or None if no user matches.
    """
    rows = run_query(_AUTH_USER_WITH_HASH_SELECT + _BY_EMAIL, (email.strip().lower(),))
    return rows[0] if rows else None


def get_user_by_uuid(uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single user by uuid, without their password hash.
    Takes the user's uuid.
    Returns the user dict (uuid, email, names, client_id, role, is_demo), or None if no user matches.
    """
    rows = run_query(_AUTH_USER_SELECT + "        WHERE u.uuid = %s;", (uuid,))
    return rows[0] if rows else None


def create_demo_user(
    client_id: str, project_id: str, project_role: str, max_demo_users: int
) -> Optional[dict[str, Any]]:
    """
    Create a throwaway demo user (demo-<uuid>@icid.local, no password, no role) and assign them to the demo project, in one statement: both rows are written or neither.
    Takes the client the user sits under, the demo project's id, the user's role on it, and the most demo users allowed at once.
    Returns the new user dict (uuid, email, names, client_id, role, is_demo), or None when the project doesn't exist or the limit is reached.
    """
    sql = """
        WITH new_user AS (
            INSERT INTO icid.users (uuid, email, first_name, client_id, is_demo)
            SELECT id.uuid, 'demo-' || id.uuid::text || '@icid.local', 'Demo', %s, true
            FROM (SELECT uuid_generate_v4() AS uuid) AS id
            WHERE EXISTS (SELECT 1 FROM icid.projects p WHERE p.project_id = %s)
              AND (SELECT count(*) FROM icid.users d WHERE d.is_demo = true) < %s
            RETURNING uuid, email, first_name, last_name, client_id, role, is_demo
        ),
        assigned AS (
            INSERT INTO icid.project_users (project_id, user_uuid, user_role)
            SELECT %s, new_user.uuid, %s
            FROM new_user
        )
        SELECT uuid, email, first_name, last_name, client_id, role, is_demo
        FROM new_user;
    """
    rows = run_query(sql, (client_id, project_id, max_demo_users, project_id, project_role))
    return rows[0] if rows else None


def delete_demo_user_rows(uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Delete a demo user and everything of theirs, children first, in one statement: their attachments, reports, IDRs, project assignments, then the user. Does nothing for a user who isn't a demo user.
    Takes the user's uuid.
    Returns a one-row list with the deleted user's uuid, or an empty list when no demo user matched.
    """
    sql = """
        WITH demo AS (
            SELECT u.uuid FROM icid.users u WHERE u.uuid = %s AND u.is_demo = true
        ),
        demo_idrs AS (
            SELECT i.idr_id FROM icid.idrs i WHERE i.reporter_uuid IN (SELECT uuid FROM demo)
        ),
        gone_attachments AS (
            DELETE FROM icid.report_attachments a
            WHERE a.uploaded_by IN (SELECT uuid FROM demo)
               OR a.report_id IN (
                   SELECT r.report_id FROM icid.idr_reports r WHERE r.idr_id IN (SELECT idr_id FROM demo_idrs)
               )
        ),
        gone_reports AS (
            DELETE FROM icid.idr_reports r WHERE r.idr_id IN (SELECT idr_id FROM demo_idrs)
        ),
        gone_idrs AS (
            DELETE FROM icid.idrs i WHERE i.idr_id IN (SELECT idr_id FROM demo_idrs)
        ),
        gone_assignments AS (
            DELETE FROM icid.project_users pu WHERE pu.user_uuid IN (SELECT uuid FROM demo)
        ),
        gone_user AS (
            DELETE FROM icid.users u WHERE u.uuid IN (SELECT uuid FROM demo) AND u.is_demo = true
            RETURNING u.uuid
        )
        SELECT uuid FROM gone_user;
    """
    return run_query(sql, (uuid,))


def set_user_signature(uuid: UUID, signature_path: str, signature_type: str) -> Optional[dict[str, Any]]:
    """
    Record a user's current signature: where its file is, how it was made, and now as when it was set. Never a demo user's.
    Takes the user's uuid, the file's object path in the signatures bucket, and 'drawn' or 'uploaded'.
    Returns the updated user dict (as get_user_by_uuid returns it), or None if no such non-demo user exists.
    """
    sql = (
        """
        UPDATE icid.users u
        SET signature_path = %s,
            signature_type = %s,
            signature_set_at = now(),
            updated_at = now()
        WHERE u.uuid = %s AND u.is_demo = false
        RETURNING"""
        + _AUTH_USER_COLUMNS
        + ";"
    )
    rows = run_query(sql, (signature_path, signature_type, uuid))
    return rows[0] if rows else None
