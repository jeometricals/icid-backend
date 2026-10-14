from typing import Any, Optional
from uuid import UUID

from psycopg.errors import ForeignKeyViolation, UniqueViolation

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
            u.signature_set_at,
            u.deleted_at,
            u.deleted_by"""
_AUTH_USER_SELECT = "\n        SELECT" + _AUTH_USER_COLUMNS + "\n        FROM icid.users u\n"
_AUTH_USER_WITH_HASH_SELECT = _AUTH_USER_SELECT.replace("u.is_demo", "u.is_demo,\n            u.password_hash")
# Emails are stored as entered and matched without regard to case, among users who aren't deleted: a deleted user's
# email belongs to no one
_BY_EMAIL = (
    "        WHERE lower(u.email) = %s AND u.deleted_at IS NULL\n        ORDER BY u.created_at, u.uuid\n        LIMIT 1;"
)


def get_all_users() -> Optional[list[dict[str, Any]]]:
    """
    Fetch every user who isn't deleted, with their employing client.
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
        WHERE u.deleted_at IS NULL
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
    Fetch the user who has an email, ignoring case, without their password hash. Deleted users are left out.
    Takes the email.
    Returns the user dict (uuid, email, names, client_id, role, is_demo), or None if no user matches.
    """
    rows = run_query(_AUTH_USER_SELECT + _BY_EMAIL, (email.strip().lower(),))
    return rows[0] if rows else None


def get_user_for_auth(email: str) -> Optional[dict[str, Any]]:
    """
    Fetch the user who has an email, ignoring case, with their password hash. For checking credentials only. Deleted users are left out, so one can't sign in.
    Takes the email.
    Returns the user dict including password_hash, or None if no user who isn't deleted matches.
    """
    rows = run_query(_AUTH_USER_WITH_HASH_SELECT + _BY_EMAIL, (email.strip().lower(),))
    return rows[0] if rows else None


def get_user_by_uuid(uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch a single user by uuid, without their password hash. A deleted user is returned too, with deleted_at set.
    Takes the user's uuid.
    Returns the user dict (uuid, email, names, client_id, role, is_demo, deleted_at), or None if no user matches.
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


def list_users_for_admin(include_deleted: bool = False) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every user, demo users included, without their password hash.
    Takes whether deleted users are listed too.
    Returns the user dicts (as get_user_by_uuid returns them) by last name, first name and email, or None on failure.
    """
    where = "" if include_deleted else "        WHERE u.deleted_at IS NULL\n"
    return run_query(_AUTH_USER_SELECT + where + "        ORDER BY u.last_name, u.first_name, u.email;")


def create_user(
    email: str, first_name: str, last_name: Optional[str], password_hash: str, role: Optional[str], client_id: str
) -> Optional[dict[str, Any]]:
    """
    Add a user who signs in with a password. Never a demo user. Does nothing when a user who isn't deleted has the email, whatever its capitals.
    Takes the email as it is to be stored, the names (last name may be None), the password's hash, the role ('admin' or None) and the client the user sits under.
    Returns the new user dict (as get_user_by_uuid returns it), or None when a user already has that email.
    """
    sql = (
        """
        INSERT INTO icid.users AS u (email, first_name, last_name, client_id, password_hash, role, is_demo)
        VALUES (%s, %s, %s, %s, %s, %s, false)
        ON CONFLICT (lower(email)) WHERE deleted_at IS NULL DO NOTHING
        RETURNING"""
        + _AUTH_USER_COLUMNS
        + ";"
    )
    rows = run_query(sql, (email, first_name, last_name, client_id, password_hash, role))
    return rows[0] if rows else None


