from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query

# What sign-in reads about a user. The password hash is added only by get_user_for_auth.
_AUTH_USER_SELECT = """
        SELECT
            u.uuid,
            u.email,
            u.first_name,
            u.last_name,
            u.client_id,
            u.role,
            u.is_demo
        FROM icid.users u
"""
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
