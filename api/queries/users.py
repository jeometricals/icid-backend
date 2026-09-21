from typing import Any, Optional

from api.db.runner import run_query


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