def update_user_fields(
    uuid: UUID,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    is_admin: Optional[bool] = None,
    is_demo: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    """
    Change some of a user's first name, last name, email, admin role and demo flag; a value passed as None keeps what the user has. Changes nothing when another user who isn't deleted has the email, whatever its capitals. The demo flag can only ever be turned off: True leaves it as it is.
    Takes the user's uuid and the new values: the names and the email as they are to be stored, is_admin (True makes the role 'admin', False clears it) and is_demo (False makes a demo user a real one).
    Returns the updated user dict (as get_user_by_uuid returns it), or None if no user matches or the email is taken.
    """
    sql = (
        """
        UPDATE icid.users u
        SET first_name = COALESCE(%s, u.first_name),
            last_name = COALESCE(%s, u.last_name),
            email = COALESCE(%s, u.email),
            role = CASE WHEN %s::boolean THEN 'admin' WHEN NOT %s::boolean THEN NULL ELSE u.role END,
            is_demo = u.is_demo AND COALESCE(%s, true),
            updated_at = now()
        WHERE u.uuid = %s
          AND NOT EXISTS (
              SELECT 1
              FROM icid.users other
              WHERE lower(other.email) = lower(%s::text) AND other.uuid <> u.uuid AND other.deleted_at IS NULL
          )
        RETURNING"""
        + _AUTH_USER_COLUMNS
        + ";"
    )
    try:
        rows = run_query(sql, (first_name, last_name, email, is_admin, is_admin, is_demo, uuid, email))
    except UniqueViolation:
        # Another user took the email between the statement's own check and its write
        return None
    return rows[0] if rows else None


def update_user_password(uuid: UUID, password_hash: str) -> Optional[dict[str, Any]]:
    """
    Replace the password a user signs in with.
    Takes the user's uuid and the new password's hash.
    Returns the updated user dict (as get_user_by_uuid returns it, so without the hash), or None if no user matches.
    """
    sql = (
        """
        UPDATE icid.users u
        SET password_hash = %s,
            updated_at = now()
        WHERE u.uuid = %s
        RETURNING"""
        + _AUTH_USER_COLUMNS
        + ";"
    )
    rows = run_query(sql, (password_hash, uuid))
    return rows[0] if rows else None


def soft_delete_user(uuid: UUID, admin_uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Mark a user deleted, with when and by whom. Nothing else of theirs changes: their IDRs, reviews, roles and signature stay.
    Takes the user's uuid and the uuid of the admin deleting them.
    Returns the updated user dict (as get_user_by_uuid returns it), or None if no user matches or they are already deleted.
    """
    sql = (
        """
        UPDATE icid.users u
        SET deleted_at = now(),
            deleted_by = %s,
            updated_at = now()
        WHERE u.uuid = %s AND u.deleted_at IS NULL
        RETURNING"""
        + _AUTH_USER_COLUMNS
        + ";"
    )
    rows = run_query(sql, (admin_uuid, uuid))
    return rows[0] if rows else None


def undelete_user(uuid: UUID) -> Optional[dict[str, Any]]:
    """
    Bring a deleted user back: clear deleted_at and deleted_by. Changes nothing when a user who isn't deleted now has their email, whatever its capitals.
    Takes the user's uuid.
    Returns the updated user dict (as get_user_by_uuid returns it), or None if no user matches, they aren't deleted, or their email is taken.
    """
    sql = (
        """
        UPDATE icid.users u
        SET deleted_at = NULL,
            deleted_by = NULL,
            updated_at = now()
        WHERE u.uuid = %s
          AND u.deleted_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM icid.users other
              WHERE lower(other.email) = lower(u.email) AND other.uuid <> u.uuid AND other.deleted_at IS NULL
          )
        RETURNING"""
        + _AUTH_USER_COLUMNS
        + ";"
    )
    try:
        rows = run_query(sql, (uuid,))
    except UniqueViolation:
        # Another user took the email between the statement's own check and its write
        return None
    return rows[0] if rows else None


def count_user_references(uuid: UUID) -> Optional[dict[str, int]]:
    """
    Count the rows that name a user, each of which stops the user's row being deleted for good.
    Takes the user's uuid.
    Returns {idrs, project_users, audit, field_edits, attachments, properties}: the IDRs they reported, reviewed or deleted, their project roles, the audit rows and field edits they made, the attachments they uploaded and the properties they created or deleted; or None on failure.
    """
    sql = """
        SELECT
            (SELECT count(*) FROM icid.idrs i
             WHERE %s IN (i.reporter_uuid, i.stage1_reviewer_uuid, i.re_reviewer_uuid, i.deleted_by)) AS idrs,
            (SELECT count(*) FROM icid.project_users pu WHERE pu.user_uuid = %s) AS project_users,
            (SELECT count(*) FROM icid.idr_audit a WHERE a.actor_uuid = %s) AS audit,
            (SELECT count(*) FROM icid.idr_field_edits e WHERE e.editor_uuid = %s) AS field_edits,
            (SELECT count(*) FROM icid.report_attachments ra WHERE ra.uploaded_by = %s) AS attachments,
            (SELECT count(*) FROM icid.properties p WHERE %s IN (p.created_by, p.deleted_by)) AS properties;
    """
    rows = run_query(sql, (uuid, uuid, uuid, uuid, uuid, uuid))
    return rows[0] if rows else None


def permanent_delete_user(uuid: UUID) -> bool:
    """
    Delete a soft-deleted user's row for good. A user who isn't soft-deleted is left alone, and so is one that any row still names (each foreign key to users refuses the delete).
    Takes the user's uuid.
    Returns True when the row was deleted, False when there is no such user, they aren't soft-deleted, or something still names them.
    """
    sql = """
        DELETE FROM icid.users u
        WHERE u.uuid = %s AND u.deleted_at IS NOT NULL
        RETURNING u.uuid;
    """
    try:
        return bool(run_query(sql, (uuid,)))
    except ForeignKeyViolation:
        return False


def cleanup_abandoned_demo_users() -> Optional[int]:
    """
    Delete the demo users created more than 24 hours ago, with everything of theirs, by running the database's cleanup function (migration 014).
    Takes nothing.
    Returns how many users it deleted, or None if the function returned nothing.
    """
    rows = run_query("SELECT icid.cleanup_abandoned_demo_users() AS purged_count;")
    return rows[0]["purged_count"] if rows else None
