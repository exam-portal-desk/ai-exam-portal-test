"""
app/db/users.py
All PostgreSQL queries related to the `users` table.
Uses selective column fetching instead of SELECT *.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, execute_returning, set_clause, insert_returning
from app.utils.datetime_service import now_utc_naive


_AUTH_COLS = "id,username,email,password,full_name,role,profile_photo_key"
_LIST_COLS = "id,username,email,full_name,role,created_at,updated_at"
_PROFILE_COLS = "id,username,email,full_name,role,created_at,last_login,auth_provider,profile_photo_key"


def get_user_by_username(username: str) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_AUTH_COLS} FROM users WHERE username=%s", (username,))
    except Exception as e:
        print(f"[db.users] get_user_by_username error: {e}")
        return None


def get_user_by_email(email: str) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_AUTH_COLS} FROM users WHERE email=%s", (email.lower(),))
    except Exception as e:
        print(f"[db.users] get_user_by_email error: {e}")
        return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM users WHERE id=%s", (user_id,))
    except Exception as e:
        print(f"[db.users] get_user_by_id error: {e}")
        return None


def get_user_profile_by_id(user_id: int) -> Optional[Dict]:
    """Safe, non-sensitive field set for the Profile page — no password,
    no google_id, no other internal identifiers."""
    try:
        return fetch_one(f"SELECT {_PROFILE_COLS} FROM users WHERE id=%s", (user_id,))
    except Exception as e:
        print(f"[db.users] get_user_profile_by_id error: {e}")
        return None


def get_all_users() -> List[Dict]:
    """Returns list-safe columns only — avoids fetching passwords in bulk."""
    try:
        return fetch_all(f"SELECT {_LIST_COLS} FROM users ORDER BY username")
    except Exception as e:
        print(f"[db.users] get_all_users error: {e}")
        return []


def get_users_by_ids(user_ids: List[int]) -> Dict[str, Dict]:
    """
    Batch fetch users by a list of IDs.
    Returns a dict keyed by str(id) for fast lookup. Includes
    profile_photo_key so callers (chat, discussions) can resolve avatars
    without a second round of per-user queries.
    """
    if not user_ids:
        return {}
    try:
        rows = fetch_all("SELECT id,username,full_name,profile_photo_key FROM users WHERE id = ANY(%s)", (list(user_ids),))
        return {str(u["id"]): u for u in rows}
    except Exception as e:
        print(f"[db.users] get_users_by_ids error: {e}")
        return {}


def create_user(user_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("users", user_data)
    except Exception as e:
        print(f"[db.users] create_user error: {e}")
        return None


def update_user(user_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE users SET {sc} WHERE id=%s", params + [user_id])
        return True
    except Exception as e:
        print(f"[db.users] update_user error: {e}")
        return False


def update_last_login(user_id: int) -> Optional[str]:
    """Atomically overwrite last_login with now(), returning the value it
    held immediately before the overwrite (None on a user's first login).
    Used to display "Last Login" as the PREVIOUS login, not the one that
    just happened — a plain UPDATE would lose the old value before any
    caller could read it."""
    try:
        rows = execute_returning(
            """
            WITH old AS (SELECT last_login FROM users WHERE id=%s)
            UPDATE users SET last_login=%s WHERE id=%s
            RETURNING (SELECT last_login FROM old) AS previous_last_login
            """,
            (user_id, now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"), user_id),
        )
        return rows[0]["previous_last_login"] if rows else None
    except Exception as e:
        print(f"[db.users] update_last_login error: {e}")
        return None


def delete_user(user_id: int) -> bool:
    try:
        execute("DELETE FROM users WHERE id=%s", (user_id,))
        return True
    except Exception as e:
        print(f"[db.users] delete_user error: {e}")
        return False


def get_user_by_google_id(google_id: str) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_AUTH_COLS} FROM users WHERE google_id=%s", (google_id,))
    except Exception as e:
        print(f"[db.users] get_user_by_google_id error: {e}")
        return None


def get_notes_view_mode(user_id: int) -> str:
    """Persisted My Notebooks grid/list view preference. Thin wrapper over
    get_view_prefs() — the 'notes' section used to live in its own
    dedicated notes_view_mode column (mirroring the chat_background_*
    convention); consolidated into the generic view_prefs jsonb column by
    migrations/20260826_consolidate_notes_view_mode.sql."""
    return get_view_prefs(user_id).get("notes", "grid")


def get_view_prefs(user_id: int) -> Dict[str, str]:
    """Generic per-section grid/list view preferences (users.view_prefs
    jsonb) — flat {section: mode} shape, one shared column for every
    toggle usage instead of a dedicated column per section."""
    try:
        row = fetch_one("SELECT view_prefs FROM users WHERE id=%s", (user_id,))
        return (row or {}).get("view_prefs") or {}
    except Exception as e:
        print(f"[db.users] get_view_prefs error: {e}")
        return {}


def set_view_pref(user_id: int, section: str, view_mode: str) -> bool:
    try:
        execute(
            "UPDATE users SET view_prefs = jsonb_set(coalesce(view_prefs, '{}'::jsonb), %s, to_jsonb(%s::text)) WHERE id=%s",
            ([section], view_mode, user_id),
        )
        return True
    except Exception as e:
        print(f"[db.users] set_view_pref error: {e}")
        return False


def get_users_count() -> int:
    """Total user count via COUNT query — no data fetch"""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM users")
        return row["count"] if row else 0
    except Exception as e:
        print(f"Error getting users count: {e}")
        return 0


def get_admins_count() -> int:
    """Admin user count via COUNT query"""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM users WHERE role ILIKE %s", ("%admin%",))
        return row["count"] if row else 0
    except Exception as e:
        print(f"Error getting admins count: {e}")
        return 0
